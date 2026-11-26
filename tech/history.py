"""Daily OHLCV store (append-only CSV, universe symbols only) built from PSX market-summary files."""
import csv, io, time, zipfile
from datetime import date, timedelta
from pathlib import Path
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
HEAD = ["date", "symbol", "open", "high", "low", "close", "volume", "prev"]


def fetch_day(d, universe=None):
    """Rows for one trading day, [] for a holiday/no file, None on failure."""
    for a in range(4):
        try:
            r = requests.get(f"https://dps.psx.com.pk/download/mkt_summary/{d.isoformat()}.Z", headers=H, timeout=40)
            if r.status_code == 404:
                return []
            if r.ok and r.content[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(r.content))
                rows = []
                for n in z.namelist():
                    for ln in z.read(n).decode("latin-1").splitlines():
                        p = ln.split("|")
                        if len(p) >= 10 and p[1].strip():
                            s = p[1].strip().upper()
                            if universe is None or s in universe:
                                rows.append([d.isoformat(), s, p[4], p[5], p[6], p[7], p[8], p[9]])
                return rows
        except Exception:
            pass
        time.sleep(3 + 5 * a)
    return None


def last_date(path):
    p = Path(path)
    if not p.exists():
        return None
    last = None
    with open(p) as f:
        for r in csv.reader(f):
            if r and r[0] != "date":
                last = r[0]
    return date.fromisoformat(last) if last else None


def update(path, universe, today, log=print, max_days=40):
    """Append missing trading days (after the last stored date, before `today`). Returns days added."""
    p = Path(path)
    p.parent.mkdir(exist_ok=True)
    last = last_date(p)
    if last is None:
        raise SystemExit(f"{p} missing - seed it first")
    d, added = last + timedelta(days=1), 0
    new = []
    while d <= today and (d - last).days <= max_days:
        if d.weekday() < 5:
            rows = fetch_day(d, universe)
            time.sleep(0.5)
            if rows is None:
                log("history: fetch failed for", d)
                break
            if rows:
                new.append(rows)
                added += 1
        d += timedelta(days=1)
    if new:
        with open(p, "a", newline="") as f:
            w = csv.writer(f)
            for rows in new:
                w.writerows(rows)
    log("history: added", added, "trading days")
    return added
