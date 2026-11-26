"""Fetch DPS end-of-day price history (timestamp, close, volume) for companies without prices in tech/ohlcv_universe.csv, plus KSE100/KMI30.
Source: dps.psx.com.pk/timeseries/eod/SYM. Output: state/intel_prices/SYM.json = [[epoch, close, volume], ...] with retrieval stamp in index."""
import json, sys, time, pathlib, datetime, requests
OUT = pathlib.Path("state/intel_prices"); OUT.mkdir(parents=True, exist_ok=True)
syms = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] else []
syms = ["KSE100", "KMI30"] + [s for s in syms if s]
H = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/plain, */*"}
idx_p = OUT / "_index.json"; idx = json.loads(idx_p.read_text()) if idx_p.exists() else {}
for s in syms:
    for a in range(3):
        try:
            r = requests.get(f"https://dps.psx.com.pk/timeseries/eod/{s}", headers={**H, "Referer": f"https://dps.psx.com.pk/company/{s}"}, timeout=40)
            j = r.json().get("data") or []
            if j:
                (OUT / f"{s}.json").write_text(json.dumps([[x[0], x[1], x[2]] for x in j], separators=(",", ":")))
                idx[s] = {"rows": len(j), "first": j[-1][0], "last": j[0][0], "retrieved": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "http": r.status_code}
                print(s, len(j)); break
            print(s, "empty", r.status_code)
        except Exception as e: print(s, "err", str(e)[:80])
        time.sleep(3)
    time.sleep(0.4)
idx_p.write_text(json.dumps(idx, indent=1))
