"""Branch-shape sweeps: guard merging, continue-guard inversion and its recover dual, terminator-guard flattening, common-tail factoring, and nested-guard flattening with its guard_split gate."""
from __future__ import annotations

import re

from struct_node import (Cond, Node, render_all)


class BranchMixin:
    """Branch-shape sweeps: guard merging, continue-guard inversion and its recover dual, terminator-guard flattening, common-tail factoring, and nested-guard flattening with its guard_split gate."""

    def _merge_guards(self, lst: list[Node]) -> int:
        """Merge adjacent `if C1 then X end; if C2 then X end` (identical
        bodies, no elses) into `if C1 or C2 then X end`, mirroring the
        reference mergeGuards: bodies must render identically, and the body
        must end in an unconditional transfer (return/goto/break/continue)
        so the original executes X at most once -- exactly what the merged
        `or` does. Conds from taken_cond/dispatcher are pure (registers and
        constants only), so short-circuit `or` preserves evaluation.
        Recurses into branches first (post-order, like the reference)."""
        merged = 0
        for nd in lst:
            if nd.then_:
                merged += self._merge_guards(nd.then_)
            if nd.else_:
                merged += self._merge_guards(nd.else_)
        i = 0
        while i < len(lst) - 1:
            a, b = lst[i], lst[i + 1]
            if (a.kind == "if" and b.kind == "if"
                    and a.cond is not None and b.cond is not None
                    and not a.else_ and not b.else_
                    and a.then_ and b.then_
                    and a.then_[-1].kind in self._TERMINAL
                    and b.then_[-1].kind in self._TERMINAL
                    and render_all(a.then_) == render_all(b.then_)):
                ca, cb = a.cond.render(), b.cond.render()
                if ca != cb:
                    a.cond = Cond("or", ca, cb)
                    del lst[i + 1]
                    merged += 1
                    continue
            i += 1
        return merged
    def _invert_continue_guards(self, lst: list[Node], tail: bool = False) -> int:
        """Turn a tail-position `if C then continue (else E)? end` into
        `if not C then E? end` (reference invertContinueGuards 0x180156070,
        which fires on an if whose then-branch is exactly one `continue`).
        The `tail` flag threads "nothing executes after this within the
        loop body" (the reference passes it the same way: it only fires on
        a last-statement chain): firing mid-body would let control fall
        through to code the `continue` used to skip. A loop body always
        starts a tail region (its end loops back); an if-branch inherits
        tail only when the if is last in a tail list. Skipped when there is
        nothing to absorb (no else and empty body would make a pointless
        `if not C then end`) and when the condition cannot negate (e.g.
        sweep-built `or` guards)."""
        inverted = 0
        for i, nd in enumerate(lst):
            child_tail = tail and i == len(lst) - 1
            if nd.kind in self._LOOP_KINDS:
                if nd.then_:
                    inverted += self._invert_continue_guards(nd.then_, True)
                if nd.else_:
                    inverted += self._invert_continue_guards(nd.else_, False)
            elif nd.kind == "if":
                if nd.then_:
                    inverted += self._invert_continue_guards(nd.then_, child_tail)
                if nd.else_:
                    inverted += self._invert_continue_guards(nd.else_, child_tail)
                if (child_tail and nd.cond is not None
                        and len(nd.then_) == 1 and nd.then_[0].kind == "continue"
                        and nd.else_):
                    try:
                        neg = nd.cond.negate()
                    except Exception:
                        continue
                    nd.cond = neg
                    nd.then_ = list(nd.else_)
                    nd.else_ = []
                    inverted += 1
        return inverted
    def _recover_guard_continue(self, lst: list[Node]) -> int:
        """Turn a trailing one-sided `if C then BODY end` in a loop body
        into guard-continue form: `if not C then continue end; BODY`
        (reference recoverGuardContinue -- the dual of
        invertContinueGuards, which needs a `continue` then-branch plus an
        else to absorb, so it can never fire here). An `and` cond splits
        into one guard per top-level part, each `if not (part) then
        continue end`. Sound: the guards continue exactly when C is falsy,
        i.e. exactly when the original skipped BODY, and BODY runs in the
        same list position afterwards. Skips when BODY is empty, ends in a
        transfer (terminator-flatten/invert territory), holds labels/gotos
        (splicing must not disturb jump targets), or is already a bare
        `continue` guard (own output). Each firing removes a one-sided
        trailing if, so the fixpoint cannot ping-pong."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._recover_guard_continue(nd.then_)
            if nd.else_:
                n += self._recover_guard_continue(nd.else_)
            if nd.kind in self._LOOP_KINDS and nd.then_:
                body = nd.then_
                last = body[-1]
                if (last.kind == "if" and last.cond is not None
                        and not last.else_ and last.then_
                        and last.then_[-1].kind not in self._TERMINAL
                        and not (len(last.then_) == 1
                                and last.then_[0].kind == "continue")
                        and not self._has_goto_or_label(last.then_)):
                    if last.cond.op == "and":
                        parts: list = [last.cond.l, last.cond.r]
                    else:
                        parts = [None]
                    guards = []
                    for part in parts:
                        if part is None:
                            try:
                                neg = last.cond.negate()
                            except Exception:
                                neg = Cond("truthy",
                                           f"not ({last.cond.render()})")
                        else:
                            neg = Cond("truthy", f"not ({part})")
                        guards.append(Node("if", cond=neg,
                                          then_=[Node("continue", "continue")],
                                          else_=[]))
                    body[-1:] = guards + list(last.then_)
                    n += 1
        return n
    def _flatten_terminator_guards(self, lst: list[Node]) -> int:
        """Turn `if C then X-term end; REST-term` (if with no else, body
        ending in an unconditional transfer, block ending in one) into
        `if not C then REST end; X` (reference flattenTerminatorGuards
        0x180154D00). Sound: both arms terminate, so the original runs X
        then transfers when C is truthy and runs REST then transfers
        otherwise -- exactly what the reordered form does; REST never ran
        straight-line in either version. The reference additionally requires
        the hoisted side to be smaller (a cost heuristic that also prevents
        ping-pong); this uses rendered-size strict `<` the same way, so a
        fired transform can never refire on its own output. Skips when REST
        holds labels/gotos (moving them into a branch would redirect or
        illegalize jumps) and when the condition cannot negate."""
        flat = 0
        for nd in lst:
            if nd.then_:
                flat += self._flatten_terminator_guards(nd.then_)
            if nd.else_:
                flat += self._flatten_terminator_guards(nd.else_)
        i = 0
        while i < len(lst):
            nd = lst[i]
            rest = lst[i + 1:]
            if (nd.kind == "if" and nd.cond is not None and not nd.else_
                    and nd.then_ and nd.then_[-1].kind in self._TERMINAL
                    and rest and rest[-1].kind in self._TERMINAL
                    and not any(x.kind in ("label", "goto") for x in rest)
                    and sum(map(len, render_all(rest)))
                    < sum(map(len, render_all(nd.then_)))):
                try:
                    neg = nd.cond.negate()
                except Exception:
                    i += 1
                    continue
                nd.cond = neg
                nd.then_, xbody = rest, nd.then_
                lst[i + 1:] = xbody
                flat += 1
                i += 1
                continue
            i += 1
        return flat
    def _factor_common_tails(self, lst: list[Node]) -> int:
        """Hoist identical trailing statements shared by both `if` branches
        to after the `end` (Tovek factor_common_tails: structured
        cross-jumping for -O2 continuation clones). Gate: the common
        suffix must match rendered-text exactly per trailing node, hold
        no label nodes, leave both branches non-empty (or empty both,
        dropping the side-effect-free cond), and END in an unconditional
        transfer (return/goto/break/continue) -- the shared continuation
        is guaranteed to transfer control, so hoisting cannot fall
        through into code the original skipped. Recurses post-order so a
        factored inner `if` is visible to the outer comparison; returns
        hoist count. Each firing strictly shrinks branch interiors, so no
        ping-pong is possible."""
        n = 0
        for nd in lst:
            if nd.then_:
                n += self._factor_common_tails(nd.then_)
            if nd.else_:
                n += self._factor_common_tails(nd.else_)
        i = 0
        while i < len(lst):
            nd = lst[i]
            if (nd.kind == "if" and nd.cond is not None
                    and nd.then_ and nd.else_
                    and not (len(nd.else_) == 1 and nd.else_[0].kind == "if")):
                ka, kb = len(nd.then_) - 1, len(nd.else_) - 1
                while (ka >= 0 and kb >= 0
                       and render_all(nd.then_[ka:ka + 1])
                       == render_all(nd.else_[kb:kb + 1])):
                    ka -= 1
                    kb -= 1
                na, nb = len(nd.then_) - 1 - ka, len(nd.else_) - 1 - kb
                k = min(na, nb)
                if k > 0 and nd.then_[-1].kind in self._TERMINAL:
                    tail = nd.then_[len(nd.then_) - k:]
                    if not any(t.kind == "label" for t in tail):
                        if ka < 0 and kb < 0:
                            # branches fully identical: cond reads registers
                            # only (taken_cond/dispatcher shapes), so drop it.
                            lst[i:i + 1] = tail
                            n += 1
                            i += len(tail)
                            continue
                        if ka < 0:
                            # then-branch IS the tail: `if not C then head end`
                            try:
                                nd.cond = nd.cond.negate()
                            except Exception:
                                pass
                            else:
                                nd.then_ = nd.else_[:len(nd.else_) - k]
                                nd.else_ = []
                                lst[i + 1:i + 1] = tail
                                n += 1
                                i += 1 + len(tail)
                                continue
                        elif kb < 0:
                            # else-branch IS the tail: `if C then head end`
                            nd.then_ = nd.then_[:len(nd.then_) - k]
                            nd.else_ = []
                            lst[i + 1:i + 1] = tail
                            n += 1
                            i += 1 + len(tail)
                            continue
                        else:
                            del nd.then_[len(nd.then_) - k:]
                            del nd.else_[len(nd.else_) - k:]
                            lst[i + 1:i + 1] = tail
                            n += 1
                            i += 1 + len(tail)
                            continue
            i += 1
        return n
    _MAX_LIFTED_GUARD = 2  # biggest bail worth lifting (reference bound)
    _GUARD_TERMS = ("return", "break", "continue")  # goto excluded:
    def _block_size(self, nodes: list[Node]) -> int:
        """Recursive statement count (reference block_size): a single but
        deeply nested body still qualifies as non-trivial."""
        n = 0
        for nd in nodes:
            n += 1
            if nd.then_:
                n += self._block_size(nd.then_)
            if nd.else_:
                n += self._block_size(nd.else_)
        return n
    @staticmethod
    def _is_valued_return(nd: Node) -> bool:
        return nd.kind == "return" and nd.text.strip() != "return"
    def _guard_split(self, nd: Node, has_rest: bool, is_elseif: bool):
        """Split one nested `if/else` bail staircase into a guard + inline
        tail (reference guard_split). Returns (guard, inline) or None.
        Never breaks elseif chains outright (chain members only lift under
        the same size rules minus the bare-bail shortcut); both-terminal
        arms only lift when nothing follows (splicing a transfer mid-block
        is invalid Lua). Pulling the else negates the cond, falling back
        to an explicit `not (...)` when it cannot negate cleanly."""
        then_, else_, cond = nd.then_, nd.else_, nd.cond
        if not then_ or not else_ or cond is None:
            return None
        if self._has_goto_or_label(then_) or self._has_goto_or_label(else_):
            return None
        then_term = then_[-1].kind in self._GUARD_TERMS
        else_term = else_[-1].kind in self._GUARD_TERMS
        then_size = self._block_size(then_)
        else_size = self._block_size(else_)
        single_bail = (len(else_) == 1 and else_[-1].kind in self._GUARD_TERMS
                         and not is_elseif)
        pull = None
        if else_term and not then_term:
            if else_size <= self._MAX_LIFTED_GUARD and (then_size > else_size
                                                        or single_bail):
                pull = "else"
        elif then_term and not else_term:
            if then_size <= self._MAX_LIFTED_GUARD and else_size > then_size:
                pull = "then"
        elif else_term and then_term:
            if has_rest:
                return None
            if min(then_size, else_size) > self._MAX_LIFTED_GUARD:
                return None
            if then_size == else_size:
                if (is_elseif or not self._is_valued_return(then_[-1])
                        or not self._is_valued_return(else_[-1])):
                    return None
                pull = "then"
            elif else_size < then_size:
                pull = "else"
            else:
                pull = "then"
        else:
            return None
        if pull is None:
            return None
        if pull == "else":
            try:
                neg = cond.negate()
                readable = cond.op not in ("<", "<=", ">", ">=")
            except Exception:
                neg = Cond("truthy", f"not ({cond.render()})")
                readable = True
            if not readable and then_size < 4:
                return None
            return Node("if", cond=neg, then_=else_, else_=[]), list(then_)
        return Node("if", cond=cond, then_=then_, else_=[]), list(else_)
    def _flatten_guards(self, lst: list[Node], is_else: bool = False) -> int:
        """Turn nested `if c then <body> else <bail> end` staircases into
        early-bail guard clauses (reference flatten_guards). Bottom-up so
        an inlined branch's own staircases are already collapsed; the
        inlined head gets one more look so a whole staircase falls in a
        single pass. Removing an else can only shrink else-counts, which
        no other pass grows, so the fixpoint cannot ping-pong."""
        n = 0
        for nd in lst:
            if nd.kind == "if":
                if nd.then_:
                    n += self._flatten_guards(nd.then_, False)
                if nd.else_:
                    n += self._flatten_guards(nd.else_, True)
            elif nd.kind in self._LOOP_KINDS and nd.then_:
                n += self._flatten_guards(nd.then_, False)
        i = 0
        while i < len(lst):
            nd = lst[i]
            if nd.kind == "if" and nd.cond is not None:
                res = self._guard_split(nd, has_rest=i < len(lst) - 1,
                                        is_elseif=is_else and len(lst) == 1)
                if res is not None:
                    guard, inline = res
                    lst[i:i + 1] = [guard] + inline
                    n += 1
                    i += 1  # past the guard; re-examine the inlined head
                    continue
            i += 1
        return n
