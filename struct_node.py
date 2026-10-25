"""Structurer shared representation: conditions, CFG nodes, loop records.

One of five structurer modules; start at struct_v14.py for the pipeline overview.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError, fmt_const
from ir_v14 import Lifter, IRError

_RDEF_RE = re.compile(r"^R(\d+) = ")
_RMULTI_LHS_RE = re.compile(r"^(R\d+(?:, R\d+)*) = ")
_RCOMPOUND_RE = re.compile(r"^R(\d+)\s*(?:\+=|-=|\*=|/=|//=|%=|\^=|\.\.=)")
def compound_match(text: str):
    """Match a `R# <op>= ...` compound-assign line, else None. Single
    owner of the compound LHS shape: struct_emit, emit_v14 and
    struct_sweep all go through here instead of owning copies."""
    return _RCOMPOUND_RE.match(text)
_RTOP_LHS_RE = re.compile(r"^R(\d+)\.\.top = ")
_RFOR_BIND_RE = re.compile(r"^for (R\d+(?:, R\d+)*) (=|in) ")
_RREF_RE = re.compile(r"R(\d+)")
_RREF_RES: dict[int, re.Pattern] = {}
class StructError(Exception):
    pass
FASTCALL_OPS = {"FASTCALL", "FASTCALL1", "FASTCALL2", "FASTCALL2K",
                "FASTCALL3", "FASTPCALL", "FINCONSTRUCT"}
COND_OPS = {
    "JUMPIF", "JUMPIFNOT",
    "JUMPIFEQ", "JUMPIFLE", "JUMPIFLT",
    "JUMPIFNOTEQ", "JUMPIFNOTLE", "JUMPIFNOTLT",
    "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS",
    "CMPPROTO",
}
FOR_OPS = frozenset({
    "FORNPREP", "FORNLOOP", "FORGLOOP",
    "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"})
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
        # Compound conditions are only built by late sweep passes (guard
        # merging), which never negate; fail fast if that changes.
        if self.op in ("or", "and"):
            raise StructError(f"cannot negate compound cond: {self.render()}")
        return Cond(_NEG[self.op], self.l, self.r)

    def render(self) -> str:
        if self.op == "or":
            return f"({self.l}) or ({self.r})"
        if self.op == "and":
            return f"({self.l}) and ({self.r})"
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
            k = fmt_const(bc, proto, aux & 0xFFFFFF, full=True)
        except Exception:
            k = f"K{aux & 0xFFFFFF}"
        return Cond("~=" if aux >> 31 else "==", R(A), k)
    if n == "CMPPROTO":
        return Cond("proto-ne", R(A), f"P{aux}")
    raise StructError(f"not a condition op: {n} at pc {ins.pc}")
_PURE_DEF_OPS = {
    "LOADNIL", "LOADB", "LOADN", "LOADK", "LOADKX", "MOVE",
    "GETGLOBAL", "GETUPVAL", "GETIMPORT",
    "GETTABLE", "GETTABLEKS", "GETUDATAKS", "GETTABLEN",
    "ADD", "SUB", "MUL", "DIV", "MOD", "POW", "IDIV",
    "ADDK", "SUBK", "MULK", "DIVK", "MODK", "POWK", "IDIVK",
    "SUBRK", "DIVRK", "AND", "OR", "ANDK", "ORK",
    "CONCAT", "NOT", "MINUS", "LENGTH",
    "NEWTABLE", "DUPTABLE", "DUPCLOSURE", "NEWCLOSURE",
}
@dataclass
class Node:
    kind: str  # stmt,label,goto,if,while,repeat,numfor,genfor,break,continue,return
    text: str = ""
    cond: Cond | None = None
    then_: list = field(default_factory=list)    # if-then / loop body
    else_: list = field(default_factory=list)    # if-else
    header: str = ""  # for/while header text
    pure: bool = False   # stmt is a pure R[defreg] def, droppable if unused
    defreg: int | None = None

    def render(self, ind: int = 0) -> list[str]:
        p = "  " * ind
        if self.kind in ("stmt", "label", "goto", "break", "continue", "return"):
            return [p + self.text]
        if self.kind == "if":
            out = [p + f"if {self.cond.render()} then"]
            out += render_all(self.then_, ind + 1)
            # `else` holding exactly one `if` renders as `elseif`: same
            # semantics, and it keeps pc-dispatcher chains (one dispatch
            # per block) flat instead of nesting one level per block.
            cur_else = self.else_
            while len(cur_else) == 1 and cur_else[0].kind == "if":
                inner = cur_else[0]
                out.append(p + f"elseif {inner.cond.render()} then")
                out += render_all(inner.then_, ind + 1)
                cur_else = inner.else_
            if cur_else:
                out.append(p + "else")
                out += render_all(cur_else, ind + 1)
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
    goto_edges: list[tuple[int, int]] = field(default_factory=list)  # (src, dst); src=-1 for sweep roots
    stats: dict = field(default_factory=dict)
    edges: list[tuple[int, int, str]] = field(default_factory=list)  # (src, dst, kind)
    done: set[int] = field(default_factory=set)
    skipped: list[int] = field(default_factory=list)
