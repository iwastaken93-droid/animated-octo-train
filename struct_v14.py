"""Decompiler structuring passes (Luau bytecode v14): CFG -> structured Nodes.

Pipeline: parser_v14/disassembler_v14 decode words -> cfg_v14 builds the CFG ->
ir_v14 lifts each pc to a source line -> this package classifies loops/branches
and emits structured pseudo-Luau. Split into modules by phase:

- struct_node: shared representation (Cond, Node, Loop, StructResult, render_all)
- struct_detect: CFG setup, loop finding, loop classification
- struct_emit: statement expansion and loop/branch emission
- struct_sweep: DCE, label/goto resolution, structuring sweeps, dispatcher
- struct_v14 (this file): Structurer composition, entry points, CLI self-test

External imports stay ``from struct_v14 import ...``; names are re-exported below.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import parser_v14
from cfg_v14 import Insn, Block, CFG, build_cfg, CFGError
from disassembler_v14 import DisasmError, fmt_const
from ir_v14 import Lifter, IRError

from struct_node import (StructError, FASTCALL_OPS, COND_OPS, Cond, taken_cond, Node, render_all, Loop, StructResult, _RDEF_RE, _RMULTI_LHS_RE, _RTOP_LHS_RE, _RFOR_BIND_RE, _RREF_RE, _RREF_RES, _PURE_DEF_OPS, _NEG)
from struct_detect import DetectMixin
from struct_emit import EmitMixin
from struct_sweep import SweepMixin


class Structurer(DetectMixin, EmitMixin, SweepMixin):
    """Composes the phase mixins; run state is set up by __init__/_reset_run_state."""

    def __init__(self, bc: parser_v14.Bytecode, proto: parser_v14.Proto):
        self.bc = bc
        self.proto = proto
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
        self._labels = {}
        self._emitted_loops = set()  # id(loop)
        self._gotos = set()
        self._latch_stack = []
        self._anchors = {}  # block -> (node list, index)
        self._nodes = []

def structure_proto(bc: parser_v14.Bytecode, proto: parser_v14.Proto) -> StructResult:
    return Structurer(bc, proto).structure()
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
                res = structure_proto(bc, p)
                print(f"== {f} Function {p.id} ==")
                print(render_struct(res, bc, p))
                print()
        except (parser_v14.ParseError, DisasmError, CFGError, IRError, StructError) as e:
            bad = True
            print(f"FAIL {f}: {type(e).__name__}: {e}")
    raise SystemExit(1 if bad else 0)

__all__ = ["Structurer", "structure_proto", "render_struct", "StructError", "StructResult",
           "Loop", "Node", "Cond", "taken_cond", "render_all", "COND_OPS", "FASTCALL_OPS"]
