"""Structurer emission: statement expansion, loop rendering, edge helpers."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError, fmt_const
from ir_v14 import Lifter, IRError

from struct_node import (StructError, FASTCALL_OPS, COND_OPS, Cond, taken_cond, Node, render_all, Loop, StructResult, _RDEF_RE, _RMULTI_LHS_RE, _RTOP_LHS_RE, _RFOR_BIND_RE, _RREF_RE, _RREF_RES, _PURE_DEF_OPS, _NEG)


class EmitMixin:
    """Emission phase (mixed into Structurer)."""

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
            for ln in self.pclines.get(ins.pc, []):
                # LOADB skip-jumps are unconditional in the CFG (single
                # successor); the leftover `goto` line would be spurious
                if ln.endswith("-- loadb skip"):
                    continue
                nd = Node("stmt", ln)
                if ins.opname in _PURE_DEF_OPS and ln.startswith(f"R{ins.A} ="):
                    m = _RDEF_RE.match(ln)
                    if m and int(m.group(1)) == ins.A:
                        nd.pure, nd.defreg = True, ins.A
                out.append(nd)
        return out
    @staticmethod
    def _lhs_defs(text: str) -> list[int]:
        """Registers defined (killed) by a stmt line's LHS, if any."""
        m = _RMULTI_LHS_RE.match(text)
        if m:
            return [int(x[1:]) for x in m.group(1).split(", ")]
        m = _RTOP_LHS_RE.match(text)
        if m:
            return [int(m.group(1))]
        return []
    @staticmethod
    def _value_refs(text: str) -> set[int]:
        """All R# read as *values*. Only the LHS occurrence itself is
        excluded -- `R0 = R0 + 1` genuinely reads R0."""
        m = _RMULTI_LHS_RE.match(text) or _RTOP_LHS_RE.match(text)
        rhs = text[m.end():] if m else text
        return {int(x) for x in _RREF_RE.findall(rhs)}
    @staticmethod
    def _header_refs(header: str) -> set[int]:
        """Regs a loop header reads. The bound loop var(s) (`for R7 =`
        / `for R7, R8 in`) are definitions, not reads -- but everything
        past `=`/`in` (init/limit/step, iterator triple) is read,
        including an init that reuses the bound register
        (`for R6 = R6, ...`)."""
        m = _RFOR_BIND_RE.match(header)
        if m:
            return {int(x) for x in _RREF_RE.findall(header[m.end():])}
        return {int(x) for x in _RREF_RE.findall(header)}
    def _subtree_refs(self, nodes: list[Node]) -> set[int]:
        out: set[int] = set()
        def walk(lst: list[Node]) -> None:
            nonlocal out
            for nd in lst:
                if nd.text:
                    out |= self._value_refs(nd.text)
                if nd.cond is not None:
                    try:
                        out |= {int(x) for x in _RREF_RE.findall(nd.cond.render())}
                    except Exception:
                        pass
                if nd.header:
                    out |= self._header_refs(nd.header)
                if nd.then_:
                    walk(nd.then_)
                if nd.else_:
                    walk(nd.else_)
        walk(nodes)
        return out
    def _edge(self, src: int, dst: int, kind: str) -> None:
        self.res.edges.append((src, dst, kind))
    def _break_reaches_outer(self, target: int, stops: dict[int, str]) -> bool:
        """Would `break` (of the loop owning `stops`) land on `target`?

        `break` resumes output structuring at this loop's exit block; from
        there the enclosing frames structure straight-line successors
        inline. If some exit reaches `target` through single-successor
        blocks that emit no statements (bare JUMP/JUMPBACK trampolines the
        compiler leaves between a loop end and an outer latch), the break
        path executes exactly the target block -- the same as the original
        branch -- so `break` is exact and `continue` (inner loop) is wrong.
        Returns False when the exit flow hits real statements, joins,
        loop roles, or already-structured blocks: the branch then needs
        the historical `continue` (documented in LIMITATIONS.md).
        """
        exits = [b for b, a in stops.items() if a == "break"]
        if not exits:
            return False
        B = {b.id: b for b in self.cfg.blocks}
        for start in exits:
            if start == target:
                return True
            cur = start
            seen = {start}
            while True:
                nxts = self.succ.get(cur, [])
                if len(nxts) != 1:
                    break
                nxt = nxts[0]
                if nxt == target:
                    return True
                if nxt in seen or nxt in self._role_blocks:
                    break
                if nxt in self.res.done:
                    break
                nb = B.get(nxt)
                if nb is None or self._stmts(nb):
                    break
                seen.add(nxt)
                cur = nxt
        return False
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
            # A body-entry that is itself an innermost latch must still be
            # structured (it carries body control flow); only OUTER latches
            # are implicit continues here. The innermost set is `latches`
            # (= stack top inside a loop body) and handled above/by fallthrough.
            # Break-first: when the current loop's exit reaches this outer
            # latch through effect-free trampolines (or IS it, when the
            # compiler fuses endLabel with the latch block), `break` lands
            # exactly on the branched-to block. Only the exit stop and the
            # reachability walk take precedence: a branch to the header
            # still uses this rule (it emits `continue`, identical to the
            # header stop), and falling through to generic block
            # structuring for a header target would miscompile FORGLOOP
            # headers as plain gotos. Walk failures keep the historical
            # `continue` (documented in LIMITATIONS.md).
            if any(cur in s for s in self._latch_stack[:-1] if s is not latches):
                # A "fallthrough" stop is the merge path's flow-through
                # marker, not a real action: treat it as absent here (the
                # merge block is still structured by the enclosing frame).
                _stop = stops.get(cur)
                if _stop == "break" or ((_stop is None or _stop == "fallthrough") and self._break_reaches_outer(cur, stops)):
                    out.append(Node("break", "break"))
                    if prev is not None:
                        self._edge(prev, cur, "break-outer")
                    break
                out.append(Node("continue", "continue"))
                if prev is not None:
                    self._edge(prev, cur, "continue-outer")
                break
            if cur not in self.reachable:
                out.append(self._goto(cur, prev if prev is not None else entry))
                if prev is not None:
                    self._edge(prev, cur, "goto-unreach")
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
                out.append(self._goto(cur, prev if prev is not None else entry))
                if prev is not None:
                    self._edge(prev, cur, "goto")
                break
            if confine is not None and cur not in confine and cur not in stops:
                out.append(self._goto(cur, prev if prev is not None else entry))
                if prev is not None:
                    self._edge(prev, cur, "goto-confine")
                break
            if cur in self.res.done:
                out.append(self._goto(cur, prev if prev is not None else entry))
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
                out.append(self._goto(ss[0], cur))
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
                # A taken side absorbed as the active loop latch continues the
                # loop; a terminating else side works negated (swap sides).
                _snap = (set(self.res.done), dict(self._anchors), set(self._emitted_loops),
                         set(self._gotos), dict(self._labels), len(self.res.edges),
                         len(self.res.goto_edges))
                then = self._build(ss[0], dict(stops), latches, confine)
                if ss[0] in latches and self._absorbable(ss[0]) and not (
                        then and then[-1].kind in ("return", "goto", "break", "continue")):
                    then = list(then) + [Node("continue", "continue")]
                if not (then and then[-1].kind in ("return", "goto", "break", "continue")):
                    (self.res.done, self._anchors, self._emitted_loops,
                     self._gotos, self._labels, n_edges, n_gedges) = (
                        _snap[0], _snap[1], _snap[2], _snap[3], _snap[4], _snap[5], _snap[6])
                    del self.res.edges[n_edges:]
                    del self.res.goto_edges[n_gedges:]
                    els = self._build(ss[1], dict(stops), latches, confine)
                    if ss[1] in latches and self._absorbable(ss[1]) and not (
                            els and els[-1].kind in ("return", "goto", "break", "continue")):
                        els = list(els) + [Node("continue", "continue")]
                    if els and els[-1].kind in ("return", "goto", "break", "continue"):
                        out += self._stmts(b)
                        if prev is not None:
                            self._edge(prev, cur, "seq-in")
                        self._edge(cur, ss[1], "if-then-term")
                        self._edge(cur, ss[0], "if-else-inline")
                        out.append(Node("if", cond=cond.negate(), then_=els, else_=[]))
                        self.res.stats["if-term"] = self.res.stats.get("if-term", 0) + 1
                        prev, cur = cur, ss[0]
                        continue
                    (self.res.done, self._anchors, self._emitted_loops,
                     self._gotos, self._labels, n_edges, n_gedges) = (
                        _snap[0], _snap[1], _snap[2], _snap[3], _snap[4], _snap[5], _snap[6])
                    del self.res.edges[n_edges:]
                    del self.res.goto_edges[n_gedges:]
                    then = []
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
                out.append(Node("stmt", f"if {cond.render()} then {self._goto(ss[0], cur).text}"))
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
            # A side absorbed as the active loop latch loops back: the
            # merge point is not a join for it, so say continue instead
            # of falling through to a spurious action there.
            if ss[0] in latches and self._absorbable(ss[0]) and not (
                    then and then[-1].kind in ("return", "goto", "break", "continue")):
                then = list(then) + [Node("continue", "continue")]
            if ss[1] in latches and self._absorbable(ss[1]) and not (
                    els and els[-1].kind in ("return", "goto", "break", "continue")):
                els = list(els) + [Node("continue", "continue")]
            if not then and els:
                cond, then, els = cond.negate(), els, then
            out.append(Node("if", cond=cond, then_=then, else_=els))
            term = ("return", "goto", "break", "continue")
            if ((then and then[-1].kind in term)
                    and (els and els[-1].kind in term)):
                # both sides terminate: nothing falls through to the merge
                # (a latch side loops back); end the chunk instead of
                # re-processing the merge into a spurious action.
                break
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
                and self._jump_to(latch, last.jump_target, L0.header)):
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
            # Numeric-for layout is [limit, step, index, variable]: the
            # compiler aliases the user loop variable to the index slot
            # R(A+2), allocating a separate R(A+3) only when the body
            # writes the loop variable (then the body opens with
            # MOVE R(A+3), R(A+2) each iteration). Bind whichever slot the
            # body actually uses; the for-statement itself performs the
            # per-iteration copy, so a leading copy-MOVE is dropped.
            varreg = A + 2
            entry_blk = B.get(L.body_entry)
            if (entry_blk is not None and entry_blk.insns
                    and entry_blk.insns[0].opname == "MOVE"
                    and entry_blk.insns[0].A == A + 3
                    and entry_blk.insns[0].B == A + 2):
                varreg = A + 3
                mvpc = entry_blk.insns[0].pc
                mvlines = self.pclines.get(mvpc, [])
                if len(mvlines) == 1 and mvlines[0].strip() == f"R{A + 3} = R{A + 2}":
                    del self.pclines[mvpc]
            head = f"for R{varreg} = R{A + 2}, R{A}, R{A + 1} do"
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
            # The variable count lives in the low 8 bits of the FORGLOOP
            # aux, but FORGLOOP terminates the header or the latch -- not
            # necessarily the latch. Match this loop's own FORGLOOP by its
            # register (== prep A) and a backedge into the loop body.
            v = 1
            for _b in (B.get(L.header), B.get(L.body_entry), B.get(L.latch)):
                _last = _b.last if _b is not None else None
                if (_last is not None and _last.opname == "FORGLOOP"
                        and _last.A == A and _last.jump_target is not None
                        and self._block_of_pc(_last.jump_target) in L.body):
                    v = max((_last.aux or 0) & 0xFF, 1)
                    break
            vars_ = ", ".join(f"R{r}" for r in range(A + 3, A + 3 + v))
            head = f"for {vars_} in R{A}, R{A + 1}, R{A + 2} do"
            test = L.header if B[L.header].last is not None and B[L.header].last.opname == "FORGLOOP" else L.latch
            if test in self.succ[L.prep]:  # type: ignore
                self._edge(L.prep, test, "loop-entry")  # type: ignore
            # test-at-top genfor: leading header stmts run before the test
            # each iteration (the header block is absorbed)
            gtop = None
            if (B[L.header].last is not None
                    and B[L.header].last.opname == "FORGLOOP"
                    and L.header != L.body_entry):
                gtop = self._stmts(B[L.header]) or None
            body = self._loop_body(L, prefix=gtop)
            self._edge(L.latch, L.header, "loop-back")
            self._edge(L.latch, L.exit, "loop-exit")
            return pre + pre_st + [Node("genfor", header=head, then_=body)]
        if L.kind == "while":
            hlast = B[L.header].last
            assert hlast is not None
            self._edge(L.header, L.body_entry, "loop-entry")
            # Leading header stmts run every iteration BEFORE the test
            # (including the first pass); the header block itself is
            # absorbed. Prefixing them under `while C` would test stale
            # regs on entry and skip them on a false first test, so use
            # a while-true + break guard that preserves the order.
            # (_stmts already skips the test insn itself.)
            htop = self._stmts(B[L.header])
            if htop:
                taken = taken_cond(hlast, self.bc, self.proto)
                guard = Node("if", cond=taken, then_=[Node("break", "break")])
                body = self._loop_body(L, prefix=htop + [guard])
                self._edge(L.latch, L.header, "loop-back")
                self._edge(L.header, L.exit, "loop-exit")
                return pre + [Node("while", cond=Cond("truthy", "true"),
                                    then_=body)]
            cond = taken_cond(hlast, self.bc, self.proto).negate()
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
        if not prefix:
            return body
        combined = list(prefix) + body
        # Anchors recorded during _build point into `body`; repoint them
        # into the combined prefix+body list or post-pass labels land stale.
        off = len(prefix)
        for blk, (lst, pos) in list(self._anchors.items()):
            if lst is body:
                self._anchors[blk] = (combined, off + pos)
        return combined
    def _after_loop(self, L: Loop) -> int | None:
        return L.exit
