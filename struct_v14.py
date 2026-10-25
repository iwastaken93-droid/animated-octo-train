"""Decompiler structuring passes (Luau bytecode v14): CFG -> structured Nodes.

Pipeline: parser_v14/disassembler_v14 decode words -> cfg_v14 builds the CFG ->
ir_v14 lifts each pc to a source line -> this package classifies loops/branches
and emits structured pseudo-Luau. Split into modules by phase:

- struct_node: shared representation (Cond, Node, Loop, StructResult, render_all)
- struct_detect: CFG setup, loop finding/classification, graph mutation
  (goto-target absorption, join splitting)
- struct_emit: statement expansion, loop/branch emission, goto/label
  edges, dispatcher fallback
- struct_sweep_{util,dce,branch,value,reg}: concern-owned sweep mixins
  (shared traversal/text policy, deletion, branch-shape, value/expression,
  register-alias)
- struct_sweep: postpass coordinator (fixpoint driver) composing the
  sweep mixins
- struct_sweep_name: usage-evidence rename of single-def registers (default on)
- struct_v14 (this file): Structurer composition, entry points, CLI self-test

External imports stay ``from struct_v14 import ...``; names are re-exported below.
"""
from __future__ import annotations

import sys

import parser_v14
from cfg_v14 import build_cfg, CFGError
from disassembler_v14 import DisasmError
from ir_v14 import Lifter, IRError

from struct_node import (StructError, FASTCALL_OPS, COND_OPS, Cond, taken_cond,
                         Node, render_all, Loop, StructResult)
from struct_detect import DetectMixin
from struct_emit import EmitMixin
from struct_sweep import SweepMixin


class Structurer(DetectMixin, EmitMixin, SweepMixin):
    """Composes the phase mixins; run state is set up by __init__/_reset_run_state."""

    def __init__(self, bc: parser_v14.Bytecode, proto: parser_v14.Proto,
                 names: bool = True):
        self.bc = bc
        self.proto = proto
        self._want_names = names
        self.cfg = build_cfg(proto.code)
        if not self.cfg.insns:
            raise StructError("empty code")
        res = Lifter(bc, proto).lift()
        if res.unhandled:
            raise StructError(f"unhandled insns: {res.unhandled}")
        self.pclines = res.pc_lines
        self._idom_map = {}  # current-graph idom for absorption
        self._setup_graph()
    def _reset_run_state(self) -> None:
        # Fresh per-run output state: absorption re-runs structure on the
        # same graph (loops/bodies persist, emission restarts from scratch).
        self._labels = {}
        self._emitted_loops = set()  # id(loop)
        self._gotos = set()
        self._latch_stack = []
        self._anchors = {}  # block -> (node list, index)
        self._merge_arm_depth = 0  # >0 inside join-merge arm builds
        self._nodes = []
        self.res.done = set()
        self.res.goto_edges = []
        self.res.edges = []
        self.res.nodes = []
        self._stops = {}
    def _take_run_snapshot(self) -> tuple:
        """Capture mutable run state for one speculative _build attempt.
        Single owner (with _reset_run_state/_restore_run_snapshot) so
        emission frames never hand-roll the snapshot tuple."""
        return (set(self.res.done), dict(self._anchors), set(self._emitted_loops),
                set(self._gotos), dict(self._labels), len(self.res.edges),
                len(self.res.goto_edges))
    def _restore_run_snapshot(self, snap: tuple) -> None:
        """Restore a run snapshot with copies: rebinding alone aliases
        the snap set, so post-restore marks would corrupt later restores
        to the same snapshot."""
        (self.res.done, self._anchors, self._emitted_loops,
         self._gotos, self._labels, n_edges, n_gedges) = (
            set(snap[0]), dict(snap[1]), set(snap[2]),
            set(snap[3]), dict(snap[4]), snap[5], snap[6])
        del self.res.edges[n_edges:]
        del self.res.goto_edges[n_gedges:]

def structure_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto,
                    names: bool = True) -> StructResult:
    return Structurer(bc, proto, names=names).structure()
def render_struct(res: StructResult, bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> str:
    dn = proto.debugname
    label = dn.decode("utf-8", "replace") if isinstance(dn, bytes) else (dn or "??")
    head = [f"-- function P{proto.id} ({label}): structured, "
            f"loops={res.stats.get('loops', {})} gotos={res.stats.get('gotos', 0)} "
            f"unclassified={res.stats.get('unclassified', 0)}"]
    return "\n".join(head + render_all(res.nodes))
if __name__ == "__main__":
    raw = sys.argv[1:]
    args: list[str] = []
    only = None
    want_names = "--no-names" not in raw
    raw = [a for a in raw if a not in ("--names", "--no-names")]
    skip_next = False
    for k, a in enumerate(raw):
        if skip_next:
            skip_next = False
            continue
        if a == "--proto":
            only = int(raw[k + 1])
            skip_next = True
        elif a.startswith("--proto="):
            only = int(a.split("=", 1)[1])
        else:
            args.append(a)
    files = args or ["fixtures/v14/loops.luac"]
    bad = False
    for f in files:
        try:
            bc = parser_v14.parse(open(f, "rb").read())
            for p in bc.protos:
                if only is not None and p.id != only:
                    continue
                res = structure_proto(bc, p, names=want_names)
                print(f"== {f} Function {p.id} ==")
                print(render_struct(res, bc, p))
                print()
        except (parser_v14.ParseError, DisasmError, CFGError, IRError, StructError) as e:
            bad = True
            print(f"FAIL {f}: {type(e).__name__}: {e}")
    raise SystemExit(1 if bad else 0)

__all__ = ["Structurer", "structure_proto", "render_struct", "StructError", "StructResult",
           "Loop", "Node", "Cond", "taken_cond", "render_all", "COND_OPS", "FASTCALL_OPS"]
