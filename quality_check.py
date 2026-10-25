"""Quality gates for decompiler output (readability, not just structure).

1. STRUCT: see struct_check.py (coverage/edge/label/kind/determinism).
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
    ("fixtures/v14/trivial.luac", 0): "print('hi')",
    ("fixtures/v14/consts.luac", 0): "print(7)",
    ("fixtures/v14/table.luac", 0): (
        "tbl = {1, 2, 3}\n"
        "tbl.x = 10\n"
        "tbl.y = tbl.x + 1\n"
        "print(tbl.y)"
    ),
    ("fixtures/v14/edge/empty.luac", 0): "return nil",
    ("fixtures/v14/edge/nil_bool.luac", 0): "return true",
    ("fixtures/v14/edge/nested.luac", 0): "return 1",
    ("fixtures/v14/edge/nested.luac", 1): "return dupclosure 'c'()",
    ("fixtures/v14/loops.luac", 0): (
        "start = 1\n"
        "R0 = 10\n"
        "R1 = 1\n"
        "for i = start, R0, R1 do\n"
        "  print(i)\n"
        "end\n"
        "count = 0\n"
        "while true do\n"
        "  R1 = 5\n"
        "  if count >= R1 then\n"
        "    break\n"
        "  end\n"
        "  count += 1\n"
        "end"
    ),
}

NAMED_ALLOWLIST = {
    # (fixture, proto) -> expected --names output. Named mode must render
    # byte-identical to default mode everywhere else: new rules migrate
    # goldens here deliberately, one entry at a time.
    ("fixtures/v14/complex/control.luac", 1): (
        "classify = dupclosure 'classify'\n"
        "total = 0\n"
        "R4 = 1\n"
        "R2 = 20\n"
        "R3 = 2\n"
        "i = R4\n"
        "for i = R4, R2, R3 do\n"
        "  R5 = i % 3\n"
        "  if R5 == 0 then\n"
        "    continue\n"
        "  end\n"
        "  R5 = 15\n"
        "  if R5 < i then\n"
        "    break\n"
        "  end\n"
        "  total += i\n"
        "end\n"
        "R4 = i\n"
        "count = 0\n"
        "while true do\n"
        "  R3 = 10\n"
        "  if count >= R3 then\n"
        "    break\n"
        "  end\n"
        "  count += 1\n"
        "  R3 = count % 2\n"
        "end\n"
        "count2 = 0\n"
        "while true do\n"
        "  count2 += 1\n"
        "  R4 = 5\n"
        "  if R4 <= count2 then\n"
        "    break\n"
        "  end\n"
        "end\n"
        "R5 = #r\n"
        "str = tostring(total)  -- fastcall\n"
        "R4 = R5 .. str\n"
        "R6 = classify(12, 3)\n"
        "R7 = classify(0, 9)\n"
        "print(R6, R7, total, count, count2, 2, false, R4)\n"
        "return total"
    ),
    ("fixtures/v14/complex/functions.luac", 11): (
        "v = tostring(R0.v)  -- fastcall\n"
        "R1 = 'Obj(' .. R0.name .. '=' .. v .. ')'\n"
        "return R1"
    ),
    ("fixtures/v14/complex/tables.luac", 1): (
        "str = tostring(R1)  -- fastcall\n"
        "return 'missing:' .. str"
    ),
    ("fixtures/v14/import.luac", 0): (
        "players = game:GetService('Players')  -- GetService method\n"
        "print(players.LocalPlayer)"
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
        named = "\n".join(render_all(Structurer(bc, p, names=True).structure().nodes))
        want_named = NAMED_ALLOWLIST.get((f.replace("\\", "/"), p.id), "\n".join(lines))
        if named != want_named:
            fails += 1
            print(f"NAMED-MISMATCH {f} P{p.id}:\n--- want ---\n{want_named}\n--- got ---\n{named}\n")
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
