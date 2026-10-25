"""Structurer postpasses: fixpoint driver over the concern-owned sweep mixins.

Pass ordering steals from the reference decompiler's computeInlining pipeline
(decompiler_analysis/decompiled/0x18015a9a0_AST_Inlining_computeInlining.c):
short-circuit/boolean reconstruction runs interleaved with folding to a
fixpoint (not once), and a structuring failure retries through a simplified
fallback instead of hard-failing (cf. Core_Pipeline_Dispatcher 0x180129920).
"""
from __future__ import annotations

import time

from cfg_v14 import Block
from struct_node import (FOR_OPS, Node, StructResult)
from struct_sweep_util import SweepUtilMixin
from struct_sweep_dce import DceMixin
from struct_sweep_branch import BranchMixin
from struct_sweep_value import ValueMixin
from struct_sweep_reg import RegMixin
from struct_sweep_name import NameMixin


class SweepMixin(SweepUtilMixin, DceMixin, BranchMixin, ValueMixin, RegMixin, NameMixin):
    """Postpass coordinator (mixed into Structurer): composes the
    concern-owned sweep mixins and runs the fixpoint driver.
    Sweep logic lives in struct_sweep_{util,dce,branch,value,reg,name};
    graph mutation lives in DetectMixin, rendered output in EmitMixin.
    This module owns only run state (self._nodes, self.res) and the
    driver that invokes the sweeps in fixpoint order."""

    # (sweep method, extra args, stats key, gates the fixpoint). The
    # driver calls each in order per round and accumulates the counters.
    SWEEP_STAGES = (
        ("_merge_bool_assigns", (), "bool-merged", False),
        ("_merge_guards", (), "guards-merged", False),
        ("_compact_conditionals", (), "conditionals-compacted", True),
        ("_eliminate_nil", (), "nils-eliminated", True),
        ("_spell_constants", (), "constants-spelled", True),
        ("_interpolate_format", (), "format-interpolated", True),
        ("_flatten_concat", (), "concat-flattened", True),
        ("_invert_continue_guards", (False,), "continue-inverted", False),
        ("_factor_common_tails", (), "tails-factored", True),
        ("_flatten_terminator_guards", (), "terminator-flattened", False),
        ("_flatten_guards", (False,), "guards-flattened", True),
        ("_recover_guard_continue", (), "guards-continued", True),
        ("_drop_unreachable", (), "unreachable-dropped", False),
        ("_cleanup_tails", (True, False), "tails-cleaned", True),
        ("_inline_single_use_temps", (), "inlined-temps", True),
        ("_propagate_copies", (), "copies-propagated", True),
        ("_compound_assigns", (), "compound-assigned", True),
    )
    def structure(self) -> StructResult:
        """Structure with goto elimination. Plain reducible protos take the
        fast path (single _run_structure, no gotos). Leftover gotos pull
        their targets into the enclosing loop body (absorption rounds),
        then anything left goes to the pc-dispatcher (no FOR ops) or
        stays excluded (FOR + unabsorbable edges)."""
        # Dispatcher-pattern fallback (cf. 0x180129920): a structuring crash
        # retries through the whole-proto pc-dispatcher instead of
        # propagating, whenever the proto has no FOR ops. Success-path
        # behavior is unchanged.
        try:
            self._run_structure()
        except Exception:
            self._reset_run_state()
            if self._apply_dispatcher():
                return self.res
            raise
        rounds = 0
        while self.res.goto_edges and rounds < self._ABSORB_ROUNDS:
            if not self._absorb_goto_targets():
                break
            self._run_structure()
            rounds += 1
        # Node-splitting prototype: duplicate one multi-predecessor join
        # per leftover goto round so each copy has a single predecessor
        # and structures inline (no goto-join). Non-loop joins only;
        # loop roles keep the dispatcher/excluded outcomes as before.
        # Budget-capped: block growth is monotone, rounds terminate.
        splits = 0
        if self.res.goto_edges:
            self._split_n0 = len(self.cfg.blocks)
            # Pristine-graph rollback: failed splits must not bloat the
            # dispatcher's CFG (copies are reachable and would emit as
            # extra `_pc` arms). Only conversions keep their copies.
            saved_blocks = [Block(b.id, b.start_pc, b.insns,
                                  list(b.successors), list(b.predecessors))
                            for b in self.cfg.blocks]
            saved_succ = {k: list(v) for k, v in self.succ.items()}
            while self.res.goto_edges and splits < self._SPLIT_BUDGET:
                if not self._split_join_once():
                    break
                splits += 1
                # No absorption here: absorption already failed on the
                # pre-split graph, and splits refuse loop roles, so there
                # is nothing new for it to pull in. Plain re-run.
                self._run_structure()
            if self.res.goto_edges and splits:
                self.cfg.blocks = saved_blocks
                self.succ = saved_succ
                self._refresh_derived()
                self._run_structure()
        if self.res.goto_edges:
            B = {b.id: b for b in self.cfg.blocks}
            has_for = any(
                B[x].last is not None
                and B[x].last.opname in FOR_OPS
                for x in self.reachable if x in B)
            if not has_for:
                self._apply_dispatcher()
            # else: FOR proto with leftover gotos: leave excluded as-is.
        if splits:
            # Applied after the dispatcher check: _apply_dispatcher
            # rebuilds stats, which would otherwise drop the count.
            self.res.stats["joins-split"] = splits
        # Usage-evidence naming runs once over the finished tree (default
        # on, `names=False` keeps R-numbers): sweep gates never see renamed
        # text, and the dispatcher fallback (which owns `_pc`) is untouched.
        nloopsplit = (0 if self.res.stats.get("tier") == "dispatcher"
                      else self._split_self_init_loop(self._nodes))
        self.res.stats["loopvars-split"] = nloopsplit
        if self._want_names and self.res.stats.get("tier") != "dispatcher":
            self.res.stats["renamed"] = self._apply_names(
                self.res.nodes, self.proto.numparams,
                tuple(getattr(self.proto, "upvals", None) or ()))
        return self.res
    def _run_structure(self) -> None:
        self._reset_run_state()
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
        # Boolean-merge before DCE so a merged `R = C and v` keeps C/v live;
        # iterate merge+DCE to a fixpoint (merging unmasks dead defs and vice
        # versa, same reason the reference interleaves its boolean/fold passes).
        t0 = time.perf_counter()
        nmerged = 0
        while True:
            gated = 0
            for meth, args, key, gates in self.SWEEP_STAGES:
                n = getattr(self, meth)(self._nodes, *args)
                self.res.stats[key] = self.res.stats.get(key, 0) + n
                if key == "bool-merged":
                    nmerged += n
                if gates:
                    gated += n
            if not self._dce(self._nodes):
                if not gated:
                    break
        self._pass_time(f"merge+dce(-{nmerged})", t0)
        dropped = self._dce_fixpoint(self._nodes)
        self.res.stats["dce-dropped"] = dropped
        self.res.nodes = self._nodes
        kinds: dict[str, int] = {}
        for L in self.res.loops:
            kinds[L.kind] = kinds.get(L.kind, 0) + 1
        edgekinds: dict[str, int] = {}
        for _, _, k in self.res.edges:
            edgekinds[k] = edgekinds.get(k, 0) + 1
        # carry sweep-pass counters across the summary rebuild (and across
        # absorb rounds, since stats persist on res). Keys derive from
        # SWEEP_STAGES so adding a stage cannot desync this list.
        carry = {k: self.res.stats.get(k, 0) for k in
                 [key for _, _, key, _ in self.SWEEP_STAGES]
                 + ["joins-merged", "dups-inlined", "dce-dropped"]}
        self.res.stats = {
            "loops": kinds, "edges": edgekinds,
            "unclassified": len(self.res.unclassified),
            "gotos": len(self._gotos),
            "done": len(self.res.done), "reachable": len(self.reachable),
            "skipped": len(self.res.skipped),
        }
        self.res.stats.update(carry)
