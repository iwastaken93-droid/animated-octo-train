"""Structurer postpasses: DCE, label/goto resolution, structuring sweeps, dispatcher."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError, fmt_const
from ir_v14 import Lifter, IRError

from struct_node import (StructError, FASTCALL_OPS, COND_OPS, Cond, taken_cond, Node, render_all, Loop, StructResult, _RDEF_RE, _RMULTI_LHS_RE, _RTOP_LHS_RE, _RFOR_BIND_RE, _RREF_RE, _RREF_RES, _PURE_DEF_OPS, _NEG)


class SweepMixin:
    """Postpass phase (mixed into Structurer)."""

    def _dce_list(self, lst: list[Node], live: set[int]) -> int:
        """Backward liveness over one statement list. `live` = regs read by
        code after the list (mutated in place to regs read by the list).
        Drops dead pure defs; returns drop count."""
        dropped = 0
        i = len(lst) - 1
        while i >= 0:
            nd = lst[i]
            if nd.then_ or nd.else_:
                if nd.kind in ("while", "repeat", "numfor", "genfor"):
                    # loop-carried: the body may read the previous
                    # iteration's defs, so seed with the body's own refs;
                    # the test cond/header also reads every iteration, so
                    # seed with those too (prefix lines are cleaned as body)
                    bodyrefs = self._subtree_refs(nd.then_)
                    testrefs: set[int] = set()
                    if nd.cond is not None:
                        try:
                            testrefs |= {int(x) for x in _RREF_RE.findall(nd.cond.render())}
                        except Exception:
                            pass
                    if nd.header:
                        testrefs |= self._header_refs(nd.header)
                    self._dce_list(nd.then_, set(live) | bodyrefs | testrefs)
                    live |= bodyrefs | testrefs
                else:
                    # Branch bodies are cleaned with a copy, but regs they
                    # still read must stay live outside: a def used only
                    # inside a branch is live (dropping it dangling-refs
                    # the branch). Refs are collected AFTER the recursive
                    # pass so dropped lines don't keep their own deps alive.
                    if nd.then_:
                        self._dce_list(nd.then_, set(live))
                        live |= self._subtree_refs(nd.then_)
                    if nd.else_:
                        self._dce_list(nd.else_, set(live))
                        live |= self._subtree_refs(nd.else_)
                if nd.cond is not None:
                    try:
                        live |= {int(x) for x in _RREF_RE.findall(nd.cond.render())}
                    except Exception:
                        pass
                if nd.header:
                    live |= self._header_refs(nd.header)
            elif nd.kind == "stmt" and nd.pure and nd.defreg is not None:
                refs = self._value_refs(nd.text)
                if nd.defreg in live:
                    live.discard(nd.defreg)
                    live |= refs
                else:
                    del lst[i]
                    dropped += 1
            else:
                if nd.kind == "stmt":
                    live -= set(self._lhs_defs(nd.text))
                if nd.text:
                    live |= self._value_refs(nd.text)
                if nd.cond is not None:
                    try:
                        live |= {int(x) for x in _RREF_RE.findall(nd.cond.render())}
                    except Exception:
                        pass
            i -= 1
        return dropped
    def _dce(self, nodes: list[Node]) -> int:
        return self._dce_list(nodes, set())
    def _label_of(self, b: int) -> str:
        if b not in self._labels:
            self._labels[b] = f"L{len(self._labels)}"
        return self._labels[b]
    def _goto(self, dst: int, src: int | None = None) -> Node:
        self._gotos.add(dst)
        # Every goto emission is recorded with its source block (sweep
        # roots have no predecessor; logged as -1) so elimination passes
        # and the edge-coverage check see the complete set, not just the
        # subset whose _build step happened to hold a predecessor.
        self.res.goto_edges.append((src if src is not None else -1, dst))
        return Node("goto", f"goto {self._label_of(dst)}")
    def structure(self) -> StructResult:
        """Structure with goto elimination. Plain reducible protos take the
        fast path (single _run_structure, no gotos). Leftover gotos pull
        their targets into the enclosing loop body (absorption rounds),
        then anything left goes to the pc-dispatcher (no FOR ops) or
        stays excluded (FOR + unabsorbable edges)."""
        self._run_structure()
        rounds = 0
        while self.res.goto_edges and rounds < self._ABSORB_ROUNDS:
            if not self._absorb_goto_targets():
                break
            self._run_structure()
            rounds += 1
        if self.res.goto_edges:
            B = {b.id: b for b in self.cfg.blocks}
            has_for = any(
                B[x].last is not None
                and B[x].last.opname in self._FOR_OPS
                for x in self.reachable if x in B)
            if not has_for:
                self._apply_dispatcher()
            # else: FOR proto with leftover gotos: leave excluded as-is.
        return self.res
    def _run_structure(self) -> None:
        self._reset_run_state()
        # Fresh per-run output state: absorption re-runs structure on the
        # same graph (loops/bodies persist, emission restarts from scratch).
        self.res.done = set()
        self.res.goto_edges = []
        self.res.edges = []
        self.res.nodes = []
        self._stops = {}
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
        dropped = self._dce(self._nodes)
        self.res.stats["dce-dropped"] = dropped
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
    _FOR_OPS = frozenset({
        "FORNPREP", "FORNLOOP", "FORGLOOP",
        "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT"})
    _ABSORB_ROUNDS = 10  # body growth is monotone, bounded by proto size
    def _reaches_avoiding(self, src: int, dst: int, avoid: int | None) -> bool:
        """src reaches dst without passing through avoid (None = anywhere)."""
        seen = {src}
        stack = [src]
        while stack:
            x = stack.pop()
            if x == dst:
                return True
            for t in self.succ.get(x, []):
                if t != avoid and t not in seen:
                    seen.add(t)
                    stack.append(t)
        return False
    def _absorbable_into(self, x: int, host: Loop, roles: set[int]) -> bool:
        """Whether block x may join host's loop body: header-dominated,
        reachable from the header without passing the exit, able to drain
        to the exit, and not a loop role (header/latch/prep/exit/FOR op)
        owned by another loop."""
        B = {b.id: b for b in self.cfg.blocks}
        if x not in self.reachable or x in host.body or x in roles:
            return False
        last = B[x].last
        if last is not None and last.opname in self._FOR_OPS:
            return False
        if not self._dominates(self._idom_map, host.header, x):
            return False
        if host.exit is None:
            return False
        if not self._reaches_avoiding(host.header, x, host.exit):
            return False
        # The block must drain out of the body: to the loop exit, or by
        # returning from inside it (a return terminates the iteration).
        if self._reaches_avoiding(x, host.exit, None):
            return True
        return not self.succ.get(x) and last is not None and last.is_return
    def _absorb_goto_targets(self) -> bool:
        """One absorption round over the current goto edges. Returns True
        when at least one block joined a loop body (caller re-structures;
        growth is monotone so rounds terminate)."""
        roles: set[int] = set()
        for L in self.res.loops:
            roles.add(L.header)
            roles |= set(L.latches)
            if L.prep is not None:
                roles.add(L.prep)
            if L.exit is not None:
                roles.add(L.exit)
        changed = False
        for s, t in list(self.res.goto_edges):
            if s < 0 or t == s:
                continue
            # Host: the smallest loop the source is inside of (target
            # outside), or the loop the target exits (source still outside
            # the loop whose break-stop governs it, e.g. a late join).
            host = None
            for L in sorted(self.res.loops, key=lambda L: len(L.body)):
                if t == L.exit and s not in L.body:
                    host = L
                    break
                if s in L.body and t not in L.body:
                    host = L
                    break
            if host is None or host.exit is None:
                continue
            # closure: the outside endpoint (target, or the source when it
            # is the one outside the host) plus predecessors back to the
            # body; every link must be absorbable or the whole endpoint is
            # refused (a partial pull would only relocate the goto).
            fringe = [t if s in host.body else s]
            want: set[int] = set()
            ok = True
            while fringe:
                x = fringe.pop()
                if x in host.body or x in want:
                    continue
                if not self._absorbable_into(x, host, roles):
                    ok = False
                    break
                want.add(x)
                fringe.extend(self.pred.get(x, []))
            if ok and want:
                host.body |= want
                changed = True
        return changed
    def _apply_dispatcher(self) -> bool:
        """Whole-proto pc-dispatcher over reachable blocks. Valid Luau by
        construction (no labels/gotos): each block's straight-line stmts
        run under `if _pc == N`, transfers assign _pc. Faithful: it is the
        CFG itself. Requires no FOR loop ops (their init semantics live in
        _emit_loop); refuses otherwise, leaving the proto excluded.
        Returns True when the dispatcher was emitted."""
        B = {b.id: b for b in self.cfg.blocks}
        for b in self.reachable:
            last = B[b].last
            if last is not None and last.opname in self._FOR_OPS:
                self.res.stats["tier"] = "dispatcher-refused-for"
                return False
        order = sorted(self.reachable, key=lambda x: B[x].start_pc)
        chain: list[Node] = [Node("break", "break")]  # unknown _pc: exit
        for b in reversed(order):
            body = self._stmts(B[b])
            returns = bool(body) and body[-1].kind == "return"
            ss = self.succ.get(b, [])
            if not returns:
                if len(ss) == 1:
                    body.append(Node("stmt", f"_pc = {ss[0]}"))
                elif len(ss) == 2:
                    last = B[b].last
                    if last is None or last.opname not in COND_OPS:
                        self.res.stats["tier"] = "dispatcher-refused-shape"
                        return
                    cond = taken_cond(last, self.bc, self.proto)
                    body.append(Node(
                        "if", cond=cond,
                        then_=[Node("stmt", f"_pc = {ss[0]}")],
                        else_=[Node("stmt", f"_pc = {ss[1]}")]))
                elif len(ss) == 0:
                    # no successor without a return: cannot happen per
                    # codegen; break rather than spin.
                    body.append(Node("break", "break"))
                else:
                    self.res.stats["tier"] = "dispatcher-refused-shape"
                    return False
            for src, dst in [(b, t) for t in ss]:
                self._edge(src, dst, "dispatch")
            eq = Cond("==", "_pc", str(b))
            chain = [Node("if", cond=eq, then_=body, else_=chain)]
        disp = [Node("stmt", f"local _pc = {self.cfg.entry}"),
                Node("while", cond=Cond("truthy", "true"), then_=chain)]
        self._dce(disp)
        self.res.nodes = disp
        self.res.goto_edges = []
        self._gotos = set()
        # Loops are intentionally not structured in dispatcher mode (the
        # dispatcher interprets the CFG directly), so detection leftovers
        # must not count as unclassified.
        self.res.unclassified = []
        self.res.done = set(self.reachable)
        kinds: dict[str, int] = {}
        for L in self.res.loops:
            kinds[L.kind] = kinds.get(L.kind, 0) + 1
        edgekinds: dict[str, int] = {}
        for _, _, k in self.res.edges:
            edgekinds[k] = edgekinds.get(k, 0) + 1
        self.res.stats = {
            "loops": kinds, "edges": edgekinds,
            "unclassified": len(self.res.unclassified),
            "gotos": 0, "tier": "dispatcher",
            "done": len(self.res.done), "reachable": len(self.reachable),
            "skipped": len(self.res.skipped),
        }
        return True
