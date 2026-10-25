"""Blessed whole-corpus snapshots: every proto's default render, pinned.

Bless:  python snapshot_check.py --bless   (rewrites snapshots/)
Check:  python snapshot_check.py           (exit 1 with unified diffs on churn)

Intentional improvements show as reviewable `git diff` on snapshots/;
unintended churn fails. Complements quality_check's 7 hand goldens (kept
as-is): goldens assert chosen contracts, snapshots pin everything else.
Snapshot text is bare `render_all` under a `-- source:` header so files
stay valid Luau. A `_tiers.txt` manifest pins per-proto structuring tier
(`structured` vs `dispatcher*`), joins-split counts, and leftover gotos:
a structured->dispatcher downgrade keeps parse-valid output, so render
snapshots alone stay green while quality regresses -- the manifest fails
that class fast.
"""
import difflib
import glob
import os
import shutil
import sys

sys.path.insert(0, ".")
import parser_v14
from struct_v14 import Structurer, render_all

SNAPDIR = os.path.join("snapshots", "v14")


def snapname(fixture, pid):
    base = fixture.replace("\\", "/")
    if base.startswith("fixtures/v14/"):
        base = base[len("fixtures/v14/"):]
    base = base.replace("/", "__")
    if base.endswith(".luac"):
        base = base[:-len(".luac")]
    return f"{base}__P{pid}.luau"


def render(fixture, proto):
    """Returns (text, tier, splits, gotos). Tier `-` means fully
    structured (no dispatcher fallback involved)."""
    bc = parser_v14.parse(open(fixture, "rb").read())
    (p,) = [x for x in bc.protos if x.id == proto.id]
    res = Structurer(bc, p).structure()
    text = "-- source: %s P%d\n" % (fixture.replace("\\", "/"), p.id) \
        + "\n".join(render_all(res.nodes)) + "\n"
    tier = res.stats.get("tier") or "-"
    return text, tier, res.stats.get("joins-split", 0), res.stats.get("gotos", 0)


def collect():
    files = sorted(glob.glob("fixtures/v14/**/*.luac", recursive=True))
    out = []
    for f in files:
        bc = parser_v14.parse(open(f, "rb").read())
        for p in bc.protos:
            out.append((f, p))
    return out


TIERS = "_tiers.txt"


def tierline(fixture, pid, tier, splits, gotos):
    return "%s P%d tier=%s splits=%d gotos=%d\n" % (
        fixture.replace("\\", "/"), pid, tier, splits, gotos)


def main():
    bless = "--bless" in sys.argv
    items = collect()
    if bless:
        shutil.rmtree(SNAPDIR, ignore_errors=True)
        os.makedirs(SNAPDIR, exist_ok=True)
    fails = 0
    tier_rows = []
    want_tiers = {}
    if not bless:
        try:
            with open(os.path.join(SNAPDIR, TIERS)) as fh:
                for ln in fh:
                    parts = ln.split()
                    if len(parts) == 5:
                        want_tiers[(parts[0], int(parts[1][1:]))] = ln.strip()
        except FileNotFoundError:
            pass
    for f, p in items:
        name = snapname(f, p.id)
        path = os.path.join(SNAPDIR, name)
        try:
            got, tier, splits, gotos = render(f, p)
        except Exception as e:
            print(f"SNAP-ERROR {f} P{p.id}: {type(e).__name__}: {e}")
            fails += 1
            continue
        key = (f.replace("\\", "/"), p.id)
        if bless:
            tier_rows.append(tierline(f, p.id, tier, splits, gotos))
        elif key not in want_tiers:
            print(f"TIER-MISSING {f} P{p.id} (run --bless)")
            fails += 1
        elif want_tiers[key] != tierline(f, p.id, tier, splits, gotos).strip():
            print(f"TIER-MISMATCH {f} P{p.id}: want [{want_tiers[key]}] "
                  f"got [{tierline(f, p.id, tier, splits, gotos).strip()}]")
            fails += 1
        if bless:
            with open(path, "w") as fh:
                fh.write(got)
            continue
        if not os.path.exists(path):
            print(f"SNAP-MISSING {f} P{p.id} (run --bless)")
            fails += 1
            continue
        with open(path) as fh:
            want = fh.read()
        if want != got:
            fails += 1
            print(f"SNAP-MISMATCH {f} P{p.id}:")
            for ln in list(difflib.unified_diff(
                    want.splitlines(), got.splitlines(),
                    "blessed", "current", lineterm=""))[:20]:
                print("    " + ln)
    if bless:
        with open(os.path.join(SNAPDIR, TIERS), "w") as fh:
            fh.writelines(sorted(tier_rows))
        print(f"blessed {len(items)} protos -> {SNAPDIR}")
        return
    print(f"snapshots: {len(items) - fails}/{len(items)} match, fails={fails}")
    sys.exit(1 if fails else 0)


main()
