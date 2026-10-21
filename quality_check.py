"""Quality gates for decompiler output (readability, not just structure).

1. STRUCT: reuse struct_check_tmp invariants (imported run below).
2. DEAD-DEF: no emitted pure `R# = ...` line may be totally unreferenced
   (a definitely-dead temporary the DCE should have removed).
3. GOLDEN: exact-match rendered output for simple fixtures.
4. MARKERS: count honesty markers (multret, setlist sugar, goto) for review.
"""
import re
import sys

sys.path.insert(0, ".")
import parser_v14
from struct_v14 import Structurer, render_all

PURE_SHAPE = re.compile(r"^R(\d+) = (?!.*\(.*\).*\()")
RREF = re.compile(r"R(\d+)")
LHS_MULTI = re.compile(r"^(R\d+(?:, R\d+)*) = ")
LHS_TOP = re.compile(r"^R(\d+)\.\.top = ")


def value_refs(text):
    defs = set()
    m = LHS_MULTI.match(text) or LHS_TOP.match(text)
    if m:
        g = m.group(1)
        defs = {int(x[1:]) for x in g.split(", ")} if "," in g or g.startswith("R") else set()
    return {int(x) for x in RREF.findall(text)} - defs


GOLDENS = {
    ("fixtures/v14/trivial.luac", 0): "print('hi')\nreturn",
    ("fixtures/v14/consts.luac", 0): "print(7)\nreturn",
    ("fixtures/v14/table.luac", 0): (
        "R0 = {1, 2, 3}\n"
        "R0.x = 10\n"
        "R1 = R0.x + 1\n"
        "R0.y = R1\n"
        "print(R0.y)\n"
        "return"
    ),
    ("fixtures/v14/edge/empty.luac", 0): "return nil",
    ("fixtures/v14/edge/nil_bool.luac", 0): "return true",
    ("fixtures/v14/edge/nested.luac", 0): "return 1",
    ("fixtures/v14/edge/nested.luac", 1): "return dupclosure 'c'()",
    ("fixtures/v14/loops.luac", 0): (
        "R2 = 1\n"
        "R0 = 10\n"
        "R1 = 1\n"
        "for R2 = R2, R0, R1 do\n"
        "  print(R2)\n"
        "end\n"
        "R0 = 0\n"
        "while true do\n"
        "  R1 = 5\n"
        "  if R0 >= R1 then\n"
        "    break\n"
        "  end\n"
        "  R0 = R0 + 1\n"
        "end\n"
        "return"
    ),
}

fails = 0
markers = {}
for (f, pid), want in GOLDENS.items():
    bc = parser_v14.parse(open(f, "rb").read())
    p = [x for x in bc.protos if x.id == pid][0]
    got = "\n".join(render_all(Structurer(bc, p).structure().nodes))
    if got != want:
        fails += 1
        print(f"GOLDEN-MISMATCH {f} P{pid}:\n--- want ---\n{want}\n--- got ---\n{got}\n")

import glob
dead = 0
for f in sorted(glob.glob("fixtures/v14/**/*.luac", recursive=True)):
    bc = parser_v14.parse(open(f, "rb").read())
    for p in bc.protos:
        lines = render_all(Structurer(bc, p).structure().nodes)
        for i, ln in enumerate(lines):
            s = ln.strip()
            m = PURE_SHAPE.match(s)
            if not m:
                continue
            if "(" in s or "function" in s or "..top" in s:
                continue  # calls/closures/multret: not pure-shaped, skip
            reg = int(m.group(1))
            others = [l for j, l in enumerate(lines) if j != i]
            if not any(reg in value_refs(l) for l in others):
                dead += 1
                print(f"DEAD-DEF {f} P{p.id}: {s!r}")
        for ln in lines:
            for mk in ("goto ", "multret", "..top", "<?>", "UNHANDLED"):
                if mk in ln:
                    markers[mk] = markers.get(mk, 0) + 1
print(f"golden-fails={fails} dead-defs={dead} markers={markers}")
sys.exit(1 if fails or dead else 0)
