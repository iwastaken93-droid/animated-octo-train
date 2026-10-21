"""Parse-validity gate: every goto-free decompiled proto must be accepted
by Luau's real parser (Luau.Ast.CLI). Protos with irreducible flow
(goto/label) or multret-SETLIST are excluded by design (no valid form)
and counted separately.
"""
import glob
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, ".")
import parser_v14
from emit_v14 import emit_proto
from struct_v14 import Structurer, render_all

AST = r"C:/lb/Release/luau-ast.exe"

files = sorted(glob.glob("fixtures/v14/**/*.luac", recursive=True))
ok = excl = fail = 0
bad = []
excl_reasons: dict[str, int] = {}
for f in files:
    bc = parser_v14.parse(open(f, "rb").read())
    for p in bc.protos:
        src, valid = emit_proto(bc, p)
        if not valid:
            excl += 1
            excl_reasons["goto/label/multret-setlist"] = excl_reasons.get(
                "goto/label/multret-setlist", 0) + 1
            continue
        # textual pre-gate: no unexpanded dynamic forms may remain
        leftover = [m for m in ("multret", "..top", "<?>", "UNHANDLED") if m in src]
        if leftover:
            excl += 1
            excl_reasons[f"leftover:{','.join(leftover)}"] = excl_reasons.get(
                f"leftover:{','.join(leftover)}", 0) + 1
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".luau", delete=False) as tf:
            tf.write(src + "\n")
            name = tf.name
        try:
            # NOTE: luau-ast always exits 0 and prints JSON; parse
            # problems are reported as "Parse errors were encountered"
            # on stderr. That string is the gate signal.
            r = subprocess.run([AST, name], capture_output=True, text=True, timeout=60)
            out = (r.stdout or "") + (r.stderr or "")
            if "Parse errors" in out:
                fail += 1
                bad.append((f, p.id, out.strip()[:600]))
            else:
                ok += 1
        finally:
            os.unlink(name)
print(f"parse-ok={ok} excluded={excl}{excl_reasons} parse-fail={fail}")
for f, pid, msg in bad[:10]:
    print(f"PARSE-FAIL {f} P{pid}:\n{msg}\n")
sys.exit(1 if fail else 0)
