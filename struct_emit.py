"""Structurer emission: statement expansion, loop/branch rendering,
goto/label edges, and the dispatcher fallback."""
from __future__ import annotations

from cfg_v14 import Block

from struct_node import (FOR_OPS, StructError, COND_OPS, Cond, taken_cond,
                         Node, Loop, _RDEF_RE,
                         _RMULTI_LHS_RE, _RTOP_LHS_RE, _RFOR_BIND_RE,
                         compound_match, _RREF_RE, _PURE_DEF_OPS)


class EmitMixin:
    """Emission phase (mixed into Structurer). Single owner of rendered
    output: statement expansion, loop/branch emission, goto/label edges,
    and the whole-proto dispatcher fallback."""

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
        m = compound_match(text)
        if m:
            return [int(m.group(1))]
        m = _RTOP_LHS_RE.match(text)
        if m:
            return [int(m.group(1))]
        return []
    @staticmethod
    def _value_refs(text: str) -> set[int]:
        """All R# read as *values*. Only the LHS occurrence itself is
        excluded -- `R0 = R0 + 1` genuinely reads R0. A compound
        `R0 += K` reads R0 too, so its LHS counts as a read."""
        m = _RMULTI_LHS_RE.match(text) or _RTOP_LHS_RE.match(text)
        if m:
            rhs = text[m.end():]
            return {int(x) for x in _RREF_RE.findall(rhs)}
        m = compound_match(text)
        if m:
            lhs = int(m.group(1))
            rhs = text[m.end():]
            return {lhs} | {int(x) for x in _RREF_RE.findall(rhs)}
        return {int(x) for x in _RREF_RE.findall(text)}
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
    def _emit_term_if(self, out: list[Node], b: Block, prev: int | None,
                      cur: int, cond: Cond, ss: list[int], arm: list[Node],
                      negate: bool) -> int:
        """Emit a terminator-style `if` for one arm and return the block
        the frame continues at. Single owner for the four call sites."""
        out += self._stmts(b)
        if prev is not None:
            self._edge(prev, cur, "seq-in")
        if negate:
            self._edge(cur, ss[1], "if-then-term")
            self._edge(cur, ss[0], "if-else-inline")
            out.append(Node("if", cond=cond.negate(), then_=arm, else_=[]))
        else:
            self._edge(cur, ss[0], "if-then-term")
            self._edge(cur, ss[1], "if-else-inline")
            out.append(Node("if", cond=cond, then_=arm, else_=[]))
        self.res.stats["if-term"] = self.res.stats.get("if-term", 0) + 1
        return ss[0] if negate else ss[1]
    def _close_latch_arm(self, root: int, arm: list[Node],
                         latches: frozenset) -> list[Node]:
        """A side absorbed as the active loop latch loops back: the merge
        point is not a join for it, so say continue instead of falling
        through to a spurious action there. Single owner for the six
        arm-closing sites."""
        if (root in latches and self._absorbable(root) and not (
                arm and arm[-1].kind in ("return", "goto", "break", "continue"))):
            return list(arm) + [Node("continue", "continue")]
        return arm
    def _build_cond(self, out: list[Node], b: Block, prev: int | None,
                    cur: int, stops: dict[int, str], latches: frozenset,
                    confine: set[int] | None, cond: Cond,
                    ss: list[int]) -> tuple[bool, int | None, int | None]:
        """Structure one conditional diamond at cur: terminator-style ifs,
        loop-aware join-merge, or the goto fallback. Returns (stop, prev,
        cur): stop ends the current chunk, else the frame continues at cur.
        Owns all branch policy so _build stays a traversal loop."""
        m = self.ipdom.get(cur)
        join_m: int | None = None
        if m is None or m not in self.reachable:
            # no join: try terminator-style `if C then <taken> end` when the
            # taken region cannot fall through (early returns, breaks...).
            # A taken side absorbed as the active loop latch continues the
            # loop; a terminating else side works negated (swap sides).
            _snap = self._take_run_snapshot()
            n_edges, n_gedges = _snap[5], _snap[6]
            then = self._close_latch_arm(
                ss[0], self._build(ss[0], dict(stops), latches, confine),
                latches)
            # Goto-free terminator shapes keep their exact form; only
            # strand-embedding (or unterminated) attempts reroute.
            then_ok = (then and then[-1].kind in ("return", "goto", "break", "continue")
                       and len(self.res.goto_edges) == n_gedges)
            els_gotoy = False
            if not (then and then[-1].kind in ("return", "goto", "break", "continue")):
                self._restore_run_snapshot(_snap)
                els = self._close_latch_arm(
                    ss[1], self._build(ss[1], dict(stops), latches, confine),
                    latches)
                if (els and els[-1].kind in ("return", "goto", "break", "continue")
                        and len(self.res.goto_edges) == n_gedges):
                    prev, cur = cur, self._emit_term_if(
                        out, b, prev, cur, cond, ss, els, True)
                    return False, prev, cur
                els_gotoy = bool(els and els[-1].kind in ("return", "goto", "break", "continue"))
                self._restore_run_snapshot(_snap)
            if then_ok:
                prev, cur = cur, self._emit_term_if(
                    out, b, prev, cur, cond, ss, then, False)
                return False, prev, cur
            # Strand-embedding or unterminated attempt: restore it and
            # prefer a join continuation over the goto fallback.
            self._restore_run_snapshot(_snap)
            jn = self._loop_join(cur, ss, stops, latches, confine)
            if jn is not None:
                m = jn
                join_m = jn
                self.res.stats["joins-merged"] = self.res.stats.get("joins-merged", 0) + 1
            elif then and then[-1].kind in ("return", "goto", "break", "continue"):
                # Goto-y taken side, no join: rebuild this exact shape
                # (_build is deterministic) and keep it verbatim.
                then = self._close_latch_arm(
                    ss[0], self._build(ss[0], dict(stops), latches, confine),
                    latches)
                prev, cur = cur, self._emit_term_if(
                    out, b, prev, cur, cond, ss, then, False)
                return False, prev, cur
            elif els_gotoy:
                els = self._close_latch_arm(
                    ss[1], self._build(ss[1], dict(stops), latches, confine),
                    latches)
                prev, cur = cur, self._emit_term_if(
                    out, b, prev, cur, cond, ss, els, True)
                return False, prev, cur
            else:
                out += self._stmts(b)
                self.res.stats["fallback-nomerge"] = self.res.stats.get("fallback-nomerge", 0) + 1
                out.append(Node("stmt", f"if {cond.render()} then {self._goto(ss[0], cur).text}"))
                self._edge(cur, ss[0], "goto-cond")
                if prev is not None:
                    self._edge(prev, cur, "seq-in")
                self._edge(cur, ss[1], "seq")
                prev, cur = cur, ss[1]
                return False, prev, cur
        if prev is not None:
            self._edge(prev, cur, "merge-out")
        self._edge(cur, ss[0], "if-then")
        self._edge(cur, ss[1], "if-else")
        out += self._stmts(b)
        stops2 = dict(stops)
        if m not in stops2:
            stops2[m] = "fallthrough"
        if join_m is not None:
            self._merge_arm_depth += 1
        try:
            then = self._build(ss[0], stops2, latches, confine)
            els = self._build(ss[1], stops2, latches, confine)
        finally:
            if join_m is not None:
                self._merge_arm_depth -= 1
        then = self._close_latch_arm(ss[0], then, latches)
        els = self._close_latch_arm(ss[1], els, latches)
        if not then and els:
            cond, then, els = cond.negate(), els, then
        out.append(Node("if", cond=cond, then_=then, else_=els))
        term = ("return", "goto", "break", "continue")
        if ((then and then[-1].kind in term)
                and (els and els[-1].kind in term)):
            # both sides terminate: nothing falls through to the merge
            # (a latch side loops back); end the chunk instead of
            # re-processing the merge into a spurious action.
            return True, prev, cur
        prev, cur = cur, m
        return False, prev, cur
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
                if self._merge_arm_depth:
                    # Merge arms need an explicit loop-back: silent absorb
                    # would fall off the arm into the merge continuation.
                    out.append(Node("continue", "continue"))
                    if prev is not None:
                        self._edge(prev, cur, "continue-latch")
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
                if cur not in self.res.done:
                    last = B[cur].last if cur in B else None
                    if (last is not None and last.is_return
                            and not self.succ.get(cur)):
                        # Return block just past the region edge (loop-exit
                        # adjacent): inline it as the arm end instead of a
                        # confine goto that can never anchor a label. The
                        # arm terminates here; nothing falls through.
                        self._anchors.setdefault(cur, (out, len(out)))
                        self.res.done.add(cur)
                        out += self._stmts(B[cur])
                        if prev is not None:
                            self._edge(prev, cur, "seq-in")
                        break
                out.append(self._goto(cur, prev if prev is not None else entry))
                if prev is not None:
                    self._edge(prev, cur, "goto-confine")
                break
            if cur in self.res.done:
                s1 = self.succ.get(cur, [])
                if (len(s1) == 1 and cur not in latches
                        and cur not in self.latch_loop
                        and cur not in self.loop_by_header
                        and cur not in self.loop_by_prep
                        and stops.get(s1[0]) in ("fallthrough", "break", "continue")):
                    dup = self._stmts(B[cur])
                    if len(dup) <= 3:
                        # Cross-diamond shared arm already emitted elsewhere:
                        # duplicate its few straight-line statements inline
                        # (node-split in the emitter: no CFG growth) and run
                        # the merge continuation instead of stranding. The
                        # stop-guarded successor ends this arm, so no chain.
                        out += dup
                        if prev is not None:
                            self._edge(prev, cur, "dup-inline")
                        self._edge(cur, s1[0], "dup-flow")
                        self.res.stats["dups-inlined"] = self.res.stats.get("dups-inlined", 0) + 1
                        prev, cur = cur, s1[0]
                        continue
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
            stop, prev, cur = self._build_cond(
                out, b, prev, cur, stops, latches, confine, cond, ss)
            if stop:
                break
            continue
        return out
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
            others = [t for t in self.succ.get(L.header, [])
                      if t != L.body_entry and t != L.exit]
            if others and len(self.succ.get(L.header, [])) == 2:
                # The header was pre-marked done above (old path absorbs
                # it); this path structures it, so unmark for the frame.
                self.res.done.discard(L.header)
                # Header fans out to two body regions: the non-entry arm
                # would never be traversed (it is not the exit either),
                # stranding it for the context-free sweep as gotos to the
                # latch. Start the body frame at the header itself (the
                # first-visit rule bypasses its own continue-stop) so the
                # header's branch goes through the standard ipdom-merge
                # above: both arms plus the join continuation, latch
                # sides as `continue`, all with loop stops.
                for arm in self.succ[L.header]:
                    self._edge(L.header, arm, "loop-inner"
                               if arm in L.body else "loop-exit")
                # prefix=None: the frame structures the header itself
                # (its stmts emit once in the diamond paths below); the
                # old `top` prefix would emit them a second time.
                body = self._loop_body(L, prefix=None, start=L.header)
                self._edge(L.latch, L.header, "loop-back")
                return pre + [Node("while", cond=Cond("truthy", "true"),
                                        then_=body)]
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
    def _loop_body(self, L: Loop, prefix: list[Node] | None = None,
                   start: int | None = None) -> list[Node]:
        stops: dict[int, str] = {}
        if L.exit is not None:
            stops[L.exit] = "break"
        stops[L.header] = "continue"
        self._latch_stack.append(L.latches)
        try:
            body = self._build(start if start is not None else L.body_entry,
                               stops, L.latches, set(L.body))
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
            if last is not None and last.opname in FOR_OPS:
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
                        return False
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
