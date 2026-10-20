"""Luau v14 SSA construction + live ranges (decompiler step 2 of step 3).

Builds on cfg_v14 blocks. Classic Cytron/Cooper-Harvey-Kennedy SSA:
  def/use collection (mirrors Bytecode.h operand docs + ir_v14 Lifter effects)
  -> iterative dominators -> dominance frontiers -> phi placement
  -> dom-tree renaming -> backward liveness -> per-version live intervals.

Multret (unknown static extent) is handled conservatively: any range that
runs "to top" covers R[x..maxstack). This may add extra phis but never
drops a real def or use, so renaming stays sound.

Usage:
  python ssa_v14.py fixtures/v14/loops.luac [--proto N]
"""
from __future__ import annotations
from dataclasses import dataclass, field
import sys
import opcodes_v14 as op
import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError


class SSAError(Exception):
    pass


def _range(a: int, b: int) -> set[int]:
    """Half-open register range clamped to 0..254."""
    return set(range(max(a, 0), min(b, 255)))


def defs_uses(ins: Insn, maxstack: int) -> tuple[set[int], set[int]]:
    """(defined regs, used regs) for one instruction. Conservative on multret."""
    n, A, B, C, aux = ins.opname, ins.A, ins.B, ins.C, ins.aux or 0
    top = min(maxstack, 255)

    if n in ("NOP", "BREAK", "COVERAGE", "NATIVECALL", "PREPVARARGS"):
        return set(), set()
    if n in ("LOADNIL", "LOADN", "LOADK", "LOADKX", "GETGLOBAL", "GETUPVAL",
             "GETIMPORT", "DUPTABLE", "DUPCLOSURE", "NEWCLOSURE", "NEWTABLE"):
        return {A}, set()
    if n == "LOADB":
        return {A}, set()
    if n == "MOVE":
        return {A}, {B}
    if n == "GETTABLE":
        return {A}, {B, C}
    if n in ("GETTABLEKS", "GETUDATAKS", "GETTABLEN"):
        return {A}, {B}
    if n == "SETTABLE":
        return set(), {A, B, C}
    if n in ("SETTABLEKS", "SETUDATAKS", "SETTABLEN"):
        return set(), {A, B}
    if n == "SETGLOBAL":
        return set(), {A}
    if n == "SETUPVAL":
        return set(), {A}
    if n == "CLOSEUPVALS":
        return set(), _range(A, top)
    if n in ("NAMECALL", "NAMECALLUDATA"):
        return {A, A + 1}, {B}
    if n in ("CALL", "CALLFB"):
        if B == 0:
            uses = _range(A + 1, top)
        else:
            uses = _range(A + 1, A + B - 1)
        uses.add(A)
        if C == 0:
            defs = _range(A, top)
        elif C == 1:
            defs = set()
        else:
            defs = _range(A, A + C - 1)
        return defs, uses
    if n == "RETURN":
        if B == 0:
            return set(), _range(A, top)
        if B == 1:
            return set(), set()
        return set(), _range(A, A + B - 1)
    if n in ("JUMP", "JUMPBACK", "JUMPX"):
        return set(), set()
    if n in ("JUMPIF", "JUMPIFNOT"):
        return set(), {A}
    if n in ("JUMPIFEQ", "JUMPIFLE", "JUMPIFLT", "JUMPIFNOTEQ",
             "JUMPIFNOTLE", "JUMPIFNOTLT"):
        return set(), {A, aux}
    if n in ("JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS"):
        return set(), {A}
    if n == "CMPPROTO":
        return set(), {A}
    if n in ("ADD", "SUB", "MUL", "DIV", "MOD", "POW", "IDIV", "AND", "OR"):
        return {A}, {B, C}
    if n in ("ADDK", "SUBK", "MULK", "DIVK", "MODK", "POWK", "IDIVK", "ANDK", "ORK"):
        return {A}, {B}
    if n in ("SUBRK", "DIVRK"):
        return {A}, {C}
    if n == "CONCAT":
        return {A}, _range(B, C + 1)
    if n in ("NOT", "MINUS", "LENGTH"):
        return {A}, {B}
    if n == "SETLIST":
        uses = {A} | (_range(B, top) if C == 0 else _range(B, B + C - 1))
        return set(), uses
    if n == "FORNPREP":
        return {A + 2, A + 3}, {A, A + 1, A + 2}
    if n == "FORNLOOP":
        return {A + 2, A + 3}, {A, A + 1, A + 2}
    if n == "FORGLOOP":
        v = aux & 0xFF
        return _range(A + 2, A + 3 + v), _range(A, A + 3)
    if n in ("FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"):
        return _range(A, A + 3), _range(A, A + 3)
    if n == "FASTCALL":
        return set(), set()
    if n == "FASTCALL1":
        return set(), {B}
    if n == "FASTCALL2":
        return set(), {B, aux & 0xFF}
    if n == "FASTCALL2K":
        return set(), {B}
    if n == "FASTCALL3":
        return set(), {B, aux & 0xFF, (aux >> 8) & 0xFF}
    if n in ("FASTPCALL",):
        return set(), set()
    if n == "FINCONSTRUCT":
        return set(), {A}
    if n == "GETVARARGS":
        if B == 0:
            return _range(A, top), set()
        return _range(A, A + B - 1), set()
    if n == "CAPTURE":
        if A in (op.LCT_VAL, op.LCT_REF):
            return set(), {B}
        return set(), set()
    if n == "NEWCLASSMEMBER":
        return set(), {A, C}
    if n == "NEWCLASS":
        return {A}, ({B} if B != 0xFF else set())
    if n == "CONSTRUCT":
        return {A}, {B}
    raise SSAError(f"no def/use rule for {n} at pc {ins.pc}")


# ---------------------------------------------------------------------------
# SSA result
# ---------------------------------------------------------------------------

@dataclass
class SSA:
    cfg: CFG
    numparams: int
    maxstack: int
    # dominance
    idom: list[int | None] = field(default_factory=list)
    dom_children: list[list[int]] = field(default_factory=list)
    reachable: set[int] = field(default_factory=set)
    # per-block def/use (registers)
    block_defs: list[set[int]] = field(default_factory=list)
    block_uses: list[set[int]] = field(default_factory=list)
    # phis: block_id -> {reg: version}; operands: (block_id, reg) -> {pred_id: version|None}
    phis: dict[int, dict[int, int]] = field(default_factory=dict)
    phi_operands: dict[tuple[int, int], dict[int, int | None]] = field(default_factory=dict)
    # versions
    nversions: dict[int, int] = field(default_factory=dict)  # reg -> count
    def_ver: dict[tuple[int, int], int] = field(default_factory=dict)   # (pc, reg) -> ver
    use_ver: dict[tuple[int, int], int | None] = field(default_factory=dict)  # (pc, reg) -> ver|None(undef)
    param_ver: dict[int, int] = field(default_factory=dict)  # param reg -> ver (0)
    undef_uses: list[tuple[int, int]] = field(default_factory=list)  # reachable (pc, reg) with no reaching def
    skipped_blocks: list[int] = field(default_factory=list)  # unreachable, not renamed
    # liveness (registers, reachable blocks)
    live_in: list[set[int]] = field(default_factory=list)
    live_out: list[set[int]] = field(default_factory=list)
    # intervals: (reg, ver) -> ((def_rank, def_idx), (end_rank, end_idx))
    intervals: dict[tuple[int, int], tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=dict)
    rank: dict[int, int] = field(default_factory=dict)  # block_id -> RPO rank

    def ssa_name(self, reg: int, ver: int | None) -> str:
        return f"R{reg}_{ver}" if ver is not None else f"R{reg}_?"

    def dominates(self, a: int, b: int) -> bool:
        """Does block a dominate block b? (reachable blocks only)."""
        x: int | None = b
        while x is not None:
            if x == a:
                return True
            if x == self.idom[x]:
                break
            x = self.idom[x]
        return False

    def dump(self) -> str:
        lines = [f"ssa: {len(self.reachable)}/{len(self.cfg.blocks)} reachable blocks, "
                 f"{sum(len(v) for v in self.phis.values())} phis, "
                 f"{sum(self.nversions.values())} versions, "
                 f"{len(self.undef_uses)} undef uses"]
        for b in self.cfg.blocks:
            if b.id not in self.reachable:
                lines.append(f"B{b.id} pc[{b.start_pc}..{b.end_pc}) UNREACHABLE")
                continue
            lines.append(f"B{b.id} pc[{b.start_pc}..{b.end_pc}) "
                         f"live_in={sorted(self.live_in[b.id])} live_out={sorted(self.live_out[b.id])}")
            for reg, ver in sorted(self.phis.get(b.id, {}).items()):
                ops = self.phi_operands.get((b.id, reg), {})
                op_s = ", ".join(f"B{p}={self.ssa_name(reg, v)}" for p, v in sorted(ops.items()))
                lines.append(f"    phi {self.ssa_name(reg, ver)} <- [{op_s}]")
            for idx, ins in enumerate(b.insns):
                parts = []
                d, u = defs_uses(ins, self.maxstack)
                for r in sorted(u):
                    parts.append(self.ssa_name(r, self.use_ver.get((ins.pc, r))))
                for r in sorted(d):
                    parts.append(f"{self.ssa_name(r, self.def_ver.get((ins.pc, r)))}=")
                lines.append(f"    {ins.pc}: {ins.opname}  {', '.join(parts)}")
        lines.append(f"intervals: {len(self.intervals)}")
        for (reg, ver), (s, e) in sorted(self.intervals.items()):
            lines.append(f"    R{reg}_{ver}: {s}..{e}")
        return "\n".join(lines)


def _rpo(cfg: CFG) -> list[int]:
    seen: set[int] = set()
    order: list[int] = []

    def visit(b: int) -> None:
        seen.add(b)
        for s in cfg.blocks[b].successors:
            if s not in seen:
                visit(s)
        order.append(b)

    visit(cfg.entry)
    order.reverse()
    return order


def build_ssa(code: list[int], numparams: int = 0, maxstack: int = 255) -> SSA:
    cfg = build_cfg(code)
    s = SSA(cfg=cfg, numparams=numparams, maxstack=maxstack)
    n = len(cfg.blocks)
    s.idom = [None] * n
    s.dom_children = [[] for _ in range(n)]
    s.block_defs = [set() for _ in range(n)]
    s.block_uses = [set() for _ in range(n)]
    s.live_in = [set() for _ in range(n)]
    s.live_out = [set() for _ in range(n)]

    if numparams < 0 or maxstack <= 0:
        raise SSAError(f"bad header params={numparams} maxstack={maxstack}")

    # ---- per-insn def/use + per-block def/use sets ----
    insn_du: dict[int, tuple[set[int], set[int]]] = {}
    for b in cfg.blocks:
        for ins in b.insns:
            d, u = defs_uses(ins, maxstack)
            insn_du[ins.pc] = (d, u)
            s.block_defs[b.id] |= d
    for b in cfg.blocks:
        killed: set[int] = set()
        for ins in b.insns:
            d, u = insn_du[ins.pc]
            s.block_uses[b.id] |= (u - killed)
            killed |= d

    # ---- RPO + iterative dominators (Cooper-Harvey-Kennedy) ----
    rpo = _rpo(cfg)
    s.reachable = set(rpo)
    s.rank = {b: i for i, b in enumerate(rpo)}
    order_idx = {b: i for i, b in enumerate(rpo)}
    s.idom[cfg.entry] = cfg.entry
    changed = True
    while changed:
        changed = False
        for b in rpo[1:]:
            preds = [p for p in cfg.blocks[b].predecessors if p in s.reachable]
            if not preds:
                continue  # unreachable-ish (no reachable pred); stays None
            new_idom = preds[0]
            for p in preds[1:]:
                if s.idom[p] is not None:
                    # finger walk
                    f1, f2 = p, new_idom
                    while f1 != f2:
                        while order_idx.get(f1, -1) > order_idx.get(f2, -1):
                            f1 = s.idom[f1]  # type: ignore
                        while order_idx.get(f2, -1) > order_idx.get(f1, -1):
                            f2 = s.idom[f2]  # type: ignore
                    new_idom = f1
            if s.idom[b] != new_idom:
                s.idom[b] = new_idom
                changed = True
    for b in rpo[1:]:
        if s.idom[b] is not None and s.idom[b] != b:
            s.dom_children[s.idom[b]].append(b)  # type: ignore
    s.skipped_blocks = sorted(b.id for b in cfg.blocks if b.id not in s.reachable)

    # ---- dominance frontiers ----
    df: dict[int, set[int]] = {b: set() for b in rpo}
    for b in rpo:
        preds = [p for p in cfg.blocks[b].predecessors if p in s.reachable]
        if len(preds) >= 2:
            for p in preds:
                runner: int | None = p
                while runner is not None and runner != s.idom[b]:
                    df[runner].add(b)
                    runner = s.idom[runner]

    # ---- phi placement (Cytron) ----
    # def blocks per reg (reachable only); params count as defined at entry
    all_regs: set[int] = set()
    for b in rpo:
        all_regs |= s.block_defs[b]
    all_regs |= set(range(min(numparams, 255)))
    def_blocks: dict[int, set[int]] = {r: set() for r in all_regs}
    for b in rpo:
        for r in s.block_defs[b]:
            def_blocks.setdefault(r, set()).add(b)
    for r in range(min(numparams, 255)):
        def_blocks.setdefault(r, set()).add(cfg.entry)
    for r in sorted(def_blocks):
        work = sorted(def_blocks[r], key=lambda b: s.rank[b])
        placed: set[int] = set()
        ever = set(work)
        while work:
            x = work.pop()
            for y in df.get(x, set()):
                if y not in placed:
                    s.phis.setdefault(y, {})[r] = -1  # version assigned at rename
                    placed.add(y)
                    if y not in ever:
                        ever.add(y)
                        work.append(y)

    # ---- renaming over the dom tree ----
    counter: dict[int, int] = {}
    stack: dict[int, list[int]] = {}

    def new_ver(r: int) -> int:
        v = counter.get(r, 0)
        counter[r] = v + 1
        stack.setdefault(r, []).append(v)
        return v

    # params: version 0 at entry
    for r in range(min(numparams, 255)):
        new_ver(r)
        s.param_ver[r] = 0

    sys.setrecursionlimit(max(10000, n * 4 + 100))

    def rename(b: int) -> None:
        pushed: list[int] = []
        # phis first
        for r in sorted(s.phis.get(b, {})):
            v = new_ver(r)
            s.phis[b][r] = v
            pushed.append(r)
        # insns in order
        for ins in cfg.blocks[b].insns:
            d, u = insn_du[ins.pc]
            for r in sorted(u):
                if stack.get(r):
                    s.use_ver[(ins.pc, r)] = stack[r][-1]
                else:
                    s.use_ver[(ins.pc, r)] = None
                    s.undef_uses.append((ins.pc, r))
            for r in sorted(d):
                v = new_ver(r)
                s.def_ver[(ins.pc, r)] = v
                pushed.append(r)
        # phi operands in successors
        for succ in cfg.blocks[b].successors:
            if succ not in s.reachable:
                continue
            for r in s.phis.get(succ, {}):
                v = stack[r][-1] if stack.get(r) else None
                s.phi_operands.setdefault((succ, r), {})[b] = v
                if v is None:
                    s.undef_uses.append((-succ - 1, r))  # phi operand missing; pc<0 marks phi
        for c in sorted(s.dom_children[b]):
            rename(c)
        for r in reversed(pushed):
            stack[r].pop()

    rename(cfg.entry)
    s.nversions = dict(counter)

    # ---- liveness (registers; phi-aware) ----
    # phi operand from B to succ S is a use at the end of B
    phi_use: list[set[int]] = [set() for _ in range(n)]
    for (succ, r) in s.phi_operands:
        for p in cfg.blocks[succ].predecessors:
            if p in s.reachable:
                phi_use[p].add(r)
    changed = True
    while changed:
        changed = False
        for b in reversed(rpo):
            new_out: set[int] = set(phi_use[b])
            for succ in cfg.blocks[b].successors:
                if succ in s.reachable:
                    new_out |= s.live_in[succ]
            # phi defs kill at block top
            phidef = set(s.phis.get(b, {}))
            new_in = s.block_uses[b] | ((new_out | phi_use[b]) - s.block_defs[b] - phidef)
            if new_out != s.live_out[b] or new_in != s.live_in[b]:
                s.live_out[b], s.live_in[b] = new_out, new_in
                changed = True

    # ---- intervals per version ----
    # order key: (rank, idx) with phi def idx=-1, param def idx=-2
    pc2b: dict[int, int] = {}
    for b in cfg.blocks:
        for ins in b.insns:
            pc2b[ins.pc] = b.id
    def_points: dict[tuple[int, int], tuple[int, int]] = {}
    for b in rpo:
        for r, v in s.phis.get(b, {}).items():
            def_points[(r, v)] = (s.rank[b], -1)
    for r, v in s.param_ver.items():
        def_points[(r, v)] = (s.rank[cfg.entry], -2)
    for (pc, r), v in s.def_ver.items():
        b = pc2b[pc]
        idx = next(i for i, ins in enumerate(cfg.blocks[b].insns) if ins.pc == pc)
        if (r, v) not in def_points:
            def_points[(r, v)] = (s.rank[b], idx)
    last_use: dict[tuple[int, int], tuple[int, int]] = dict(def_points)
    for (pc, r), v in s.use_ver.items():
        if v is None:
            continue
        b = pc2b[pc]
        idx = next(i for i, ins in enumerate(cfg.blocks[b].insns) if ins.pc == pc)
        key = (s.rank[b], idx)
        if key > last_use.get((r, v), def_points[(r, v)]):
            last_use[(r, v)] = key
    for (succ, r), ops in s.phi_operands.items():
        for p, v in ops.items():
            if v is None:
                continue
            key = (s.rank[p], len(cfg.blocks[p].insns))  # operand live at end of pred
            if key > last_use.get((r, v), def_points[(r, v)]):
                last_use[(r, v)] = key
    for k, d in def_points.items():
        e = last_use.get(k, d)
        s.intervals[k] = (d, max(d, e))

    return s


def build_ssa_for_proto(proto: parser_v14.Proto) -> SSA:
    return build_ssa(proto.code, proto.numparams, proto.maxstack or 255)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--proto")]
    only = None
    for k, a in enumerate(sys.argv[1:]):
        if a.startswith("--proto"):
            if "=" in a:
                only = int(a.split("=", 1)[1])
            else:
                only = int(sys.argv[sys.argv.index(a) + 1])
    files = args or ["fixtures/v14/loops.luac"]
    bad = False
    for f in files:
        try:
            bc = parser_v14.parse(open(f, "rb").read())
            for p in bc.protos:
                if only is not None and p.id != only:
                    continue
                s = build_ssa_for_proto(p)
                dn = p.debugname
                label = dn.decode("utf-8", "replace") if isinstance(dn, bytes) else (dn or "??")
                print(f"== {f} Function {p.id} ({label}) ==")
                print(s.dump())
                print()
        except (parser_v14.ParseError, DisasmError, CFGError, SSAError) as e:
            bad = True
            print(f"FAIL {f}: {type(e).__name__}: {e}")
    raise SystemExit(1 if bad else 0)
