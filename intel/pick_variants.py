"""Choose, per OCR report, between the original extraction and re-OCR variants (state/intel_extract_v/<v>/), by check results.
A variant replaces the original only if it scores strictly better: (status rank, failed checks, -passed checks, -fields)."""
import json, glob, os, sys, tempfile, pathlib
sys.path.insert(0, "intel")
import build_financials as BO, build_financials_fin as BF
RANK = {"ok": 0, "review": 1, "low_confidence": 2, "quarantine": 3}
U = json.load(open("state/intel_universe.json")); U = U.get("symbols", U)
isop = lambda s: (U.get(s, {}) or {}).get("company_class", "operating") == "operating"
def score(items):
    if not items: return (9, 99, 0, 0)
    return (min(RANK.get(i["status"], 3) for i in items), sum(c["status"] == "FAIL" for i in items for c in i["checks"]),
            -sum(c["status"] == "pass" for i in items for c in i["checks"]), -sum(len(i["fields"]) for i in items))
def build(mod, exdir, syms):
    mod.EX = pathlib.Path(exdir); mod.OUT = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    mod.main(syms); return json.load(open(mod.OUT))
orig = {}
for f in ("intel_financials", "intel_financials_fin"):
    for s, l in json.load(open(f"state/{f}.json")).items():
        for i in l: orig.setdefault((s, i["report"]), []).append(i)
def merged_dir(vd):
    """combine per-shard files SYM__n.json into one SYM.json per symbol (temp dir)"""
    tmp = tempfile.mkdtemp(); acc = {}
    for f in glob.glob(vd + "/*.json"):
        sym = os.path.basename(f)[:-5].split("__")[0]; acc.setdefault(sym, {}).update(json.load(open(f)))
    for sym, d in acc.items(): json.dump(d, open(f"{tmp}/{sym}.json", "w"))
    return tmp
vdirs = sorted(glob.glob("state/intel_extract_v/*"))
cand = {}; VDIR = {}
for vd in vdirs:
    v = os.path.basename(vd); VDIR[v] = merged_dir(vd); vd = VDIR[v]; syms = sorted(os.path.basename(p)[:-5] for p in glob.glob(vd + "/*.json"))
    for mod, sel in ((BO, [s for s in syms if isop(s)]), (BF, [s for s in syms if not isop(s)])):
        if not sel: continue
        try: res = build(mod, vd, sel)
        except Exception as e: print("build error", v, repr(e)[:100]); continue
        for s, l in res.items():
            for i in l: cand.setdefault((s, i["report"]), {}).setdefault(v, []).append(i)
changed = []; stats = {"better": 0, "same_or_worse": 0}
for (s, rid), vs in cand.items():
    base = score(orig.get((s, rid), [])); best = (base, None)
    for v, items in vs.items():
        sc = score(items)
        if sc < best[0]: best = (sc, v)
    if best[1]:
        stats["better"] += 1; p = f"state/intel_extract/{s}.json"; d = json.load(open(p))
        new = json.load(open(f"{VDIR[best[1]]}/{s}.json"))[rid]
        new["variant"] = best[1]; d[rid] = new; json.dump(d, open(p, "w"), separators=(",", ":"))
        changed.append({"sym": s, "report": rid, "variant": best[1], "before": base, "after": best[0]})
    else: stats["same_or_worse"] += 1
json.dump({"stats": stats, "changed": changed}, open("state/intel_ocr_variant_log.json", "w"), indent=1)
print(stats)
