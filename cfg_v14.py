"""Luau v14 CFG builder. Builds on disassembler_v14.split() output.

Mirrors control-flow semantics from luau/Common/include/Luau/BytecodeUtils.h:
  isFallthrough(), isJumpD(), isFastCall(), isSkipC(), getJumpTarget().

Usage:
  python cfg_v14.py fixtures/v14/loops.luac
  python cfg_v14.py --dot fixtures/v14/loops.luac  (graphviz dot output)
"""
from __future__ import annotations
from dataclasses import dataclass, field
import sys
import opcodes_v14 as op
import parser_v14
from disassembler_v14 import split, DisasmError


class CFGError(Exception):
    pass


# Ops that never fall through (isFallthrough() == False)
_NO_FALLTHROUGH = {
    "RETURN", "JUMP", "JUMPBACK", "JUMPX",
    "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT",
}

# Conditional branches with TWO successors (target + fallthrough).
# Unconditional members of _NO_FALLTHROUGH have ONE successor (target only).
# Everything else with a jump target (FASTCALL*, LOADB-skip) also has two.
_TWO_SUCC_JUMPS = {
    "JUMPIF", "JUMPIFNOT",
    "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT",
    "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT",
    "FORNPREP", "FORNLOOP", "FORGLOOP",
    "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS",
    "CMPPROTO",
}


@dataclass
class Insn:
    pc: int            # word index of header
    op_index: int
    opname: str
    header: int
    aux: int | None
    length: int        # 1 or 2 words
    A: int
    B: int
    C: int
    D: int
    E: int
    jump_target: int | None
    falls_through: bool

    @property
    def end_pc(self) -> int:
        return self.pc + self.length

    @property
    def is_return(self) -> bool:
        return self.opname == "RETURN"

    @property
    def is_unconditional(self) -> bool:
        return self.opname in _NO_FALLTHROUGH and self.jump_target is not None

    @property
    def is_branch(self) -> bool:
        return self.jump_target is not None


def decode(code: list[int]) -> list[Insn]:
    """Decode raw words into Insn list. Raises DisasmError/CFGError on bad data."""
    out: list[Insn] = []
    for pc, opname, header, aux in split(code):
        oi = op.op(header)
        jt = op.get_jump_target(header, aux or 0, pc)
        falls = op.is_fallthrough(oi)
        out.append(Insn(
            pc=pc, op_index=oi, opname=opname, header=header, aux=aux,
            length=op.get_op_length(oi),
            A=op.a(header), B=op.b(header), C=op.c(header),
            D=op.d(header), E=op.e(header),
            jump_target=jt, falls_through=falls,
        ))
    return out


@dataclass
class Block:
    id: int
    start_pc: int
    insns: list[Insn] = field(default_factory=list)
    successors: list[int] = field(default_factory=list)
    predecessors: list[int] = field(default_factory=list)

    @property
    def end_pc(self) -> int:
        return self.insns[-1].end_pc if self.insns else self.start_pc

    @property
    def last(self) -> Insn | None:
        return self.insns[-1] if self.insns else None

    def is_loop_header(self, blocks: list["Block"]) -> bool:
        # target of a backedge (some successor block starts at or before us
        # via a backward jump, or we are entered from below)
        for pred_id in self.predecessors:
            pred = blocks[pred_id]
            if pred.last and pred.last.jump_target == self.start_pc and pred.start_pc > self.start_pc:
                return True
        return False


@dataclass
class CFG:
    insns: list[Insn]
    blocks: list[Block]
    entry: int = 0
    pc_to_block: dict[int, int] = field(default_factory=dict)
    pc_to_insn: dict[int, int] = field(default_factory=dict)

    def block_of_pc(self, pc: int) -> int:
        if pc not in self.pc_to_block:
            raise CFGError(f"pc {pc} is not a block start (mid-instruction jump?)")
        return self.pc_to_block[pc]

    def successors_of(self, bid: int) -> list[int]:
        return self.blocks[bid].successors

    def backedges(self) -> list[tuple[int, int]]:
        """(source_block, target_block) where target starts at/before source."""
        out = []
        for b in self.blocks:
            for s in b.successors:
                if self.blocks[s].start_pc <= b.start_pc:
                    out.append((b.id, s))
        return out

    def dump(self) -> str:
        lines = [f"cfg: {len(self.insns)} insns, {len(self.blocks)} blocks, entry=B{self.entry}"]
        for b in self.blocks:
            last = b.last
            flag = ""
            if last and last.is_return:
                flag = " [return]"
            elif last and last.is_unconditional:
                flag = " [uncond]"
            elif last and last.is_branch:
                flag = " [branch]"
            lines.append(
                f"B{b.id} pc[{b.start_pc}..{b.end_pc}) "
                f"succ={b.successors} pred={b.predecessors}{flag}"
            )
            for ins in b.insns:
                tgt = f" -> {ins.jump_target}" if ins.jump_target is not None else ""
                lines.append(f"    {ins.pc}: {ins.opname}{tgt}")
        return "\n".join(lines)

    def to_dot(self, name: str = "cfg") -> str:
        lines = [f'digraph {name} {{', '  node [shape=box];']
        for b in self.blocks:
            label = f"B{b.id}\\n" + "\\n".join(f"{i.pc}: {i.opname}" for i in b.insns)
            lines.append(f'  B{b.id} [label="{label}"];')
        for b in self.blocks:
            for s in b.successors:
                style = ""
                last = b.last
                if last and last.jump_target is not None and s == self.pc_to_block.get(last.jump_target, -1):
                    # jump edge vs fallthrough: mark backedges bold
                    if self.blocks[s].start_pc <= b.start_pc:
                        style = " [style=bold,color=red]"
                lines.append(f"  B{b.id} -> B{s}{style};")
        lines.append("}")
        return "\n".join(lines)


def _successor_pcs(ins: Insn, next_pc: int | None, code_len: int) -> list[int]:
    """Word-pc successors for one instruction. next_pc=None at end of code."""
    if ins.is_return:
        return []
    if ins.opname in _NO_FALLTHROUGH:
        # unconditional jump / return-like: target only (RETURN handled above)
        if ins.jump_target is None:
            # RETURN already returned; others must have targets
            raise CFGError(f"{ins.opname} at pc {ins.pc} has no target")
        return [ins.jump_target]
    succ: list[int] = []
    if ins.jump_target is not None:
        succ.append(ins.jump_target)
    # fallthrough
    if next_pc is not None and next_pc < code_len:
        # conditional branch or plain fallthrough both continue here;
        # unconditional already returned above so this is safe
        if ins.jump_target is None or ins.opname in _TWO_SUCC_JUMPS or \
                ins.opname in ("FASTCALL", "FASTCALL1", "FASTCALL2", "FASTCALL2K", "FASTCALL3", "FASTPCALL", "FINCONSTRUCT") or \
                (ins.opname == "LOADB" and ins.C != 0):
            succ.append(next_pc)
        elif ins.jump_target is None:
            succ.append(next_pc)
    elif ins.jump_target is None:
        # last insn with no jump and no fallthrough slot: implicit return point
        pass
    # dedupe preserving order (e.g. JUMP 0 -> target == next_pc)
    seen: list[int] = []
    for s in succ:
        if s not in seen:
            seen.append(s)
    return seen


def build_cfg(code: list[int]) -> CFG:
    insns = decode(code)
    if not insns:
        raise CFGError("empty code")
    n_words = code.__len__() if hasattr(code, "__len__") else insns[-1].end_pc
    # word-pc -> insn index; also detect overlaps (split already guarantees this)
    pc_to_insn: dict[int, int] = {}
    insn_starts = set()
    for i, ins in enumerate(insns):
        insn_starts.add(ins.pc)
        pc_to_insn[ins.pc] = i
        if ins.aux is not None:
            # AUX word must not be decoded as an instruction start
            pass

    code_len = max(ins.end_pc for ins in insns)

    # 1. collect leaders: entry, every jump target, and the fallthrough
    # pc after any branching instruction. Straight-line fallthrough is NOT
    # a leader (otherwise every insn becomes its own block).
    leaders: set[int] = {insns[0].pc}
    for idx, ins in enumerate(insns):
        nxt = insns[idx + 1].pc if idx + 1 < len(insns) else None
        if ins.jump_target is not None:
            leaders.add(ins.jump_target)
            if nxt is not None:
                leaders.add(nxt)
        elif not ins.falls_through and nxt is not None:
            # RETURN (or any no-fallthrough without target): next pc starts
            # an unreachable block rather than merging into the returned block
            leaders.add(nxt)

    # 2. validate jump targets land on instruction starts
    for ins in insns:
        if ins.jump_target is not None and ins.jump_target not in insn_starts:
            # allow jump to end-of-code (pc == code_len means "past the end";
            # reference treats it as fall off; normalize to no-block)
            if ins.jump_target == code_len:
                continue
            raise CFGError(
                f"{ins.opname} at pc {ins.pc} targets {ins.jump_target}, "
                f"which is not an instruction start (mid-AUX jump?)"
            )

    # 3. slice into blocks
    sorted_leaders = sorted(leaders - {code_len})
    # drop leaders beyond code (can happen for jump-to-end)
    sorted_leaders = [L for L in sorted_leaders if L in insn_starts]
    leader_to_bid = {pc: i for i, pc in enumerate(sorted_leaders)}
    blocks: list[Block] = [Block(id=i, start_pc=pc) for i, pc in enumerate(sorted_leaders)]
    pc_to_block: dict[int, int] = {}
    cur = -1
    for ins in insns:
        if ins.pc in leader_to_bid:
            cur = leader_to_bid[ins.pc]
        blocks[cur].insns.append(ins)
        pc_to_block[ins.pc] = cur
    # map every word pc (incl. AUX) to block for convenience? No — only starts.

    # 4. wire successors (block ids)
    for b in blocks:
        last = b.last
        if last is None:
            continue
        idx = pc_to_insn[b.insns[-1].pc]
        nxt = insns[idx + 1].pc if idx + 1 < len(insns) else None
        for t in _successor_pcs(last, nxt, code_len):
            if t == code_len:
                continue  # falls off the end: no successor block
            b.successors.append(leader_to_bid[t])
        # dedupe
        b.successors = list(dict.fromkeys(b.successors))
    for b in blocks:
        for s in b.successors:
            blocks[s].predecessors.append(b.id)

    # 5. full-coverage check
    covered = sum(len(b.insns) for b in blocks)
    if covered != len(insns):
        raise CFGError(f"coverage: {covered}/{len(insns)} insns in blocks")

    return CFG(insns=insns, blocks=blocks, entry=0,
               pc_to_block={b.start_pc: b.id for b in blocks},
               pc_to_insn=pc_to_insn)


def build_cfg_for_proto(proto: parser_v14.Proto) -> CFG:
    return build_cfg(proto.code)


def _iter_luac_files() -> list[str]:
    import glob
    files = sorted(glob.glob("fixtures/v14/*.luac"))
    files += sorted(glob.glob("fixtures/v14/**/*.luac", recursive=True))
    # dedupe (recursive glob may repeat top level on some pythons)
    seen: list[str] = []
    for f in files:
        if f not in seen:
            seen.append(f)
    return seen


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dot"]
    dot = "--dot" in sys.argv[1:]
    files = args or ["fixtures/v14/loops.luac"]
    bad = False
    for f in files:
        try:
            bc = parser_v14.parse(open(f, "rb").read())
            for p in bc.protos:
                cfg = build_cfg_for_proto(p)
                if dot:
                    print(cfg.to_dot(f"proto{p.id}"))
                else:
                    dn = p.debugname
                    label = dn.decode() if isinstance(dn, bytes) else (dn or "??")
                    print(f"== {f} Function {p.id} ({label}) ==")
                    print(cfg.dump())
                    print()
        except (parser_v14.ParseError, DisasmError, CFGError) as e:
            bad = True
            print(f"FAIL {f}: {type(e).__name__}: {e}")
    raise SystemExit(1 if bad else 0)
