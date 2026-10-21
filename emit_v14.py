"""Valid-Luau emitter (decompiler step 5, first pass).

Takes structured nodes and renders a per-proto `local function` that the
real Luau parser accepts -- for protos whose control flow structured fully
(no goto/label leftovers). Known approximations are marked with `-- ~~~`:

* registers: function-scope `local R...` header; params become the signature
* `R#A..top` (dynamic multret/varargs/return): expanded to a contiguous
  `R#A..R#max` list where max = highest register referenced in the function.
  Exact when consumers use fixed registers; `f() -> return f()`-style
  value-count passthrough may gain trailing nils (documented).
* multret SETLIST (`= ...multret...`) has no valid form -> proto excluded.
* `goto`/`L#:` leftovers (irreducible flow) -> proto excluded from validity.
* upvalues render as bare `U#` globals (parse-valid; capture lost -- noted).
* `dupclosure 'x'` parses as a call; `P#` closure refs are globals.

Validity here means PARSE-validity under Luau's real grammar, checked by
valid_check.py with Luau.Ast.CLI. Semantic equivalence is a later stage.
"""
from __future__ import annotations

import re

import parser_v14
from struct_v14 import Node, Structurer, render_all

_RREF = re.compile(r"R(\d+)")
_LHS_MULTI = re.compile(r"^(R\d+(?:, R\d+)*) = ")
_LHS_TOP = re.compile(r"^R(\d+)\.\.top = ")
_LHS_TOP_RET = re.compile(r"^return R(\d+)\.\.top\s*$")
_MRET_ARG = re.compile(r"\(\.\.\.multret@(\d+)\.\.\.\)")
_FOR_BIND = re.compile(r"^for (R\d+(?:, R\d+)*) (?:=|in) ")


def _walk_texts(nodes: list[Node]):
    out: list[str] = []

    def walk(lst: list[Node]) -> None:
        for nd in lst:
            if nd.text:
                out.append(nd.text)
            if nd.cond is not None:
                try:
                    out.append(nd.cond.render())
                except Exception:
                    pass
            if nd.header:
                out.append(nd.header)
            if nd.then_:
                walk(nd.then_)
            if nd.else_:
                walk(nd.else_)

    walk(nodes)
    return out


def _has_control_leftovers(nodes: list[Node]) -> bool:
    for t in _walk_texts(nodes):
        s = t.strip()
        if re.search(r"\bgoto\b", s):
            return True
    def labels(lst: list[Node]) -> bool:
        return any(n.kind == "label" for n in lst) or any(
            labels(n.then_) or labels(n.else_) for n in lst)
    if labels(nodes):
        return True
    for t in _walk_texts(nodes):
        if "...multret..." in t and "setlist multret" in t:
            return True
    return False


def _max_reg(nodes: list[Node]) -> int:
    m = -1
    for t in _walk_texts(nodes):
        for x in _RREF.findall(t):
            m = max(m, int(x))
    return m


def _assigned_regs(nodes: list[Node]) -> set[int]:
    out: set[int] = set()

    def walk(lst: list[Node]) -> None:
        for nd in lst:
            if nd.kind == "stmt" and nd.text:
                m = _LHS_MULTI.match(nd.text) or _LHS_TOP.match(nd.text)
                if m:
                    for x in m.group(1).split(", "):
                        out.add(int(x[1:]))
            if nd.then_:
                walk(nd.then_)
            if nd.else_:
                walk(nd.else_)

    walk(nodes)
    return out


def _expand_top(line: str, hi: int) -> str:
    """`R4..top = RHS` -> `R4, R5, ... Rhi = RHS`; same for return form."""
    m = _LHS_TOP.match(line)
    if m:
        a = int(m.group(1))
        regs = ", ".join(f"R{r}" for r in range(a, max(a, hi) + 1))
        return f"{regs} = {line[m.end():]}"
    m = _LHS_TOP_RET.match(line.strip())
    if m:
        a = int(m.group(1))
        regs = ", ".join(f"R{r}" for r in range(a, max(a, hi) + 1))
        return line[:len(line) - len(line.lstrip())] + f"return {regs}"
    return line


def _expand_mret_args(line: str, hi: int) -> str:
    """`f(...multret@N...)` (broken chain) -> `f(RN, ... Rhi)`. Width
    approximation: may pad nils or truncate (documented)."""
    def _one(mo: re.Match) -> str:
        a = int(mo.group(1))
        regs = ", ".join(f"R{r}" for r in range(a, max(a, hi) + 1))
        return f"({regs})"
    return _MRET_ARG.sub(_one, line)


def _expand_node(nd: Node, hi: int) -> None:
    if nd.text and ("..top" in nd.text or "multret@" in nd.text):
        nd.text = _expand_mret_args(_expand_top(nd.text, hi), hi)
    for sub in (nd.then_, nd.else_):
        for c in sub:
            _expand_node(c, hi)


def render_function(bc: parser_v14.Bytecode, proto: parser_v14.Proto,
                    nodes: list[Node], stats: dict | None = None) -> tuple[str, bool]:
    """Returns (source, is_valid_candidate). Invalid candidates still render,
    with a WARNING header, for manual review."""
    import copy
    nodes = copy.deepcopy(nodes)
    bad_flow = _has_control_leftovers(nodes)
    hi = _max_reg(nodes)
    for nd in nodes:
        _expand_node(nd, hi)
    assigned = _assigned_regs(nodes)
    params = [f"R{r}" for r in range(proto.numparams)]
    # Hoist every assigned non-param register, including for-bound ones:
    # a numeric-for index (e.g. `for R6 = R6, ...`) is assigned in the
    # prep and must stay a function-level local so the head init reads
    # the outer value; the for-statement itself opens an inner scope.
    locs = sorted(r for r in assigned if r >= proto.numparams)

    dn = proto.debugname
    label = dn.decode("utf-8", "replace") if isinstance(dn, bytes) else (dn or "??")
    head = [f"-- decompiled P{proto.id} ({label})"]
    if stats:
        head.append(f"-- loops={stats.get('loops', {})} gotos={stats.get('gotos', 0)} "
                    f"unclassified={stats.get('unclassified', 0)}")
    if bad_flow:
        head.append("-- WARNING: irreducible control flow (goto/label/multret-setlist)"
                    " -- NOT valid Luau, needs review")
    sig = ", ".join(params + (["..."] if proto.is_vararg else []))
    head.append(f"local function P{proto.id}({sig})")
    body: list[str] = []
    if locs:
        body.append("  local " + ", ".join(f"R{r}" for r in locs))
    body += ["  " + ln for ln in render_all(nodes)]
    body.append("end")
    return "\n".join(head + body), not bad_flow


def emit_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> tuple[str, bool]:
    st = Structurer(bc, proto)
    res = st.structure()
    return render_function(bc, proto, res.nodes, res.stats)
