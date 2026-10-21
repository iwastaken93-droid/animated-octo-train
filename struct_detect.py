"""Structurer CFG detection: graph setup, loop finding, and loop classification."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError, fmt_const
from ir_v14 import Lifter, IRError

from struct_node import (StructError, FASTCALL_OPS, COND_OPS, Cond, taken_cond, Node, render_all, Loop, StructResult, _RDEF_RE, _RMULTI_LHS_RE, _RTOP_LHS_RE, _RFOR_BIND_RE, _RREF_RE, _RREF_RES, _PURE_DEF_OPS, _NEG)


class DetectMixin:
    """Graph/detection phase (mixed into Structurer; needs core run state)."""

    def _setup_graph(self) -> None:
        """(Re)build struct-graph derivations from self.cfg.blocks.
        Called once at init. The Lifter output (pclines) is pc-keyed;
        pc2b keeps the pc->block mapping."""
        self.pc2b = {}
        for b in self.cfg.blocks:
            for ins in b.insns:
                if ins.pc not in self.pc2b:
                    self.pc2b[ins.pc] = b.id
        self.succ = {}
        for b in self.cfg.blocks:
            self.succ[b.id] = self._struct_succ(b)
        self._refresh_derived()
    def _refresh_derived(self) -> None:
        """Reachability, ranks, loops, postdoms from the current succ map."""
        n = len(self.cfg.blocks)
        self.pred = {b.id: [] for b in self.cfg.blocks}
        for b in self.cfg.blocks:
            for t in self.succ.get(b.id, []):
                if t in self.pred:
                    self.pred[t].append(b.id)
        # reachability on the struct graph.
        self.reachable = set()
        stack = [self.cfg.entry]
        while stack:
            x = stack.pop()
            if x in self.reachable:
                continue
            self.reachable.add(x)
            stack += self.succ.get(x, [])
        # RPO ranks (struct graph)
        self.rank = {}
        seen: set[int] = set()
        order: list[int] = []

        def visit(x: int) -> None:
            seen.add(x)
            for t in self.succ.get(x, []):
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
        self._idom_map = idom
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
        # Every block that owns loop structure (headers, latches, preps,
        # exits, body entries): the break-reachability walk must not pass
        # through these, or the output would diverge into structured code.
        roles: set[int] = set()
        for L in self.res.loops:
            roles.add(L.header)
            roles.add(L.latch)
            roles.update(L.latches)
            roles.add(L.body_entry)
            if L.prep is not None:
                roles.add(L.prep)
            if L.exit is not None:
                roles.add(L.exit)
        self._role_blocks = roles
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
    def _jump_to(self, b: int, target_pc: int, header: int) -> bool:
        """Block b transfers to `header` via a jump to target_pc: the
        struct successor map is the ground truth, pc confirms the edge."""
        if self._block_of_pc(target_pc) == header:
            return True
        B = {x.id: x for x in self.cfg.blocks}
        return (header in self.succ.get(b, [])
                and B[header].start_pc == target_pc)
    def _cond_targets_header(self, b: int, header: int) -> bool:
        B = {x.id: x for x in self.cfg.blocks}
        last = B[b].last
        return (last is not None and last.opname in COND_OPS
                and last.jump_target is not None
                and self._jump_to(b, last.jump_target, header))
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
        # Bottom-test FOR loops win over a header conditional: the header's
        # test is then the first if inside the body (e.g. continue/break
        # guards), not the loop test. Skip header-cond rules in that case so
        # classification falls through to the FORNLOOP/FORGLOOP logic below.
        # (A FORGLOOP header keeps its genfor test-at-top interpretation.)
        _latch_is_for = (len(latches) == 1 and B[latches[0]].last is not None
                         and B[latches[0]].last.opname in ("FORNLOOP", "FORGLOOP")
                         and back_to_header(latches[0]))
        if hop in COND_OPS and len(hs) == 2 and all(back_to_header(l) for l in latches)                 and not _latch_is_for:
            E, F = hs[0], hs[1]
            if E not in body and F in body:
                return Loop("while", header, lat if lat >= 0 else latches[0],
                            frozenset(latches), body, None, E, F)
            if E in body and F in body:
                outs = self._outside_exits(body)
                if not outs:
                    return None
                return Loop("whiletrue", header, lat if lat >= 0 else latches[0],
                            frozenset(latches), body, None, sorted(outs)[0], F)
            return None
        if hop == "JUMP" and len(hs) == 1 and hs[0] in body                 and all(back_to_header(l) for l in latches)                 and not _latch_is_for:
            outs = self._outside_exits(body)
            if not outs:
                return None
            return Loop("whiletrue", header, lat if lat >= 0 else latches[0],
                        frozenset(latches), body, None, sorted(outs)[0], hs[0])

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
                # latch exit may reach the prep's zero-trip exit via a
                # trampoline (e.g. latch -> JUMP block -> prep target).
                # Chase unconditional jumps, then fall back to entry-keyed
                # lookup (the prep whose fallthrough is the header).
                target = self._chase_trampoline(outs[0], body)
                if target is not None and target != outs[0]:
                    P = self._numfor_prep(target, header, body)
                if P is None:
                    P = self._numfor_prep_by_entry(header, body)
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
        # The prep whose jump target is the latch exit (zero-trip path).
        preps = [b.id for b in self.cfg.blocks
                 if b.id in self.reachable
                 and b.last is not None
                 and b.last.opname == "FORNPREP"
                 and b.last.jump_target is not None
                 and self._block_of_pc(b.last.jump_target) == exit_b]
        return preps[0] if len(preps) == 1 else None
    def _numfor_prep_by_entry(self, header: int, body: set[int]) -> int | None:
        # The prep falling through to the header (alternate ownership
        # proof for numeric-for headers).
        preps = [b.id for b in self.cfg.blocks
                 if b.id in self.reachable
                 and b.last is not None
                 and b.last.opname == "FORNPREP"
                 and self._fallthrough(b) == header]
        return preps[0] if len(preps) == 1 else None
    def _chase_trampoline(self, b: int, body: set[int]) -> int | None:
        seen = {b}
        cur = b
        while cur not in body and len(self.succ[cur]) == 1:
            nxt = self.succ[cur][0]
            if nxt in seen:
                break
            if not (self._flow_to(cur, nxt) or self._jumps_to(cur, nxt)):
                break
            seen.add(nxt)
            cur = nxt
        return cur
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
            if ins.jump_target is None or not self._jump_to(latch, ins.jump_target, header):
                return None
            if len(outs) != 1:
                return None
            E = outs[0]
            if E in body:
                return None
            return Loop("repeat", header, latch, body, None, E, header)
        return None
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
