"""Luau v14 expression lifter (decompiler step 1 of step 3).

Folds register chains into expressions and emits linear pseudo-Luau with
R# names and `goto L#` for control flow. No structuring yet (that's next);
proves the IR works first.

Semantics mirror luau/Common/include/Luau/Bytecode.h operand docs and the
CALL/FASTCALL/NAMECALL contracts:
  CALL:     A=func reg, B=nargs+1 (0=MULTRET to top), C=nresults+1 (0=MULTRET)
  NAMECALL: must be followed directly by CALL; R[A]=func, R[A+1]=self copy
  FASTCALL*: inline builtin attempt; args read from the following CALL.
            on success jumps OVER the fallback loader *and* the CALL.
  GETIMPORT: D=const index of IMPORT entry (dotted path)

Usage:
  python ir_v14.py fixtures/v14/trivial.luac [--proto N]
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
import sys
import opcodes_v14 as op
import parser_v14
from cfg_v14 import decode, CFGError
from disassembler_v14 import DisasmError
from disassembler_v14 import fmt_const, fmt_table_template


class IRError(Exception):
    pass


# ---------------------------------------------------------------------------
# Builtin ids (LuauBuiltinFunction order in Bytecode.h) -> dotted Luau name
# ---------------------------------------------------------------------------

_BUILTINS = (
    "none",  # 0 LBF_NONE
    "assert",  # 1
    "math.abs", "math.acos", "math.asin", "math.atan2", "math.atan",
    "math.ceil", "math.cosh", "math.cos", "math.deg", "math.exp",
    "math.floor", "math.fmod", "math.frexp", "math.ldexp", "math.log10",
    "math.log", "math.max", "math.min", "math.modf", "math.pow",
    "math.rad", "math.sinh", "math.sin", "math.sqrt", "math.tanh", "math.tan",
    "bit32.arshift", "bit32.band", "bit32.bnot", "bit32.bor", "bit32.bxor",
    "bit32.btest", "bit32.extract", "bit32.lrotate", "bit32.lshift",
    "bit32.replace", "bit32.rrotate", "bit32.rshift",
    "type",  # 40
    "string.byte", "string.char", "string.len", "typeof", "string.sub",
    "math.clamp", "math.sign", "math.round",
    "rawset", "rawget", "rawequal",
    "table.insert", "table.unpack",
    "vector",
    "bit32.countlz", "bit32.countrz",
    "select", "rawlen", "bit32.extractk",
    "getmetatable", "setmetatable",
    "tonumber", "tostring", "bit32.byteswap",
    "buffer.readi8", "buffer.readu8", "buffer.writeu8",
    "buffer.readi16", "buffer.readu16", "buffer.writeu16",
    "buffer.readi32", "buffer.readu32", "buffer.writeu32",
    "buffer.readf32", "buffer.writef32",
    "buffer.readf64", "buffer.writef64",
    "vector.magnitude", "vector.normalize", "vector.cross", "vector.dot",
    "vector.floor", "vector.ceil", "vector.abs", "vector.sign",
    "vector.clamp", "vector.min", "vector.max",
    "math.lerp", "vector.lerp",
    "math.isnan", "math.isinf", "math.isfinite",
    "integer.create", "integer.tonumber", "integer.neg",
    "integer.add", "integer.sub", "integer.mul", "integer.div",
    "integer.min", "integer.max", "integer.rem", "integer.idiv",
    "integer.udiv", "integer.urem", "integer.mod", "integer.clamp",
    "integer.band", "integer.bor", "integer.bnot", "integer.bxor",
    "integer.lt", "integer.le", "integer.ult", "integer.ule",
    "integer.gt", "integer.ge", "integer.ugt", "integer.uge",
    "integer.lshift", "integer.rshift", "integer.arshift",
    "integer.lrotate", "integer.rrotate",
    "integer.extract", "integer.btest",
    "integer.countrz", "integer.countlz", "integer.bswap",
    "buffer.readinteger", "buffer.writeinteger",
)


def builtin_name(fid: int) -> str:
    if 0 <= fid < len(_BUILTINS):
        return _BUILTINS[fid]
    return f"builtin{fid}"


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

# precedence levels (higher binds tighter)
_P_OR, _P_AND, _P_CMP, _P_CONCAT, _P_ADD, _P_MUL, _P_UN, _P_POW, _P_ATOM = range(1, 10)

_BIN_PREC = {
    "or": _P_OR, "and": _P_AND,
    "==": _P_CMP, "~=": _P_CMP, "<": _P_CMP, "<=": _P_CMP, ">": _P_CMP, ">=": _P_CMP,
    "..": _P_CONCAT,
    "+": _P_ADD, "-": _P_ADD,
    "*": _P_MUL, "/": _P_MUL, "//": _P_MUL, "%": _P_MUL,
    "^": _P_POW,
}
_RIGHT_ASSOC = {"..", "^"}
_UNOPS = {"not": _P_UN, "-": _P_UN, "#": _P_UN}

_MAX_DEPTH = 32


@dataclass
class Expr:
    kind: str
    text: str = ""
    kids: tuple = ()
    depth: int = 1

    def render(self, parent_prec: int = 0) -> str:
        k = self.kind
        if k in ("const", "reg", "upval", "global", "import", "varargs",
                 "top", "closure", "table", "name", "dots"):
            return self.text
        if k == "index":
            o, idx = self.kids
            return f"{_recv(o, o.render(_P_ATOM))}[{idx.render()}]"
        if k == "indexk":
            return f"{_recv(self.kids[0], self.kids[0].render(_P_ATOM))}.{self.text}"
        if k == "indexn":
            return f"{_recv(self.kids[0], self.kids[0].render(_P_ATOM))}[{self.text}]"
        if k == "unop":
            operand = self.kids[0].render(_P_UN)
            s = f"{self.text} {operand}" if self.text == "not" else f"{self.text}{operand}"
            return f"({s})" if _P_UN < parent_prec else s
        if k == "binop":
            prec = _BIN_PREC[self.text]
            l, r = self.kids
            # left side: right-assoc ops need +1 on the left to keep parens right
            lp = prec + (1 if self.text in _RIGHT_ASSOC else 0)
            rp = prec + (0 if self.text in _RIGHT_ASSOC else 1)
            s = f"{l.render(lp)} {self.text} {r.render(rp)}"
            return f"({s})" if prec < parent_prec else s
        if k == "call":
            f, args = self.text, self.kids
            return f"{f}({', '.join(a.render() for a in args)})"
        if k == "method":
            obj, args = self.kids[0], self.kids[1:]
            base = _recv(obj, obj.render(_P_ATOM))
            return f"{base}:{self.text}({', '.join(a.render() for a in args)})"
        if k == "funcval":
            # bare function value used as a statement callee later; render callee
            return self.text
        return self.text or "<?>"

    def __str__(self) -> str:
        return self.render()


def _leaf(kind: str, text: str) -> Expr:
    return Expr(kind, text)


def _node(kind: str, text: str, kids: tuple) -> Expr:
    d = 1 + max((c.depth for c in kids), default=0)
    return Expr(kind, text, kids, d)


def _bin(o: str, l: Expr, r: Expr) -> Expr:
    return _node("binop", o, (l, r))


def _mentions_reg(e: Expr, reg: int) -> bool:
    """Whether expression tree e reads R{reg} (tree walk, not text)."""
    if e.kind == "reg" and e.text == f"R{reg}":
        return True
    return any(_mentions_reg(c, reg) for c in e.kids)


# expression kinds that are not valid method/index receivers without parens
# (literals, table constructors, `...`, stale method values)
_NEEDS_RECV_PARENS = {"const", "table", "dots", "funcval"}


def _recv(e: Expr, rendered: str) -> str:
    if e.kind in _NEEDS_RECV_PARENS:
        return f"({rendered})"
    return rendered


def _un(o: str, v: Expr) -> Expr:
    return _node("unop", o, (v,))


# comparison folded names for JUMPIF* cond rendering
_CMP_OPS = {
    "JUMPIFEQ": "==", "JUMPIFLE": "<=", "JUMPIFLT": "<",
    "JUMPIFNOTEQ": "~=", "JUMPIFNOTLE": ">", "JUMPIFNOTLT": ">=",
}


# ---------------------------------------------------------------------------
# Lifter
# ---------------------------------------------------------------------------

@dataclass
class LiftResult:
    lines: list[str] = field(default_factory=list)
    covered: set[int] = field(default_factory=set)      # pcs with a line
    suppressed: set[int] = field(default_factory=set)   # pcs folded into another line
    unhandled: list[int] = field(default_factory=list)  # pcs with no rule (must stay empty)
    pc_lines: dict[int, list[str]] = field(default_factory=dict)  # pc -> raw lines (no label prefix)


class Lifter:
    def __init__(self, bc: parser_v14.Bytecode, proto: parser_v14.Proto):
        self.bc = bc
        self.proto = proto
        self.insns = decode(proto.code)
        self.env: dict[int, Expr] = {}
        self.res = LiftResult()
        self._multidef = self._find_multidef()
        self._last_ctor: dict[int, int] = {}  # reg -> NEWTABLE pc (for SETLIST merge)
        self._pending_fastcall: dict | None = None   # {id, args, target, pc}
        self._fallback: dict | None = None           # {pc, expr} suppressed loader
        self._pending_namecall: dict | None = None   # {obj, method, dest}
        self._last_multret: dict | None = None       # {text, start, pc, kind}
        # straight-line multret chain: a C==0 CALL / B==0 GETVARARGS leaves
        # its results on top; a following B==0 consumer forwards them. Nest
        # the producer text (`f(g(...))`) instead of emitting `...multret...`.
        # Cleared by joins (labels), clobbers, and single-shot use.
        self._labels: dict[int, int] = {}
        self._build_labels()

    # -- helpers ---------------------------------------------------------
    def _build_labels(self) -> None:
        order: list[int] = []
        for ins in self.insns:
            t = ins.jump_target
            if t is not None and t not in self._labels:
                self._labels[t] = -1
                order.append(t)
        for k, t in enumerate(sorted(order)):
            self._labels[t] = k

    # A-operand is a *source* (not a def) for these; everything else with
    # an A operand defines R[A] (branches excluded conservatively below).
    _SRC_A = {
        "SETTABLE", "SETGLOBAL", "SETUPVAL", "RETURN", "CLOSEUPVALS",
        "JUMP", "JUMPBACK", "JUMPX", "JUMPIF", "JUMPIFNOT",
        "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT", "JUMPIFNOTEQ",
        "JUMPIFNOTLE", "JUMPIFNOTLT", "JUMPXEQKNIL", "JUMPXEQKB",
        "JUMPXEQKN", "JUMPXEQKS", "CMPPROTO",
    }

    def _find_multidef(self) -> set[int]:
        counts: dict[int, int] = {}
        for ins in self.insns:
            # loop ops implicitly redefine their control/output window
            # every iteration; without this the flow-insensitive env folds
            # pre-loop values into loop bodies (e.g. `print(1)` for print(i)).
            # Over-marking is safe (just folds less).
            if ins.opname in ("FORNPREP", "FORNLOOP"):
                for r in range(ins.A, ins.A + 4):
                    counts[r] = counts.get(r, 0) + 1
                continue
            if ins.opname == "FORGLOOP":
                # outputs are R[A+3 .. A+3+N-1] (N = aux low byte); the VM
                # also writes R[A+2] (index copy). Derive the exact window
                # from this insn's own aux instead of a fixed guess.
                n = (ins.aux & 0xFF) if ins.aux else 0
                for r in range(ins.A, ins.A + 3 + max(n, 1)):
                    counts[r] = counts.get(r, 0) + 1
                continue
            if ins.opname in ("FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"):
                for r in range(ins.A, ins.A + 3):
                    counts[r] = counts.get(r, 0) + 1
                continue
            if ins.opname in self._SRC_A:
                continue
            if ins.opname in ("CALL", "CALLFB"):
                n = 8 if ins.C == 0 else max(ins.C - 1, 0)
                for r in range(ins.A, ins.A + max(n, 1)):
                    counts[r] = counts.get(r, 0) + 1
            elif ins.opname == "GETVARARGS" and ins.B == 0:
                for r in range(ins.A, 255):
                    counts[r] = counts.get(r, 0) + 1
            else:
                counts[ins.A] = counts.get(ins.A, 0) + 1
        return {r for r, c in counts.items() if c > 1}

    def _at_label(self, pc: int) -> None:
        """Entering a join point: drop stale folds for reused registers,
        plus every cached expr that reads them. A surviving expr would
        re-render with the MERGED value on a later fold (e.g. `R9 = R8`
        kept across a join that redefines R8); the defining statement
        fixed the old meaning in place, so afterwards only the plain
        register stays valid. Single-def exprs that read nothing
        clobbered here are kept."""
        for r in self._multidef:
            self.env.pop(r, None)
        if self.env:
            multi = self._multidef
            for r in [r for r, old in self.env.items()
                      if any(_mentions_reg(old, d) for d in multi)]:
                self.env.pop(r, None)
        self._last_multret = None  # joins merge tops; chains don't survive

    def _lab(self, pc: int) -> str:
        return f"L{self._labels[pc]}"

    def _const(self, kid: int) -> Expr:
        try:
            return _leaf("const", fmt_const(self.bc, self.proto, kid, full=True))
        except Exception:
            return _leaf("const", f"K{kid}")

    def _import_path(self, kid: int) -> str:
        try:
            return fmt_const(self.bc, self.proto, kid, full=True)
        except Exception:
            return f"K{kid}"

    def _upval(self, u: int) -> Expr:
        names = self.proto.upvals
        if 0 <= u < len(names) and names[u]:
            nm = names[u]
            nm = nm.decode("utf-8", "replace") if isinstance(nm, bytes) else str(nm)
            return _leaf("upval", f"U{u}({nm})")
        return _leaf("upval", f"U{u}")

    def _use(self, reg: int) -> Expr:
        """Flow-insensitive fold, width-bounded: substitute only shallow
        exprs (depth<=2: consts, names, R#.x, -R#, #R#). Deeper trees
        stay as R# so `R2=a+b; R3=R2*c` renders `R3 = R2 * R#C`, not an
        exponentially re-expanded tree. Table leaves never substitute:
        `{}` is a heap identity, re-rendering it per use would imply
        distinct tables."""
        # any read of a pending-ctor table vetoes the SETLIST merge: the
        # read observes the table before the fill in original order
        self._last_ctor.pop(reg, None)
        e = self.env.get(reg)
        if e is not None and e.depth <= 2 and e.kind != "table":
            return e
        return _leaf("reg", f"R{reg}")

    def _clobber(self, reg: int) -> None:
        """R{reg} is rebound: drop its cached expr and every cached expr
        that reads it. Registers are mutable, so a surviving expr would
        re-render with the NEW value on a later fold (e.g. `R4 = R5 .. R6`
        kept across `R5 = print` folds to `print .. ...`). The defining
        statement already fixed the old meaning in place; afterwards only
        the plain register stays valid. (Table-mutation ops like SETTABLE
        do NOT clobber: the register still holds the same table.)"""
        self.env.pop(reg, None)
        for r in [r for r, old in self.env.items() if _mentions_reg(old, reg)]:
            self.env.pop(r, None)
        # any redefinition kills a pending constructor merge...
        # (...except the NEWTABLE site itself, which re-arms below).
        # Dependents keep their own ctor state: their table is unchanged.
        self._last_ctor.pop(reg, None)
        self._chain_invalidate_from(reg)

    def _def(self, reg: int, e: Expr) -> None:
        if e.depth > _MAX_DEPTH:
            e = _leaf("reg", f"R{reg}")
        elif _mentions_reg(e, reg):
            # Self-referential fold (e.g. `R7 = R7 + 2`): the emitted
            # statement already performs the update, so later uses must
            # read the plain register. Re-expanding the defining
            # expression at a later use would apply the update twice.
            e = _leaf("reg", f"R{reg}")
        self._clobber(reg)
        self.env[reg] = e

    def _emit(self, text: str) -> None:
        self.res.lines.append(text)

    def _op(self, ins, text: str) -> None:
        """Emit one lifted line for ins (covers its pc)."""
        prefix = ""
        if ins.pc in self._labels:
            prefix = f"{self._lab(ins.pc)}: "
        self._emit(f"{prefix}{text}")
        self.res.covered.add(ins.pc)
        self.res.pc_lines.setdefault(ins.pc, []).append(text)

    def _cond_str(self, e: Expr) -> str:
        return e.render()

    # -- CALL helpers ----------------------------------------------------
    def _call_args(self, ins, first: int, count: int) -> tuple[list[Expr], bool]:
        """Returns (args, multret_tail). count<0 means MULTRET (B==0)."""
        if count < 0:
            return [self._use(r) for r in range(first, 255) if r in self.env or r < first + 0], True
        return [self._use(r) for r in range(first, first + count)], False

    def _chain_invalidate_from(self, start: int) -> None:
        ch = self._last_multret
        if ch is not None and start >= ch["start"]:
            self._last_multret = None

    def _suppress_pc(self, pc: int) -> None:
        """Blank an already-emitted pc (multret producer nested into its
        consumer: keeps single evaluation). Drops its res.lines entry."""
        lines = self.res.pc_lines.get(pc, [])
        if not lines:
            return
        self.res.pc_lines[pc] = []
        self.res.suppressed.add(pc)
        doomed = set(lines)
        removed: set[str] = set()
        for li in range(len(self.res.lines) - 1, -1, -1):
            l = self.res.lines[li]
            m = re.match(r"^(L\d+: )(.*)$", l)
            body = m.group(2) if m else l
            if body in doomed and body not in removed:
                removed.add(body)
                del self.res.lines[li]
                if len(removed) == len(doomed):
                    break

    def _fmt_call_results(self, ins, call_text: str) -> str:
        """CALL/CALLFB result assignment rendering. Returns full statement."""
        A, B, C = ins.A, ins.B, ins.C
        nargs = B - 1
        if C == 1:
            return call_text  # 0 results: bare call (top untouched)
        if C == 0:
            # multret results from A to top
            self._clobber(A)
            for r in list(self.env):
                if r > A:
                    self._clobber(r)
            # straight-line chain head for a following multret consumer
            self._last_multret = {"text": call_text, "start": A,
                                  "pc": ins.pc, "kind": "call"}
            return f"R{A}..top = {call_text}"
        regs = ", ".join(f"R{r}" for r in range(A, A + C - 1))
        for r in range(A, A + C - 1):
            self._clobber(r)
        # fixed result count resets the stack top: chain broken
        self._last_multret = None
        # record first result as the call expr for folding? Calls have side
        # effects; keep result regs opaque (no fold) to stay sound.
        return f"{regs} = {call_text}"

    def _consume_multret(self, base: int) -> str | None:
        """Nestable multret arg/return span starting at register `base`.
        Returns the arg text (fixed head regs + producer), or None when the
        straight-line chain is broken (caller emits a width marker instead).
        A nested call producer is suppressed (single evaluation); `...`
        producers are pure reads and stay. Single-shot for non-chaining
        consumers (a bare/fixed consumer doesn't extend the chain)."""
        ch = self._last_multret
        if ch is None or ch["start"] < base:
            return None
        head = [self._use(r).render() for r in range(base, ch["start"])]
        parts = head + [ch["text"]]
        if ch["kind"] == "call":
            self._suppress_pc(ch["pc"])
            self._last_multret = None
        return ", ".join(parts)

    def _lift_call(self, ins, idx: int) -> None:
        A, B, C = ins.A, ins.B, ins.C
        nargs = B - 1  # <0 => multret
        # method call via preceding NAMECALL?
        if self._pending_namecall is not None:
            nc = self._pending_namecall
            self._pending_namecall = None
            if nargs < 0:
                nested = self._consume_multret(A + 2)
                obj = nc["obj"]
                if nested is not None:
                    obj_s = _recv(obj, obj.render(_P_ATOM))
                    stmt = self._fmt_call_results(
                        ins, f"{obj_s}:{nc['method']}({nested})")
                    self._op(ins, f"{stmt}  -- {nc['methodname']} method")
                    return
                argstr = f"{', '.join([f'R{r}' for r in range(A + 2, A + 6)])}..., ..." \
                    if False else "R{A+1}..top"
                args = "...multret..."
                arg_exprs = [self._use(r) for r in range(A + 2, min(A + 6, 255))]
                tail = ", ..."
            else:
                arg_exprs = [self._use(r) for r in range(A + 2, A + 1 + nargs)] if nargs > 0 else []
                tail = ""
            obj = nc["obj"]
            m = _node("method", nc["method"], (obj,) + tuple(arg_exprs))
            stmt = self._fmt_call_results(ins, m.render())
            self._op(ins, f"{stmt}{tail}  -- {nc['methodname']} method")
            return
        # fastcall pair?
        if self._pending_fastcall is not None:
            fc = self._pending_fastcall
            self._pending_fastcall = None
            fb = self._fallback
            self._fallback = None
            name = builtin_name(fc["id"])
            arg_exprs: list[Expr] = fc["args"]  # pre-resolved at FASTCALL site
            # plain FASTCALL: args live in the CALL's registers
            if fc["plain"]:
                if nargs < 0:
                    nested = self._consume_multret(A + 1)
                    args_s = nested if nested is not None else f"...multret@{A + 1}..."
                else:
                    arg_exprs = [self._use(r) for r in range(A + 1, A + 1 + nargs)] if nargs > 0 else []
                    args_s = ", ".join(a.render() for a in arg_exprs)
                call_e = f"{name}({args_s})"
            else:
                call_e = f"{name}({', '.join(a.render() for a in arg_exprs)})"
            self._op(ins, self._fmt_call_results(ins, call_e) + "  -- fastcall")
            return
        # plain call
        fu = self._use(A)
        # a stale NAMECALL value is still just the function in R[A];
        # rendering its `X:m` fiction as a callee would be invalid code
        f = f"R{A}" if fu.kind == "funcval" else fu.render(_P_ATOM)
        if nargs < 0:
            nested = self._consume_multret(A + 1)
            if nested is None:
                call_e = f"{f}(...multret@{A + 1}...)"
            else:
                call_e = f"{f}({nested})"
        else:
            arg_exprs = [self._use(r) for r in range(A + 1, A + 1 + nargs)] if nargs > 0 else []
            call_e = f"{f}({', '.join(a.render() for a in arg_exprs)})"
        if self._fallback is not None:
            # stale fallback (fastcall didn't consume it?) flush first
            fb = self._fallback
            self._fallback = None
            self._op_fallback(fb)
        self._op(ins, self._fmt_call_results(ins, call_e))

    def _op_fallback(self, fb: dict) -> None:
        ins = fb["ins"]
        self._chain_invalidate_from(fb.get("reg", 999))
        self._op(ins, f"{fb['text']}  -- fallback")

    # -- main ------------------------------------------------------------
    def lift(self) -> LiftResult:
        if not self.insns:
            raise IRError("empty code")
        insns = self.insns
        i = 0
        while i < len(insns):
            ins = insns[i]
            if ins.pc in self._labels:
                self._at_label(ins.pc)
            n = ins.opname
            A, B, C, D, E, aux = ins.A, ins.B, ins.C, ins.D, ins.E, ins.aux or 0
            t = ins.jump_target
            L = self._lab(t) if t is not None and t in self._labels else (f"@{t}" if t is not None else "?")

            # NEWCLOSURE lookahead: absorb following CAPTUREs into one line
            if n == "NEWCLOSURE":
                caps: list[str] = []
                j = i + 1
                while j < len(insns) and insns[j].opname == "CAPTURE":
                    ci = insns[j]
                    kind = op.CAPTURE_NAMES.get(ci.A, str(ci.A))
                    src = f"U{ci.B}" if ci.A == op.LCT_UPVAL else f"R{ci.B}"
                    caps.append(f"{kind} {src}")
                    j += 1
                cap_s = f" captures({', '.join(caps)})" if caps else ""
                child = D
                dn = ""
                if 0 <= child < len(self.bc.protos):
                    d = self.bc.protos[child].debugname
                    dn = d.decode("utf-8", "replace") if isinstance(d, bytes) else (d or "")
                # expression form must stay parseable: bare proto ref + info comment
                e = _leaf("closure", f"P{child}")
                self._def(A, e)
                self._op(ins, f"R{A} = P{child}  -- function P{child}({dn}){cap_s}")
                for k in range(i + 1, j):
                    self.res.suppressed.add(insns[k].pc)
                i = j
                continue

            if n == "CAPTURE":
                # stray CAPTURE (no NEWCLOSURE immediately before, e.g. after
                # DUPCLOSURE flows): upvalue binding has no Luau statement
                # form, so it vanishes here (suppressed, like PREPVARARGS)
                # instead of leaking as a `-- capture` comment.
                self.res.suppressed.add(ins.pc)
                i += 1
                continue

            # FASTCALL*: record, emit nothing yet
            if n in ("FASTCALL", "FASTCALL1", "FASTCALL2", "FASTCALL2K",
                     "FASTCALL3", "FASTPCALL", "FINCONSTRUCT"):
                fname = builtin_name(A) if n not in ("FASTPCALL", "FINCONSTRUCT") else None
                if n == "FASTCALL":
                    self._pending_fastcall = {"id": A, "args": [], "plain": True, "target": t, "pc": ins.pc}
                elif n == "FASTCALL1":
                    self._pending_fastcall = {"id": A, "args": [self._use(B)], "plain": False, "target": t, "pc": ins.pc}
                elif n == "FASTCALL2":
                    self._pending_fastcall = {"id": A, "args": [self._use(B), self._use(aux & 0xFF)], "plain": False, "target": t, "pc": ins.pc}
                elif n == "FASTCALL2K":
                    self._pending_fastcall = {"id": A, "args": [self._use(B), self._const(aux)], "plain": False, "target": t, "pc": ins.pc}
                elif n == "FASTCALL3":
                    self._pending_fastcall = {"id": A, "args": [self._use(B), self._use(aux & 0xFF), self._use((aux >> 8) & 0xFF)], "plain": False, "target": t, "pc": ins.pc}
                elif n == "FASTPCALL":
                    pname = "pcall" if A == 0 else "xpcall"
                    self._pending_fastcall = {"id": -1, "args": [], "plain": True, "target": t, "pc": ins.pc, "override": pname}
                    # patch: plain path uses override name
                elif n == "FINCONSTRUCT":
                    self._pending_fastcall = {"id": -1, "args": [self._use(A)], "plain": False, "target": t, "pc": ins.pc, "override": "finconstruct"}
                if self._pending_fastcall is not None and "override" in self._pending_fastcall:
                    pass
                # stash override into builtin rendering: handle at CALL
                self.res.suppressed.add(ins.pc)
                # emit a marker line? No — keep output clean; the CALL line
                # carries `-- fastcall`. But labels may sit ON the fastcall pc:
                if ins.pc in self._labels:
                    self._emit(f"{self._lab(ins.pc)}:")
                    self.res.covered.add(ins.pc)
                i += 1
                continue

            # fallback loader between FASTCALL and CALL: suppress, stash
            if self._pending_fastcall is not None and n in ("GETIMPORT", "MOVE", "GETUPVAL"):
                if n == "GETIMPORT":
                    e = _leaf("import", self._import_path(D))
                elif n == "MOVE":
                    e = self._use(B)
                else:
                    e = self._upval(B)
                self._fallback = {"ins": ins, "text": f"R{A} = {e.render()}", "reg": A, "expr": e}
                self.res.suppressed.add(ins.pc)
                if ins.pc in self._labels:
                    self._emit(f"{self._lab(ins.pc)}:")
                    self.res.covered.add(ins.pc)
                i += 1
                continue

            # NAMECALL: record, fold into following CALL
            if n in ("NAMECALL", "NAMECALLUDATA"):
                kid = aux & 0xFFFF
                try:
                    mname = self.bc.strings and None
                    c = self.proto.consts[kid]
                    mname = c.value[2].decode("utf-8", "replace")
                except Exception:
                    mname = f"K{kid}"
                obj = self._use(B)
                self._pending_namecall = {"obj": obj, "method": mname,
                                          "methodname": mname, "dest": A}
                # model: R[A]=func value, R[A+1]=self. R[A] keeps the raw
                # register (rendering `X:m` as a plain value would be invalid
                # code); R[A+1] aliases the object for folding.
                self._clobber(A)
                self._def(A + 1, obj)
                self.res.suppressed.add(ins.pc)
                if ins.pc in self._labels:
                    self._emit(f"{self._lab(ins.pc)}:")
                    self.res.covered.add(ins.pc)
                i += 1
                continue

            # ---- straight-line value ops ----
            if n == "NOP":
                self._op(ins, "-- nop"); i += 1; continue
            if n == "BREAK":
                self._op(ins, "-- break"); i += 1; continue
            if n == "COVERAGE":
                self._op(ins, "-- coverage"); i += 1; continue
            if n == "NATIVECALL":
                self._op(ins, "-- nativecall"); i += 1; continue
            if n == "PREPVARARGS":
                self.res.suppressed.add(ins.pc); i += 1; continue  # hidden like --text
            if n == "LOADNIL":
                e = _leaf("const", "nil"); self._def(A, e); self._op(ins, f"R{A} = nil"); i += 1; continue
            if n == "LOADB":
                e = _leaf("const", "true" if B else "false"); self._def(A, e)
                self._op(ins, f"R{A} = {'true' if B else 'false'}")
                if C:
                    # skip-next: unconditional short jump
                    ll = self._lab(t) if t in self._labels else f"@{t}"
                    self._emit(f"goto {ll}  -- loadb skip")
                i += 1; continue
            if n == "LOADN":
                e = _leaf("const", str(D)); self._def(A, e); self._op(ins, f"R{A} = {D}"); i += 1; continue
            if n == "LOADK":
                e = self._const(D); self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "LOADKX":
                e = self._const(aux); self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "MOVE":
                e = self._use(B); self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "GETGLOBAL":
                try:
                    g = self.proto.consts[aux].value[2].decode("utf-8", "replace")
                except Exception:
                    g = f"K{aux}"
                e = _leaf("global", g); self._def(A, e); self._op(ins, f"R{A} = {g}"); i += 1; continue
            if n == "SETGLOBAL":
                try:
                    g = self.proto.consts[aux].value[2].decode("utf-8", "replace")
                except Exception:
                    g = f"K{aux}"
                self._op(ins, f"{g} = {self._use(A).render()}"); i += 1; continue
            if n == "GETUPVAL":
                e = self._upval(B); self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "SETUPVAL":
                self._op(ins, f"{self._upval(B).render()} = {self._use(A).render()}"); i += 1; continue
            if n == "CLOSEUPVALS":
                # no Luau statement; scope end handles it. Vanishes
                # (suppressed) instead of leaking as a `-- close` comment.
                self.res.suppressed.add(ins.pc)
                i += 1
                continue
            if n == "GETIMPORT":
                e = _leaf("import", self._import_path(D)); self._def(A, e)
                self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "GETTABLE":
                e = _node("index", "", (self._use(B), self._use(C)))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "SETTABLE":
                self._op(ins, f"{self._use(B).render(_P_ATOM)}[{self._use(C).render()}] = {self._use(A).render()}"); i += 1; continue
            if n in ("GETTABLEKS", "GETUDATAKS"):
                try:
                    key = self.proto.consts[aux & 0xFFFF].value[2].decode("utf-8", "replace")
                except Exception:
                    key = f"K{aux}"
                e = _node("indexk", key, (self._use(B),))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n in ("SETTABLEKS", "SETUDATAKS"):
                try:
                    key = self.proto.consts[aux & 0xFFFF].value[2].decode("utf-8", "replace")
                except Exception:
                    key = f"K{aux}"
                self._op(ins, f"{self._use(B).render(_P_ATOM)}.{key} = {self._use(A).render()}"); i += 1; continue
            if n == "GETTABLEN":
                e = _node("indexn", str(C + 1), (self._use(B),))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "SETTABLEN":
                self._op(ins, f"{self._use(B).render(_P_ATOM)}[{C + 1}] = {self._use(A).render()}"); i += 1; continue
            if n in ("ADD", "SUB", "MUL", "DIV", "MOD", "POW", "IDIV"):
                sop = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/",
                       "MOD": "%", "POW": "^", "IDIV": "//"}[n]
                e = _bin(sop, self._use(B), self._use(C))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n in ("ADDK", "SUBK", "MULK", "DIVK", "MODK", "POWK", "IDIVK"):
                sop = {"ADDK": "+", "SUBK": "-", "MULK": "*", "DIVK": "/",
                       "MODK": "%", "POWK": "^", "IDIVK": "//"}[n]
                e = _bin(sop, self._use(B), self._const(C))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n in ("SUBRK", "DIVRK"):
                sop = {"SUBRK": "-", "DIVRK": "/"}[n]
                e = _bin(sop, self._const(B), self._use(C))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n in ("AND", "OR"):
                sop = "and" if n == "AND" else "or"
                e = _bin(sop, self._use(B), self._use(C))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n in ("ANDK", "ORK"):
                sop = "and" if n == "ANDK" else "or"
                e = _bin(sop, self._use(B), self._const(C))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "CONCAT":
                parts = [self._use(r) for r in range(B, C + 1)]
                e = parts[0]
                for p in parts[1:]:
                    e = _bin("..", e, p)
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n in ("NOT", "MINUS", "LENGTH"):
                sop = {"NOT": "not", "MINUS": "-", "LENGTH": "#"}[n]
                e = _un(sop, self._use(B))
                self._def(A, e); self._op(ins, f"R{A} = {e.render()}"); i += 1; continue
            if n == "NEWTABLE":
                arr = aux or 0
                e = _leaf("table", "{}")
                self._def(A, e); self._op(ins, f"R{A} = {{}}  -- array {arr}")
                self._last_ctor[A] = ins.pc
                i += 1; continue
            if n == "DUPTABLE":
                # Emit the template as a real literal (`{a = 1, ...}`), not
                # `{}`: the VM pre-fills template keys (lvmload.cpp), so an
                # empty literal silently changes pairs/next iteration and any
                # read of template keys. Plain-TABLE templates pre-fill 0.
                tmpl = fmt_table_template(self.bc, self.proto, D)
                if tmpl is None:
                    # exotic template (never emitted by the reference
                    # compiler: string keys + scalar/vector values only).
                    # Stay valid; the value loss is documented in LIMITATIONS.
                    tmpl = "{}"
                    suffix = f"  -- dup K{D} (template not representable)"
                else:
                    suffix = f"  -- dup K{D}"
                e = _leaf("table", tmpl)
                self._def(A, e); self._op(ins, f"R{A} = {tmpl}{suffix}"); i += 1; continue
            if n == "DUPCLOSURE":
                cname = self._const(D).render()
                if not cname.strip():
                    # anonymous child proto: the constant renders empty,
                    # which would leak `dupclosure ` (two adjacent
                    # expressions: unparseable). Fall back to an explicit
                    # call on the child proto ref, same fiction level as
                    # NEWCLOSURE's `P#`.
                    try:
                        cname = f"(P{self.proto.consts[D].value[1]})"
                    except Exception:
                        cname = f"(K{D})"
                else:
                    cname = f" {cname}"
                e = _leaf("closure", f"dupclosure{cname}")
                self._def(A, e); self._op(ins, f"R{A} = dupclosure{cname}"); i += 1; continue
            if n == "SETLIST":
                count = C - 1
                if C == 0:
                    # dynamic-count setlist has no valid Luau form; keep the
                    # marker honest (validity gate excludes these protos)
                    self._op(ins, f"R{A}[{aux}..] = ...multret...  -- setlist multret"); i += 1; continue
                regs = [self._use(r).render() for r in range(B, B + count)] if count > 0 else []
                rhs = "{" + ", ".join(regs) + "}"
                # constructor merge: `R{A} = {}` ... `SETLIST @1` folds to
                # `R{A} = {...}` (valid + readable). Safe only when no other
                # def of R{A} intervened (tracked via _last_ctor) and values
                # carry no register reads that the merge would move earlier.
                merged = False
                if (aux == 1 and ins.pc not in self._labels
                        and self._last_ctor.get(A) is not None
                        and not re.search(r"R\d+", rhs)):
                    nt_pc = self._last_ctor[A]
                    # a join between the NEWTABLE and here could skip the
                    # constructor while still reaching the fill: don't merge
                    between = [lp for lp in self._labels if nt_pc < lp < ins.pc]
                    old = self.res.pc_lines.get(nt_pc, [])
                    if (not between and len(old) == 1
                            and re.match(rf"^R{A} = \{{\}}", old[0])):
                        newln = f"R{A} = {rhs}"
                        old[0] = newln
                        for li in range(len(self.res.lines) - 1, -1, -1):
                            l = self.res.lines[li]
                            m = re.match(r"^(L\d+: )(.*)$", l)
                            body = m.group(2) if m else l
                            if re.match(rf"^R{A} = \{{\}}", body):
                                self.res.lines[li] = (m.group(1) if m else "") + newln
                                merged = True
                                break
                    # consume the pending ctor either way: reusing the
                    # rewritten line for a later SETLIST would be wrong
                    self._last_ctor.pop(A, None)
                if merged:
                    self.res.suppressed.add(ins.pc)
                    self.res.covered.add(ins.pc)
                    self.res.pc_lines[ins.pc] = []
                    i += 1
                    continue
                self._op(ins, f"R{A} = table.move({rhs}, 1, {count}, {aux}, R{A})  -- setlist {count}")
                i += 1
                continue
            if n == "GETVARARGS":
                if B == 0:
                    for r in range(A, 255):
                        self._clobber(r)
                    self._last_multret = {"text": "...", "start": A,
                                          "pc": ins.pc, "kind": "dots"}
                    self._op(ins, f"R{A}..top = ..."); i += 1; continue
                regs = ", ".join(f"R{r}" for r in range(A, A + B - 1))
                for r in range(A, A + B - 1):
                    self._def(r, _leaf("dots", "..."))
                self._op(ins, f"{regs} = ..."); i += 1; continue
            if n in ("CALL", "CALLFB"):
                tag = "  -- callfb" if n == "CALLFB" else ""
                # _lift_call emits with its own suffix; append tag there
                before = len(self.res.lines)
                # handle override names (pcall/xpcall/finconstruct)
                if self._pending_fastcall is not None and "override" in self._pending_fastcall:
                    fc = self._pending_fastcall
                    self._pending_fastcall = None
                    fb = self._fallback
                    self._fallback = None
                    A2, B2, C2 = A, B, C
                    nargs2 = B2 - 1
                    if nargs2 < 0:
                        nested = self._consume_multret(A2 + 1)
                        if nested is None:
                            call_e = f"{fc['override']}(...multret@{A2 + 1}...)"
                        else:
                            call_e = f"{fc['override']}({nested})"
                    else:
                        arg_exprs = [self._use(r) for r in range(A2 + 1, A2 + 1 + nargs2)] if nargs2 > 0 else []
                        call_e = f"{fc['override']}({', '.join(a.render() for a in arg_exprs)})"
                    self._op(ins, self._fmt_call_results(ins, call_e) + "  -- fastcall")
                    i += 1
                    continue
                self._lift_call(ins, i)
                if tag:
                    self.res.lines[-1] += tag
                i += 1
                continue
            if n == "RETURN":
                if B == 0:
                    nested = self._consume_multret(A)
                    if nested is None:
                        self._op(ins, f"return R{A}..top")
                    else:
                        self._op(ins, f"return {nested}")
                elif B == 1:
                    self._op(ins, "return")
                else:
                    vals = ", ".join(self._use(r).render() for r in range(A, A + B - 1))
                    self._op(ins, f"return {vals}")
                i += 1
                continue
            if n == "NEWCLASSMEMBER":
                try:
                    key = self.proto.consts[aux].value[2].decode("utf-8", "replace")
                except Exception:
                    key = f"K{aux}"
                self._op(ins, f"R{A}.{key} = {self._use(C).render()}  -- classmember"); i += 1; continue
            if n == "NEWCLASS":
                base = "no_base" if B == 0xFF else self._use(B).render()
                e = _leaf("table", f"class {self._const(aux).render()}")
                self._def(A, e)
                self._op(ins, f"R{A} = class({self._const(aux).render()}, {base})"); i += 1; continue
            if n == "CONSTRUCT":
                self._op(ins, f"R{A} = construct(R{B})  -- slot {aux}"); i += 1; continue

            # ---- branches ----
            if n in ("JUMP", "JUMPBACK", "JUMPX"):
                extra = {"JUMP": "", "JUMPBACK": "  -- loop back", "JUMPX": ""}[n]
                self._op(ins, f"goto {L}{extra}"); i += 1; continue
            if n in ("JUMPIF", "JUMPIFNOT"):
                cond = self._use(A)
                s = self._cond_str(cond) if n == "JUMPIF" else f"not {cond.render(_P_UN)}"
                self._op(ins, f"if {s} then goto {L}"); i += 1; continue
            if n in _CMP_OPS:
                l, r = self._use(A), self._use(aux)
                self._op(ins, f"if {l.render(_P_CMP)} {_CMP_OPS[n]} {r.render(_P_CMP + 1)} then goto {L}")
                i += 1
                continue
            if n == "JUMPXEQKNIL":
                cond = f"{self._use(A).render(_P_CMP)} {'~=' if aux >> 31 else '=='} nil"
                self._op(ins, f"if {cond} then goto {L}"); i += 1; continue
            if n == "JUMPXEQKB":
                cond = f"{self._use(A).render(_P_CMP)} {'~=' if aux >> 31 else '=='} {'true' if aux & 1 else 'false'}"
                self._op(ins, f"if {cond} then goto {L}"); i += 1; continue
            if n in ("JUMPXEQKN", "JUMPXEQKS"):
                k = self._const(aux & 0xFFFFFF)
                sop = "~=" if aux >> 31 else "=="
                self._op(ins, f"if {self._use(A).render(_P_CMP)} {sop} {k.render(_P_CMP + 1)} then goto {L}")
                i += 1
                continue
            if n == "CMPPROTO":
                self._op(ins, f"if proto({self._use(A).render(_P_ATOM)}) ~= P{aux} then goto {L}")
                i += 1
                continue
            if n in ("FORNPREP", "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"):
                self._op(ins, f"goto {L}  -- {n.lower()} R{A}"); i += 1; continue
            if n in ("FORNLOOP", "FORGLOOP"):
                extra = ""
                if n == "FORGLOOP":
                    extra = f"  -- {aux & 0xFF} vars"
                self._op(ins, f"if fornext R{A} then goto {L}{extra}"); i += 1; continue

            self.res.unhandled.append(ins.pc)
            self._op(ins, f"-- UNHANDLED {n}")
            i += 1

        # flush dangling fastcall (no CALL followed — malformed, keep honest)
        if self._pending_fastcall is not None:
            fc = self._pending_fastcall
            self._pending_fastcall = None
            self._emit(f"-- dangling fastcall {builtin_name(fc['id'])} at pc {fc['pc']}")
        if self._fallback is not None:
            fb = self._fallback
            self._fallback = None
            self._op_fallback(fb)
        return self.res


def lift_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> LiftResult:
    return Lifter(bc, proto).lift()


def lift_all(bc: parser_v14.Bytecode) -> dict[int, LiftResult]:
    return {p.id: lift_proto(bc, p) for p in bc.protos}


def render_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto, res: LiftResult) -> str:
    dn = proto.debugname
    label = dn.decode("utf-8", "replace") if isinstance(dn, bytes) else (dn or "??")
    head = [f"-- function P{proto.id} ({label}): params={proto.numparams} "
            f"vararg={proto.is_vararg} maxstack={proto.maxstack}"]
    return "\n".join(head + res.lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--proto")]
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--proto"):
            only = int(a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1])
    files = args or ["fixtures/v14/trivial.luac"]
    bad = False
    for f in files:
        try:
            data = open(f, "rb").read()
            bc = parser_v14.parse(data)
            for p in bc.protos:
                if only is not None and p.id != only:
                    continue
                res = lift_proto(bc, p)
                print(f"== {f} Function {p.id} ==")
                print(render_proto(bc, p, res))
                print()
                if res.unhandled:
                    bad = True
                    print(f"UNHANDLED in {f} proto {p.id}: {res.unhandled}")
        except (parser_v14.ParseError, IRError, DisasmError, CFGError) as e:
            bad = True
            print(f"FAIL {f}: {type(e).__name__}: {e}")
    raise SystemExit(1 if bad else 0)
