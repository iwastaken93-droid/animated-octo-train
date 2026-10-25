"""Deletion sweeps: liveness DCE to a fixpoint, unreachable-shadow drops,
and dead tail-transfer cleanup. These only remove nodes."""
from __future__ import annotations

import time

from struct_node import (Node, _RREF_RE)


class DceMixin:
    """Deletion sweeps: liveness DCE to a fixpoint, unreachable-shadow drops, and dead tail-transfer cleanup. These only remove nodes."""

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
    def _dce_fixpoint(self, nodes: list[Node], tag: str = "dce") -> int:
        """DCE to a fixpoint: each dropped def can orphan the next (the
        reference runs its foldTables/DCE pass 3x interleaved with other
        transforms for the same reason). Single-pass DCE leaves chained
        dead temps behind."""
        total = 0
        while True:
            t0 = time.perf_counter()
            dropped = self._dce(nodes)
            total += dropped
            self._pass_time(f"{tag}(-{dropped})", t0)
            if not dropped:
                return total
    def _drop_unreachable(self, lst: list[Node]) -> int:
        """Drop statements shadowed by an unconditional transfer in the same
        block (reference dropUnreachable 0x12B770 cuts a block after `break` /
        `continue`). A label clears the shadow: statements after `goto L` are
        reachable when a label follows, so only non-label statements with no
        intervening label since the terminator are removed. Recurses first."""
        dropped = 0
        for nd in lst:
            if nd.then_:
                dropped += self._drop_unreachable(nd.then_)
            if nd.else_:
                dropped += self._drop_unreachable(nd.else_)
        shadow = False
        i = 0
        while i < len(lst):
            if lst[i].kind == "label":
                shadow = False
                i += 1
            elif shadow:
                del lst[i]
                dropped += 1
            else:
                if lst[i].kind in self._TERMINAL:
                    shadow = True
                i += 1
        return dropped
    def _cleanup_tails(self, lst: list[Node], tail_return: bool = False,
                       tail_continue: bool = False) -> int:
        """Drop dead-weight tail transfers (Tovek cleanup_returns): a bare
        `return` that is the last line of the function body, and a bare
        `continue` that drains to its loop latch with nothing after it.
        Tail position propagates into a trailing `if`'s branches (a branch
        tail that falls off the loop/function end is equally dead) but
        never into loop bodies for `return` (a return mid-loop stops the
        loop -- dropping it would keep iterating). Loops establish their
        own `continue` context. Valued returns are never touched, and only
        the final node of a list is ever popped, so labels and gotos are
        undisturbed. Each firing deletes a node, so the fixpoint loop
        cannot ping-pong."""
        n = 0
        for nd in lst[:-1] if lst else []:
            if nd.kind == "if":
                n += self._cleanup_tails(nd.then_)
                n += self._cleanup_tails(nd.else_)
            elif nd.kind in self._LOOP_KINDS and nd.then_:
                n += self._cleanup_tails(nd.then_, False, True)
        if not lst:
            return n
        last = lst[-1]
        if tail_return and last.kind == "return" and last.text.strip() == "return":
            lst.pop()
            return n + 1
        if tail_continue and last.kind == "continue" and last.text.strip() == "continue":
            lst.pop()
            return n + 1
        if last.kind == "if":
            n += self._cleanup_tails(last.then_, tail_return, tail_continue)
            n += self._cleanup_tails(last.else_, tail_return, tail_continue)
        elif last.kind in self._LOOP_KINDS and last.then_:
            n += self._cleanup_tails(last.then_, False, True)
        return n
