import json, glob, sys, os
kinds = tuple(os.environ.get("OCR_KINDS", "quarterly").split(","))
shard, n = int(sys.argv[1]), int(sys.argv[2])
w = {}
for f in sorted(glob.glob("state/intel_raw/reports_index/*.json")):
    for s, d in json.load(open(f)).items():
        for k, v in d.items():
            if v.get("ocr") and v.get("status") == "ok" and not v.get("ocr_done") and (v.get("kind") or "").startswith(kinds):
                w[s] = w.get(s, 0) + (v.get("pages") or 20)
order = sorted(w, key=lambda s: -w[s]); bins = [[] for _ in range(n)]; load = [0] * n
for s in order:
    i = load.index(min(load)); bins[i].append(s); load[i] += w[s]
print(",".join(bins[shard]) if len(sys.argv) < 4 else f"{len(w)} symbols, {sum(w.values())} pages, max shard {max(load)} pages")
