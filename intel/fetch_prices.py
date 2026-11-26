"""Backfill daily prices (open/high/low/close/volume/prev) for symbols missing from tech/ohlcv_universe.csv,
from the same official source the daily job uses (dps.psx.com.pk/download/mkt_summary/DATE.Z). Sharded by date.
Output: state/intel_prices/part_<shard>.csv  (same columns as ohlcv_universe.csv)."""
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, "tech")
import history
syms = set(x for x in sys.argv[1].split(",") if x); shard, n = int(sys.argv[2]), int(sys.argv[3])
start, end = dt.date(2022, 1, 3), dt.date.today()
days = [start + dt.timedelta(i) for i in range((end - start).days + 1)]
days = [d for d in days if d.weekday() < 5][shard::n]
out = pathlib.Path("state/intel_prices"); out.mkdir(parents=True, exist_ok=True)
rows, fails = [], []
for d in days:
    r = history.fetch_day(d, syms)
    if r is None: fails.append(d.isoformat())
    else: rows += r
with open(out / f"part_{shard}.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(history.HEAD); w.writerows(rows)
(out / f"fails_{shard}.txt").write_text("\n".join(fails))
print("shard", shard, "days", len(days), "rows", len(rows), "failed", len(fails))
