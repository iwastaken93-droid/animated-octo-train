import sys, glob
sys.path.insert(0, ".")
import parser_v14
from struct_v14 import Structurer

files = list(dict.fromkeys(sorted(glob.glob("fixtures/v14/*.luac")) + sorted(glob.glob("fixtures/v14/**/*.luac", recursive=True))))
fails = 0; nprotos = 0
tot = {"loops": {}, "unclassified": 0, "gotos": 0, "fallbacks": 0}
for f in files:
    bc = parser_v14.parse(open(f, "rb").read())
    for p in bc.protos:
        nprotos += 1
        st = Structurer(bc, p)
        try:
            res = st.structure()
        except Exception as e:
            print(f"CRASH {f} P{p.id}: {type(e).__name__}: {e}"); fails += 1
            continue
        for k, v in res.stats.get("loops", {}).items():
            tot["loops"][k] = tot["loops"].get(k, 0) + v
        tot["unclassified"] += res.stats.get("unclassified", 0)
        tot["gotos"] += res.stats.get("gotos", 0)
        fb = res.stats.get("fallback-block", 0) + res.stats.get("fallback-nomerge", 0)
        tot["fallbacks"] += fb
        if res.unclassified:
            print(f"UNCLASS {f} P{p.id}: {res.unclassified}"); fails += 1
        if fb:
            print(f"FALLBACK {f} P{p.id}: {fb}"); fails += 1
        # coverage: every reachable block structured
        missing = st.reachable - res.done
        if missing:
            print(f"COVERAGE {f} P{p.id}: missing={sorted(missing)}"); fails += 1
        # edge coverage: every struct edge of reachable blocks classified
        logged = {(a, b) for a, b, _ in res.edges}
        for b in st.reachable:
            for t in st.succ[b]:
                if (b, t) not in logged and b != t:
                    # self-edges absorbed silently are ok if block is a latch/header
                    print(f"EDGE {f} P{p.id}: {b}->{t} unclassified"); fails += 1
        # goto/label balance: every goto target structured (has label emitted somewhere)
        rendered = "\n".join(n.render_text() if hasattr(n, 'render_text') else "" for n in [])
        import re
        from struct_v14 import render_all
        text = "\n".join(render_all(res.nodes))
        gotos = set(re.findall(r"goto (L\d+)", text))
        labels = set(re.findall(r"^\s*(L\d+):", text, re.M))
        if not gotos <= labels:
            print(f"LABEL {f} P{p.id}: dangling={sorted(gotos - labels)}"); fails += 1
        # loop-kind cross-check vs latch opcodes
        for L in res.loops:
            lop = st.cfg.blocks[L.latch].last.opname
            flowback = L.header in st.succ[L.latch]
            latches_ok = all(L.header in st.succ[l] for l in L.latches)
            if L.kind == "numfor" and lop != "FORNLOOP":
                print(f"KIND {f} P{p.id}: numfor latch={lop}"); fails += 1
            if L.kind == "genfor" and not (lop == "FORGLOOP" or latches_ok):
                print(f"KIND {f} P{p.id}: genfor latch={lop}"); fails += 1
            if L.kind in ("while", "whiletrue") and not (lop == "JUMPBACK" or latches_ok):
                print(f"KIND {f} P{p.id}: {L.kind} latch={lop}"); fails += 1
            if L.kind == "repeat":
                ins = st.cfg.blocks[L.latch].last
                from struct_v14 import COND_OPS
                if not (lop in COND_OPS or latches_ok):
                    print(f"KIND {f} P{p.id}: repeat latch={lop}"); fails += 1
        # determinism
        res2 = Structurer(bc, p).structure()
        if render_all(res.nodes) != render_all(res2.nodes):
            print(f"NONDET {f} P{p.id}"); fails += 1
print(f"files={len(files)} protos={nprotos} loops={tot['loops']} unclassified={tot['unclassified']} gotos={tot['gotos']} fallbacks={tot['fallbacks']} fails={fails}")
sys.exit(1 if fails else 0)
