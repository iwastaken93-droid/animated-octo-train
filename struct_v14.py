"""Luau v14 control-flow structuring (decompiler step 3 of step 3).

Cons�umes cfg_v14 blocks + ir_v14 lifted lines and rebuilds structured
pseudo-Luau: if/else (post-dominator joins), numeric for (FORNPREP/FORNLOOP),
generic for (FORGPREP*/FORGLOOP), while / repeat-until (JUMPBACK / cond
backedges), break/continue, with goto+label fallback for anything
irreducible. Straight-line statements come from ir_v14 pc_lines, so folds
(constants, imports, fastcalls, method calls) carry over unchanged.

Semantics notes (verified against luau/VM/src/lvmexecute.cpp + Bytecode.h):
  LOADB always skips (pc += C unconditionally) -> unconditional jump here,
    NOT the two-successor edge BytecodeUtils.isFallthrough() suggests.
  FASTCALL success/failure reconverge with identical values -> transparent
    (structure follows the fallthrough slow path only).
  NAMECALL/FASTCALL fallback loaders are already suppressed in pc_lines.

Usage:
  python struct_v14.py fixtures/v14/loops.luac [--proto N]
"""
from __future__ import annotations
from dataclasses import dataclass, field
import sys
import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError, fmt_const
from ir_v14 import Lifter, IRError


class StructError(Exception):
    pass


FASTCALL_OPS = {"FASTCALL", "FASTCALL1", "FASTCALL2", "FASTCALL2K",
                "FASTCALL3", "FASTPCALL", "FINCONSTRUCT"}

# true conditional branches (jump taken iff cond holds)
COND_OPS = {
    "JUMPIF", "JUMPIFNOT",
    "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT",
    "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT",
    "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS",
    "CMPPROTO",
}

_CMP_OPS = {
    "JUMPIFEQ": "==", "JUMPIFLE": "<=", "JUMPIFLT": "<",
    "JUMPIFNOTEQ": "~=", "JUMPIFNOTLE": ">", "JUMPIFNOTLT": ">=",
}
_NEG = {"==": "~=", "~=": "==", "<": ">=", "<=": ">", ">": "<=", ">=": "<",
        "truthy": "falsy", "falsy": "truthy"}


@dataclass
class Cond:
    op: str  # truthy/falsy/cmp/proto-ne
    l: str
    r: str = ""

    def negate(self) -> "Cond":
        return Cond(_NEG[self.op], self.l, self.r)

    def render(self) -> str:
        if self.op == "truthy":
            return self.l
        if self.op == "falsy":
            return f"not {self.l}"
        if self.op == "proto-ne":
            return f"proto({self.l}) ~= {self.r}"
        return f"{self.l} {self.op} {self.r}"


def taken_cond(ins: Insn, bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> Cond:
    """Condition under which a conditional branch takes its jump target."""
    n, A, aux = ins.opname, ins.A, ins.aux or 0
    R = lambda r: f"R{r}"
    if n == "JUMPIF":
        return Cond("truthy", R(A))
    if n == "JUMPIFNOT":
        return Cond("falsy", R(A))
    if n in _CMP_OPS:
        return Cond(_CMP_OPS[n], R(A), R(aux))
    if n == "JUMPXEQKNIL":
        return Cond("~=" if aux >> 31 else "==", R(A), "nil")
    if n == "JUMPXEQKB":
        return Cond("~=" if aux >> 31 else "==", R(A), "true" if aux & 1 else "false")
    if n in ("JUMPXEQKN", "JUMPXEQKS"):
        try:
            k = fmt_const(bc, proto, aux & 0xFFFFFF)
        except Exception:
            k = f"K{aux & 0xFFFFFF}"
        return Cond("~=" if aux >> 31 else "==", R(A), k)
    if n == "CMPPROTO":
        return Cond("proto-ne", R(A), f"P{aux}")
    raise StructError(f"not a condition op: {n} at pc {ins.pc}")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

@dataclass
class Node:
    kind: str  # stmt,label,goto,if,while,repeat,numfor,genfor,break,continue,return
    text: str = ""
    cond: Cond | None = None
    then_: list = field(default_factory=list)    # if-then / loop body
    else_: list = field(default_factory=list)    # if-else
    header: str = ""  # for/while header text

    def render(self, ind: int = 0) -> list[str]:
        p = "  " * ind
        if self.kind in ("stmt", "label", "goto", "break", "continue", "return"):
            return [p + self.text]
        if self.kind == "if":
            out = [p + f"if {self.cond.render()} then"]
            out += render_all(self.then_, ind + 1)
            if self.else_:
                out.append(p + "else")
                out += render_all(self.else_, ind + 1)
            out.append(p + "end")
            return out
        if self.kind == "while":
            out = [p + f"while {self.cond.render()} do"]
            out += render_all(self.then_, ind + 1)
            out.append(p + "end")
            return out
        if self.kind == "repeat":
            out = [p + "repeat"]
            out += render_all(self.then_, ind + 1)
            out.append(p + f"until {self.cond.render()}")
            return out
        if self.kind in ("numfor", "genfor"):
            out = [p + self.header]
            out += render_all(self.then_, ind + 1)
            out.append(p + "end")
            return out
        return [p + f"-- ?{self.kind} {self.text}"]


def render_all(nodes: list[Node], ind: int = 0) -> list[str]:
    out: list[str] = []
    for x in nodes:
        out += x.render(ind)
    return out


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------

@dataclass
class Loop:
    kind: str  # numfor, genfor, while, whiletrue, repeat
    header: int
    latch: int  # primary latch (for continue-target/display)
    latches: frozenset = frozenset()  # all backedge sources (fused continues)
    body: set[int] = field(default_factory=set)
    prep: int | None = None
    exit: int | None = None
    body_entry: int = -1

    def __post_init__(self) -> None:
        if not self.latches:
            self.latches = frozenset({self.latch})


@dataclass
class StructResult:
    nodes: list[Node] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    unclassified: list[tuple[int, int]] = field(default_factory=list)  # (latch, header)
    stats: dict = field(default_factory=dict)
    edges: list[tuple[int, int, str]] = field(default_factory=list)  # (src, dst, kind)
    done: set[int] = field(default_factory=set)
    skipped: list[int] = field(default_factory=list)


class Structurer:
    def __init__(self, bc: parser_v14.Bytecode, proto: parser_v14.Proto):
        self.bc = bc
        self.proto = proto
        self.cfg = build_cfg(proto.code)
        if not self.cfg.insns:
            raise StructError("empty code")
        res = Lifter(bc, proto).lift()
        if res.unhandled:
            raise StructError(f"unhandled insns: {res.unhandled}")
        self.pclines = res.pc_lines
        n = len(self.cfg.blocks)
        self.pc2b: dict[int, int] = {}
        for b in self.cfg.blocks:
            for ins in b.insns:
                self.pc2b[ins.pc] = b.id
        self.succ: dict[int, list[int]] = {}
        for b in self.cfg.blocks:
            self.succ[b.id] = self._struct_succ(b)
        self.pred: dict[int, list[int]] = {b.id: [] for b in self.cfg.blocks}
        for b in self.cfg.blocks:
            for t in self.succ[b.id]:
                self.pred[t].append(b.id)
        # reachability on the struct graph
        self.reachable: set[int] = set()
        stack = [self.cfg.entry]
        while stack:
            x = stack.pop()
            if x in self.reachable:
                continue
            self.reachable.add(x)
            stack += self.succ[x]
        # RPO ranks (struct graph)
        self.rank: dict[int, int] = {}
        seen: set[int] = set()
        order: list[int] = []

        def visit(x: int) -> None:
            seen.add(x)
            for t in self.succ[x]:
                if t not in seen:
                    visit(t)
            order.append(x)

        sys.setrecursionlimit(max(10000, 4 * n + 100))
        visit(self.cfg.entry)
        order.reverse()
        self.rank = {b: i for i, b in enumerate(order)}
        self.res = StructResult(skipped=sorted(b.id for b in self.cfg.blocks if b.id not in self.reachable))
        self._find_loops()
        self._postdoms()
        self._labels: dict[int, str] = {}
        self._emitted_loops: set[int] = set()  # id(loop)
        self._gotos: set[int] = set()
        self._latch_stack: list[frozenset] = []
        self._anchors: dict[int, tuple[list, int]] = {}  # block -> (node list, index)

    # -- struct graph ----------------------------------------------------
    def _block_of_pc(self, pc: int) -> int | None:
        return self.pc2b.get(pc)

    def _fallthrough(self, b: Block) -> int | None:
        idx = next(i for i, ins in enumerate(self.cfg.insns) if ins.pc == b.insns[-1].pc)
        if idx + 1 < len(self.cfg.insns):
            return self._block_of_pc(self.cfg.insns[idx + 1].pc)
        return None

    def _struct_succ(self, b: Block) -> list[int]:
        last = b.last
        if last is None or last.is_return:
            return []
        if last.opname == "LOADB" and last.C != 0:
            t = self._block_of_pc(last.jump_target)  # type: ignore
            return [t] if t is not None else []
        if last.opname in FASTCALL_OPS:
            f = self._fallthrough(b)
            return [f] if f is not None else []
        return [t for t in b.successors if t is not None]

    # -- loop detection --------------------------------------------------
    def _body_of(self, latch: int, header: int) -> set[int]:
        """Natural loop: header + reverse-reachable from latch without passing header."""
        if latch == header:
            return {header}
        body = {header, latch}
        stack = [latch]
        while stack:
            x = stack.pop()
            for p in self.pred[x]:
                if p != header and p not in body:
                    body.add(p)
                    stack.append(p)
        return body

    def _idom(self) -> dict[int, int | None]:
        """Cooper-Harvey-Kennedy immediate dominators on the struct graph."""
        rpo = sorted(self.reachable, key=lambda b: self.rank.get(b, 10**9))
        idx = {b: i for i, b in enumerate(rpo)}
        idom: dict[int, int | None] = {b: None for b in self.reachable}
        idom[self.cfg.entry] = self.cfg.entry
        changed = True
        while changed:
            changed = False
            for b in rpo[1:]:
                preds = [p for p in self.pred[b] if idom[p] is not None]
                if not preds:
                    continue
                new = preds[0]
                for p in preds[1:]:
                    f1, f2 = p, new
                    while f1 != f2:
                        while idx.get(f1, -1) > idx.get(f2, -1):
                            f1 = idom[f1]  # type: ignore
                        while idx.get(f2, -1) > idx.get(f1, -1):
                            f2 = idom[f2]  # type: ignore
                    new = f1
                if idom[b] != new:
                    idom[b] = new
                    changed = True
        return idom

    def _dominates(self, idom: dict[int, int | None], a: int, b: int) -> bool:
        x: int | None = b
        while x is not None:
            if x == a:
                return True
            if x == idom.get(x):
                break
            x = idom.get(x)
        return False

    def _find_loops(self) -> None:
        idom = self._idom()
        edges = []
        for b in self.cfg.blocks:
            if b.id not in self.reachable:
                continue
            for t in self.succ[b.id]:
                if t == b.id or self._dominates(idom, t, b.id):
                    edges.append((b.id, t))
        # group backedges by header; body = union of natural loops
        by_header: dict[int, list[int]] = {}
        for latch, header in edges:
            by_header.setdefault(header, []).append(latch)
        groups: list[tuple[int, list[int], set[int]]] = []
        for header, latches in by_header.items():
            body = {header}
            for l in latches:
                body |= self._body_of(l, header)
            groups.append((header, latches, body))
        # innermost (smallest body) first so nested loops consume latches
        groups.sort(key=lambda g: len(g[2]))
        used_latch: set[int] = set()
        for header, latches, body in groups:
            latches = [l for l in latches if l not in used_latch]
            if not latches:
                continue
            loop = self._classify(header, latches, body)
            if loop is None:
                for l in latches:
                    self.res.unclassified.append((l, header))
                continue
            # single-entry check with representatives: entries arriving via the
            # same already-candidate loop body count as one (sequential loops).
            reps: set[int] = set()
            for p in self.pred[header]:
                if p not in body and p in self.reachable:
                    reps.add(self._entry_rep(p, body, groups, header))
            if len(reps) > 1:
                for l in latches:
                    self.res.unclassified.append((l, header))
                continue
            for l in loop.latches:
                used_latch.add(l)
            self.res.loops.append(loop)
        self.loop_by_header = {L.header: L for L in self.res.loops}
        self.loop_by_prep = {L.prep: L for L in self.res.loops if L.prep is not None}
        self.latch_loop = {l: L for L in self.res.loops for l in L.latches}

    def _entry_rep(self, p: int, body: set[int], groups: list, header: int) -> int:
        """Representative of an outside predecessor: innermost candidate body
        containing it (sequential loop composition), else itself."""
        best: int | None = None
        best_sz = None
        for h2, _, b2 in groups:
            if h2 != header and p in b2:
                if best_sz is None or len(b2) < best_sz:
                    best, best_sz = h2, len(b2)
        return best if best is not None else p

    def _flow_to(self, b: int, target: int) -> bool:
        """Block b flows unconditionally to target (single succ, no jump insn)."""
        B = {x.id: x for x in self.cfg.blocks}
        if self.succ[b] != [target]:
            return False
        last = B[b].last
        return last is not None and last.opname not in COND_OPS | {
            "JUMP", "JUMPBACK", "JUMPX", "RETURN",
            "FORNPREP", "FORNLOOP", "FORGLOOP",
            "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"} and not (
                last.opname == "LOADB" and last.C != 0)

    def _jumps_to(self, b: int, target: int) -> bool:
        """Block b ends with an unconditional jump to target (LOADB-skip included)."""
        B = {x.id: x for x in self.cfg.blocks}
        last = B[b].last
        if last is None:
            return False
        if last.opname in ("JUMP", "JUMPBACK", "JUMPX"):
            return self.succ[b] == [target]
        if last.opname == "LOADB" and last.C != 0:
            return self.succ[b] == [target]
        return False

    def _cond_targets_header(self, b: int, header: int) -> bool:
        B = {x.id: x for x in self.cfg.blocks}
        last = B[b].last
        return (last is not None and last.opname in COND_OPS
                and last.jump_target is not None
                and self._block_of_pc(last.jump_target) == header)

    def _outside_exits(self, body: set[int]) -> set[int]:
        out: set[int] = set()
        for x in body:
            for t in self.succ[x]:
                if t not in body:
                    out.add(t)
        return out

    def _classify(self, header: int, latches: list[int], body: set[int]) -> Loop | None:
        """Group classification. latches = backedge sources unconsumed, header-dominated."""
        B = {b.id: b for b in self.cfg.blocks}
        hop = B[header].last.opname if B[header].last else ""
        hs = self.succ[header]
        lat = latches[0] if len(latches) == 1 else -1

        def back_to_header(l: int) -> bool:
            return header in self.succ[l]

        # ---- test-at-top: header carries the test ----
        if hop == "FORGLOOP" and len(hs) == 2 and all(back_to_header(l) for l in latches):
            F, E = hs[0], hs[1]
            if F not in body or E in body:
                return None
            P = self._genfor_prep(header)
            if P is None:
                return None
            return Loop("genfor", header, lat if lat >= 0 else latches[0],
                        frozenset(latches), body, P, E, F)
        if hop in COND_OPS and len(hs) == 2 and all(back_to_header(l) for l in latches):
            E, F = hs[0], hs[1]
            if E not in body and F in body:
                return Loop("while", header, lat if lat >= 0 else latches[0],
                            frozenset(latches), body, None, E, F)
            if E in body and F in body:
                outs = self._outside_exits(body)
                if len(outs) != 1:
                    return None
                return Loop("whiletrue", header, lat if lat >= 0 else latches[0],
                            frozenset(latches), body, None, outs.pop(), F)
            return None
        if hop == "JUMP" and len(hs) == 1 and hs[0] in body                 and all(back_to_header(l) for l in latches):
            outs = self._outside_exits(body)
            if len(outs) != 1:
                return None
            return Loop("whiletrue", header, lat if lat >= 0 else latches[0],
                        frozenset(latches), body, None, outs.pop(), hs[0])

        if len(latches) != 1:
            return None
        latch = latches[0]
        lop = B[latch].last.opname if B[latch].last else ""
        outs = [t for t in self.succ[latch] if t != header]
        # ---- fused self loops ----
        if latch == header:
            if lop == "FORNLOOP":
                if len(outs) != 1 or outs[0] in body:
                    return None
                P = self._numfor_prep(outs[0], header, body)
                return Loop("numfor", header, latch, frozenset({latch}), body,
                            P, outs[0], header) if P is not None else None
            if lop == "FORGLOOP":
                if len(outs) != 1 or outs[0] in body:
                    return None
                P = self._genfor_prep(header)
                return Loop("genfor", header, latch, frozenset({latch}), body,
                            P, outs[0], header) if P is not None else None
            if lop == "JUMPBACK":
                outside = [t for t in self.succ[latch] if t not in body]
                E = outside[0] if len(outside) == 1 else None
                return Loop("whiletrue", header, latch, frozenset({latch}), body,
                            None, E, header) if E is not None else None
            if self._cond_targets_header(latch, header):
                if len(outs) != 1 or outs[0] in body:
                    return None
                return Loop("repeat", header, latch, frozenset({latch}), body,
                            None, outs[0], header)
            return None
        # ---- bottom-test loops ----
        if lop == "FORNLOOP":
            if len(outs) != 1 or outs[0] in body:
                return None
            P = self._numfor_prep(outs[0], header, body)
            if P is None:
                return None
            entry = self._fallthrough(B[P])
            if entry is None or (entry != header and entry not in body):
                return None
            return Loop("numfor", header, latch, frozenset({latch}), body, P, outs[0], entry)
        if lop == "FORGLOOP":
            if len(outs) != 1 or outs[0] in body:
                return None
            if B[latch].last.jump_target is None or                     self._block_of_pc(B[latch].last.jump_target) != header:
                return None
            P = self._genfor_prep(latch)
            return Loop("genfor", header, latch, frozenset({latch}), body,
                        P, outs[0], header) if P is not None else None
        if lop == "JUMPBACK":
            return None  # handled by header-cond rules above
        if self._cond_targets_header(latch, header):
            B2 = {b.id: b for b in self.cfg.blocks}
            hlast = B2[header].last
            if hlast is not None and hlast.opname in COND_OPS:
                return None  # header has its own test; header rules decide
            if len(outs) != 1 or outs[0] in body:
                return None
            return Loop("repeat", header, latch, frozenset({latch}), body,
                        None, outs[0], header)
        return None

    def _numfor_prep(self, exit_b: int, header: int, body: set[int]) -> int | None:
        preps = [b.id for b in self.cfg.blocks
                 if b.id in self.reachable and b.last is not None
                 and b.last.opname == "FORNPREP"
                 and b.last.jump_target is not None
                 and self._block_of_pc(b.last.jump_target) == exit_b]
        return preps[0] if len(preps) == 1 else None

    def _genfor_prep(self, test_b: int) -> int | None:
        preps = [b.id for b in self.cfg.blocks
                 if b.id in self.reachable and b.last is not None
                 and b.last.opname in ("FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT")
                 and b.last.jump_target is not None
                 and self._block_of_pc(b.last.jump_target) == test_b]
        return preps[0] if len(preps) == 1 else None
        if lop == "JUMPBACK":
            if header == latch:
                outs_all = [t for t in self.succ[header] if t != header]
                # while-true: exits are breaks; need exactly one outside exit to continue
                outside = [t for t in self.succ[latch] if t not in body]
                E = outside[0] if len(outside) == 1 else None
                if E is None:
                    return None
                return Loop("whiletrue", header, latch, body, None, E, header)
            hop = B[header].last.opname if B[header].last else ""
            if hop not in COND_OPS:
                return None
            hs = self.succ[header]
            if len(hs) != 2:
                return None
            # loop continues via fallthrough; jump target is the exit
            E = hs[0]  # target first per _successor_pcs order
            F = hs[1]
            if E in body or F not in body:
                return None
            return Loop("while", header, latch, body, None, E, F)
        if lop in COND_OPS:
            ins = B[latch].last
            assert ins is not None
            if ins.jump_target is None or self._block_of_pc(ins.jump_target) != header:
                return None
            if len(outs) != 1:
                return None
            E = outs[0]
            if E in body:
                return None
            return Loop("repeat", header, latch, body, None, E, header)
        return None

    # -- post dominators -------------------------------------------------
    def _postdoms(self) -> None:
        reach = [b for b in self.reachable]
        exits = [b for b in reach if not self.succ[b]]
        if not exits:
            self.ipdom = {}
            return
        pdom: dict[int, set[int]] = {b: set(reach) for b in reach}
        for e in exits:
            pdom[e] = {e}
        changed = True
        while changed:
            changed = False
            for b in reach:
                if b in exits:
                    continue
                s = self.succ[b]
                if not s:
                    continue
                ni = set(pdom[s[0]])
                for t in s[1:]:
                    ni &= pdom[t]
                ni.add(b)
                if ni != pdom[b]:
                    pdom[b] = ni
                    changed = True
        self.ipdom = {}
        for b in reach:
            strict = pdom[b] - {b}
            if strict:
                self.ipdom[b] = max(strict, key=lambda c: len(pdom[c]))

    # -- helpers ---------------------------------------------------------
    SKIP_OPS = COND_OPS | {"JUMP", "JUMPBACK", "JUMPX", "RETURN",
                           "FORNPREP", "FORNLOOP", "FORGLOOP",
                           "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"}

    def _stmts(self, b: Block, skip_last: bool = True) -> list[Node]:
        out: list[Node] = []
        for i, ins in enumerate(b.insns):
            last = (i == len(b.insns) - 1)
            if last and skip_last and ins.opname in self.SKIP_OPS:
                if ins.opname == "RETURN":
                    for ln in self.pclines.get(ins.pc, ["return"]):
                        out.append(Node("return", ln))
                continue
            out.extend(Node("stmt", ln) for ln in self.pclines.get(ins.pc, []))
        return out

    def _label_of(self, b: int) -> str:
        if b not in self._labels:
            self._labels[b] = f"L{len(self._labels)}"
        return self._labels[b]

    def _goto(self, dst: int) -> Node:
        self._gotos.add(dst)
        return Node("goto", f"goto {self._label_of(dst)}")

    # -- main build ------------------------------------------------------
    def structure(self) -> StructResult:
        self._stops: dict[int, str] = {}
        self._nodes = self._build(self.cfg.entry, {})
        # sweep any reachable-but-unstructured blocks (forward-goto targets).
        # NOTE: += copies elements, so repoint anchors from the temp list.
        for b in sorted(self.reachable, key=lambda x: self.cfg.blocks[x].start_pc):
            if b not in self.res.done:
                base = len(self._nodes)
                sub = self._build(b, {})
                for blk, (lst, pos) in list(self._anchors.items()):
                    if lst is sub:
                        self._anchors[blk] = (self._nodes, base + pos)
                self._nodes += sub
        # post-pass: insert labels at anchored positions (descending per list)
        by_list: dict[int, list[tuple[int, str]]] = {}
        for dst in self._gotos:
            if dst in self._anchors:
                lst, pos = self._anchors[dst]
                by_list.setdefault(id(lst), (lst, []))[1].append((pos, self._label_of(dst)))
        for lst, items in by_list.values():
            for pos, lab in sorted(items, reverse=True):
                lst.insert(pos, Node("label", f"{lab}:"))
        self.res.nodes = self._nodes
        kinds: dict[str, int] = {}
        for L in self.res.loops:
            kinds[L.kind] = kinds.get(L.kind, 0) + 1
        edgekinds: dict[str, int] = {}
        for _, _, k in self.res.edges:
            edgekinds[k] = edgekinds.get(k, 0) + 1
        self.res.stats = {
            "loops": kinds, "edges": edgekinds,
            "unclassified": len(self.res.unclassified),
            "gotos": len(self._gotos),
            "done": len(self.res.done), "reachable": len(self.reachable),
            "skipped": len(self.res.skipped),
        }
        return self.res

    def _edge(self, src: int, dst: int, kind: str) -> None:
        self.res.edges.append((src, dst, kind))

    def _build(self, entry: int | None, stops: dict[int, str], latches: frozenset = frozenset(),
               confine: set[int] | None = None) -> list[Node]:
        out: list[Node] = []
        cur = entry
        prev: int | None = None
        first = True
        while cur is not None:
            B = {b.id: b for b in self.cfg.blocks}
            # latch of the innermost active loop: absorb terminator (backedge),
            # emit leading stmts. A jump to an OUTER latch is a continue.
            if cur in latches and self._absorbable(cur):
                if cur in self.res.done:
                    out.append(Node("continue", "continue"))
                    if prev is not None:
                        self._edge(prev, cur, "continue-latch")
                    break
                b = B[cur]
                self._anchors.setdefault(cur, (out, len(out)))
                self.res.done.add(cur)
                out += self._stmts(b)
                if prev is not None:
                    self._edge(prev, cur, "seq-in")
                break
            if any(cur in s for s in self._latch_stack):
                out.append(Node("continue", "continue"))
                if prev is not None:
                    self._edge(prev, cur, "continue-outer")
                break
            if cur not in self.reachable:
                out.append(self._goto(cur))
                self._edge(prev, cur, "goto-unreach") if prev is not None else None
                break
            act = stops.get(cur)
            if first and cur == entry and act == "continue":
                # loop-body entry coincides with header: first visit flows in
                act = None
            first = False
            if act in ("break", "continue"):
                out.append(Node(act, act))
                if prev is not None:
                    self._edge(prev, cur, act)
                break
            if act == "fallthrough":
                if prev is not None:
                    self._edge(prev, cur, "merge-in")
                break
            if act == "goto":
                out.append(self._goto(cur))
                if prev is not None:
                    self._edge(prev, cur, "goto")
                break
            if confine is not None and cur not in confine and cur not in stops:
                out.append(self._goto(cur))
                if prev is not None:
                    self._edge(prev, cur, "goto-confine")
                break
            if cur in self.res.done:
                out.append(self._goto(cur))
                if prev is not None:
                    self._edge(prev, cur, "goto-join")
                break
            b = B[cur]
            # loop triggers (first visit only; done-check above guards re-entry)
            L = self.loop_by_prep.get(cur)
            if L is not None and id(L) not in self._emitted_loops:
                self._anchors.setdefault(cur, (out, len(out)))
                out += self._emit_loop(L, b)
                cur = self._after_loop(L)
                prev = L.latch
                continue
            L = self.loop_by_header.get(cur)
            if L is not None and L.prep is None and id(L) not in self._emitted_loops:
                self._anchors.setdefault(cur, (out, len(out)))
                out += self._emit_loop(L, b)
                cur = self._after_loop(L)
                prev = L.latch
                continue
            self._anchors.setdefault(cur, (out, len(out)))
            self.res.done.add(cur)
            last = b.last
            if last is None or last.is_return:
                out += self._stmts(b)
                if prev is not None and prev != cur:
                    pass
                break
            ss = self.succ[cur]
            if len(ss) == 0:
                out += self._stmts(b)
                break
            if len(ss) == 1:
                out += self._stmts(b)
                if prev is not None:
                    self._edge(prev, cur, "seq-in")
                self._edge(cur, ss[0], "seq")
                prev, cur = cur, ss[0]
                continue
            # two successors: must be a real conditional (prep/loop ops never reach here)
            if last.opname not in COND_OPS:
                out += self._stmts(b)
                # transparent control (shouldn't happen; keep semantics with goto)
                self.res.stats["fallback-block"] = self.res.stats.get("fallback-block", 0) + 1
                out.append(self._goto(ss[0]))
                self._edge(cur, ss[0], "goto-fallback")
                if prev is not None:
                    self._edge(prev, cur, "seq-in")
                self._edge(cur, ss[1], "seq")
                prev, cur = cur, ss[1]
                continue
            cond = taken_cond(last, self.bc, self.proto)
            m = self.ipdom.get(cur)
            if m is None or m not in self.reachable:
                # no join: try terminator-style `if C then <taken> end` when the
                # taken region cannot fall through (early returns, breaks...).
                _snap = (set(self.res.done), dict(self._anchors), set(self._emitted_loops),
                         set(self._gotos), dict(self._labels), len(self.res.edges))
                then = self._build(ss[0], dict(stops), latches, confine)
                if not (then and then[-1].kind in ("return", "goto", "break", "continue")):
                    (self.res.done, self._anchors, self._emitted_loops,
                     self._gotos, self._labels, n_edges) = (
                        _snap[0], _snap[1], _snap[2], _snap[3], _snap[4], _snap[5])
                    del self.res.edges[n_edges:]
                if not then:
                    # taken side empty (already structured): fall through to goto form
                    pass
                elif then[-1].kind in ("return", "goto", "break", "continue"):
                    out += self._stmts(b)
                    if prev is not None:
                        self._edge(prev, cur, "seq-in")
                    self._edge(cur, ss[0], "if-then-term")
                    self._edge(cur, ss[1], "if-else-inline")
                    out.append(Node("if", cond=cond, then_=then, else_=[]))
                    self.res.stats["if-term"] = self.res.stats.get("if-term", 0) + 1
                    prev, cur = cur, ss[1]
                    continue
                out += self._stmts(b)
                self.res.stats["fallback-nomerge"] = self.res.stats.get("fallback-nomerge", 0) + 1
                out.append(Node("stmt", f"if {cond.render()} then {self._goto(ss[0]).text}"))
                self._edge(cur, ss[0], "goto-cond")
                if prev is not None:
                    self._edge(prev, cur, "seq-in")
                self._edge(cur, ss[1], "seq")
                prev, cur = cur, ss[1]
                continue
            if prev is not None:
                self._edge(prev, cur, "merge-out")
            self._edge(cur, ss[0], "if-then")
            self._edge(cur, ss[1], "if-else")
            out += self._stmts(b)
            stops2 = dict(stops)
            if m not in stops2:
                stops2[m] = "fallthrough"
            then = self._build(ss[0], stops2, latches, confine)
            els = self._build(ss[1], stops2, latches, confine)
            if not then and els:
                cond, then, els = cond.negate(), els, then
            out.append(Node("if", cond=cond, then_=then, else_=els))
            prev, cur = cur, m
            continue
        return out

    def _absorbable(self, latch: int) -> bool:
        """Latch terminator is the loop's own test/back-jump (absorb it) rather
        than body control flow (structure it as a branch)."""
        B = {b.id: b for b in self.cfg.blocks}
        last = B[latch].last
        if last is None:
            return True
        if last.opname in ("FORNLOOP", "FORGLOOP", "JUMPBACK"):
            return True
        L0 = self.latch_loop.get(latch)
        if (L0 is not None and L0.kind == "repeat" and last.opname in COND_OPS
                and last.jump_target is not None
                and self._block_of_pc(last.jump_target) == L0.header):
            return True
        # single fallthrough straight back into the header: silent back-edge
        return self.succ[latch] == [L0.header] if L0 is not None else False

    def _log_loop_edges(self, L: Loop) -> None:
        B = {b.id: b for b in self.cfg.blocks}
        if L.prep is not None:
            for t in self.succ[L.prep]:
                self._edge(L.prep, t, "loop-entry" if t in L.body else "loop-zero")
        for t in self.succ[L.header]:
            if t == L.exit:
                self._edge(L.header, t, "loop-exit")
            elif t in L.body:
                self._edge(L.header, t, "loop-inner")
        for l in L.latches:
            for t in self.succ[l]:
                if t == L.header:
                    self._edge(l, t, "loop-back")
                elif t == L.exit:
                    self._edge(l, t, "loop-exit")
                elif t in L.body:
                    self._edge(l, t, "loop-inner")
                else:
                    self._edge(l, t, "loop-escape")
                    self._gotos.add(t)

    def _emit_loop(self, L: Loop, trig: Block) -> list[Node]:
        B = {b.id: b for b in self.cfg.blocks}
        self._emitted_loops.add(id(L))
        self._log_loop_edges(L)
        if L.header != L.body_entry and L.header not in L.latches:
            # test header is absorbed, never processed as a plain block
            self.res.done.add(L.header)
        pre: list[Node] = []
        if L.kind == "numfor":
            pre_st = self._stmts(B[L.prep]) if L.prep is not None else []
            self.res.done.add(L.prep)  # type: ignore
            A = B[L.prep].last.A if L.prep is not None and B[L.prep].last else 0
            head = f"for R{A + 3} = R{A + 2}, R{A}, R{A + 1} do"
            self._edge(L.prep, L.body_entry, "loop-entry")  # type: ignore
            self._edge(L.prep, L.exit, "loop-zero")  # type: ignore
            body = self._loop_body(L)
            self._edge(L.latch, L.header, "loop-back")
            self._edge(L.latch, L.exit, "loop-exit")
            return pre + pre_st + [Node("numfor", header=head, then_=body)]
        if L.kind == "genfor":
            pre_st = self._stmts(B[L.prep]) if L.prep is not None else []
            self.res.done.add(L.prep)  # type: ignore
            A = B[L.prep].last.A if L.prep is not None and B[L.prep].last else 0
            v = B[L.latch].last.aux & 0xFF if B[L.latch].last and B[L.latch].last.aux else 0
            vars_ = ", ".join(f"R{r}" for r in range(A + 3, A + 3 + max(v, 1)))
            head = f"for {vars_} in R{A}, R{A + 1}, R{A + 2} do"
            test = L.header if B[L.header].last is not None and B[L.header].last.opname == "FORGLOOP" else L.latch
            if test in self.succ[L.prep]:  # type: ignore
                self._edge(L.prep, test, "loop-entry")  # type: ignore
            body = self._loop_body(L)
            self._edge(L.latch, L.header, "loop-back")
            self._edge(L.latch, L.exit, "loop-exit")
            return pre + pre_st + [Node("genfor", header=head, then_=body)]
        if L.kind == "while":
            hlast = B[L.header].last
            assert hlast is not None
            cond = taken_cond(hlast, self.bc, self.proto).negate()
            self._edge(L.header, L.body_entry, "loop-entry")
            body = self._loop_body(L)
            self._edge(L.latch, L.header, "loop-back")
            self._edge(L.header, L.exit, "loop-exit")
            return pre + [Node("while", cond=cond, then_=body)]
        if L.kind == "whiletrue":
            top = [] if L.header == L.latch else self._stmts(B[L.header])
            self.res.done.add(L.header)
            self._edge(L.header, L.body_entry, "loop-entry")
            body = self._loop_body(L, prefix=top)
            self._edge(L.latch, L.header, "loop-back")
            return pre + [Node("while", cond=Cond("truthy", "true"), then_=body)]
        if L.kind == "repeat":
            if L.header == L.latch:
                top = self._stmts(B[L.header])
                self.res.done.add(L.header)
                llast = B[L.latch].last
                assert llast is not None
                cond = taken_cond(llast, self.bc, self.proto).negate()
                self._edge(L.latch, L.header, "loop-back")
                self._edge(L.latch, L.exit, "loop-exit")
                return pre + [Node("repeat", cond=cond, then_=top)]
            self._edge(L.header, L.body_entry, "loop-entry")
            body = self._loop_body(L)
            llast = B[L.latch].last
            assert llast is not None
            cond = taken_cond(llast, self.bc, self.proto).negate()
            self._edge(L.latch, L.header, "loop-back")
            self._edge(L.latch, L.exit, "loop-exit")
            return pre + [Node("repeat", cond=cond, then_=body)]
        raise StructError(f"unknown loop kind {L.kind}")

    def _loop_body(self, L: Loop, prefix: list[Node] | None = None) -> list[Node]:
        stops: dict[int, str] = {}
        if L.exit is not None:
            stops[L.exit] = "break"
        stops[L.header] = "continue"
        self._latch_stack.append(L.latches)
        try:
            body = self._build(L.body_entry, stops, L.latches, set(L.body))
        finally:
            self._latch_stack.pop()
        return (list(prefix or []) + body)

    def _after_loop(self, L: Loop) -> int | None:
        return L.exit


def structure_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> StructResult:
    return Structurer(bc, proto).structure()


def render_struct(res: StructResult, bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> str:
    dn = proto.debugname
    label = dn.decode("utf-8", "replace") if isinstance(dn, bytes) else (dn or "??")
    head = [f"-- function P{proto.id} ({label}): structured, "
            f"loops={res.stats.get('loops', {})} gotos={res.stats.get('gotos', 0)} "
            f"unclassified={res.stats.get('unclassified', 0)}"]
    return "\n".join(head + render_all(res.nodes))


if __name__ == "__main__":
    raw = sys.argv[1:]
    args: list[str] = []
    only = None
    skip_next = False
    for k, a in enumerate(raw):
        if skip_next:
            skip_next = False
            continue
        if a == "--proto":
            only = int(raw[k + 1])
            skip_next = True
        elif a.startswith("--proto="):
            only = int(a.split("=", 1)[1])
        else:
            args.append(a)
    files = args or ["fixtures/v14/loops.luac"]
    bad = False
    for f in files:
        try:
            bc = parser_v14.parse(open(f, "rb").read())
            for p in bc.protos:
                if only is not None and p.id != only:
                    continue
                res = structure_proto(bc, p)
                print(f"== {f} Function {p.id} ==")
                print(render_struct(res, bc, p))
                print()
        except (parser_v14.ParseError, DisasmError, CFGError, IRError, StructError) as e:
            bad = True
            print(f"FAIL {f}: {type(e).__name__}: {e}")
    raise SystemExit(1 if bad else 0)
