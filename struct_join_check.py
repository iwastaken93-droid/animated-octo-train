"""Focused contract for join-merge continuation (struct_emit).

Pins behavior, not implementation:
- Loop-entangled diamonds structure instead of stranding as gotos.
- Join telemetry survives the production stats summary.
- Output is deterministic per proto.

Terminator-first ordering for goto-free shapes is pinned by the quality
goldens and full gates; this file stays limited to the join contract.
Run: python struct_join_check.py. Exit 1 with FAIL lines on mismatch.
"""
import sys

sys.path.insert(0, ".")
import parser_v14
from struct_v14 import Structurer, render_all

# (fixture, pid, min joins-merged, min dups-inlined)
CASES = [
    ("fixtures/v14/complex/adversarial.luac", 10, 0, 0),
    ("fixtures/v14/complex/adversarial.luac", 14, 1, 1),
    ("fixtures/v14/edge/struct_torture.luac", 3, 0, 0),
]

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)
        if detail:
            print("  " + detail)


for fixture, pid, min_merges, min_dups in CASES:
    bc = parser_v14.parse(open(fixture, "rb").read())
    proto = next(p for p in bc.protos if p.id == pid)
    first = Structurer(bc, proto).structure()
    text = "\n".join(render_all(first.nodes))
    again = "\n".join(render_all(Structurer(bc, proto).structure().nodes))
    tag = f"{fixture} P{pid}"
    merges = first.stats.get("joins-merged", 0) or 0
    dups = first.stats.get("dups-inlined", 0) or 0
    check(f"structured {tag}",
          first.stats.get("tier") is None and first.stats.get("gotos", 0) == 0,
          f"tier={first.stats.get('tier')} gotos={first.stats.get('gotos')}")
    check(f"no-goto {tag}", "goto " not in text and "_pc" not in text)
    check(f"deterministic {tag}", text == again)
    check(f"telemetry {tag}",
          isinstance(merges, int) and isinstance(dups, int)
          and merges >= min_merges and dups >= min_dups,
          f"joins-merged={merges} dups-inlined={dups}")

print(f"failures: {len(fails)}")
sys.exit(1 if fails else 0)
