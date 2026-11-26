"""Index of filed financial reports per company  ->  state/intel_filings_index.json   (raw replies in state/intel_raw/portal/)

Source: PSX financial reports portal  https://financials.psx.com.pk/  (annQtrStmts.php, the same calls its own page makes).
  get_comp_data  -> every annual / quarterly report on file for one company: PDF id, report type, period ended,
                    posting date (the date the exchange posted it - used as the 'date public' for back-tests; for
                    reports posted in bulk during a back-fill the date is the portal's, flagged below).
The company drop-down on the portal home page gives the portal's own list of company codes (kept for cross-checking
the universe and for catching new listings).
Annual reports carry only a year as period; the period end is derived from the fiscal year end read on the DPS company
page (state/intel_dps_company.json) when we have it, and marked 'derived'.
usage: python intel/filings_index.py [--symbols A,B] [--new-only] [--limit N]    (default: every in-scope symbol)"""
import calendar
import gzip
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
RAWD = STATE / "intel_raw" / "portal"
OUT = STATE / "intel_filings_index.json"
BASE = "https://financials.psx.com.pk/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
PAUSE = 0.7
MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def post(s, data, tries=5):
    last = None
    for a in range(tries):
        try:
            time.sleep(PAUSE)
            r = s.post(BASE + "annQtrStmts.php", data=data, headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE}, timeout=60)
            if r.status_code in (429, 503):
                time.sleep(8 * (a + 1)); last = RuntimeError(f"http {r.status_code}"); continue
            return r
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(5 + 8 * a)
    raise last


def company_list():
    r = requests.get(BASE, headers={"User-Agent": UA}, timeout=45)
    opts = re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', r.text, re.S)
    return {c.strip().upper(): re.sub(r"\s+", " ", n).strip() for c, n in opts if c.strip()}, {"url": BASE, "retrieved": now(), "http": r.status_code, "sha256": hashlib.sha256(r.content).hexdigest()}


def derive_period(label, fye_month):
    if re.fullmatch(r"\d{4}", label or "") and fye_month:
        y = int(label)
        return f"{y}-{fye_month:02d}-{calendar.monthrange(y, fye_month)[1]:02d}"
    return None


def fetch_symbol(sym, fye_month):
    s = session()
    try:
        r = post(s, {"name": "get_comp_data", "smbCode": sym})
        meta = {"retrieved": now(), "http": r.status_code, "sha256": hashlib.sha256(r.content).hexdigest(), "bytes": len(r.content)}
        if r.status_code != 200:
            return sym, {"status": "http_" + str(r.status_code), **meta}
        RAWD.mkdir(parents=True, exist_ok=True)
        with gzip.open(RAWD / f"{sym}.json.gz", "wb", 9) as g:
            g.write(r.content)
        data = json.loads(r.text) if r.text.strip() else []
        reps = []
        for x in data if isinstance(data, list) else []:
            m = re.search(r'href="([^"]+)"[^>]*>([^<]*)<', x.get("Reports", ""))
            if not m:
                continue
            pid = re.search(r"id=([^&\"']+)", m.group(1))
            kind = m.group(2).strip().lower()
            pe = x.get("period_ended") or ""
            reps.append({"kind": kind, "portal_id": pid.group(1) if pid else None, "url": BASE + m.group(1),
                         "period_label": pe, "period_end": pe if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pe) else derive_period(pe, fye_month),
                         "period_end_basis": "published" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pe) else ("derived_from_fiscal_year_end" if derive_period(pe, fye_month) else "unknown"),
                         "posting_date": x.get("posting_date")})
        reps.sort(key=lambda z: (z["period_end"] or "", z["kind"]))
        # bulk back-fill: many reports with one posting date means the portal's upload date, not the original release date
        dates = {}
        for z in reps:
            dates[z["posting_date"]] = dates.get(z["posting_date"], 0) + 1
        for z in reps:
            z["posting_date_note"] = "bulk_upload_date" if dates.get(z["posting_date"], 0) >= 5 else "posting_date"
        return sym, {"status": "ok", "n": len(reps), "reports": reps, **meta}
    except Exception as e:  # noqa: BLE001
        return sym, {"status": "error", "error": repr(e)[:200]}


def main(argv):
    arg = lambda k: argv[argv.index(k) + 1] if k in argv else None
    univ = json.load(open(STATE / "intel_universe.json"))["symbols"]
    dps = json.load(open(STATE / "intel_dps_company.json"))["symbols"] if (STATE / "intel_dps_company.json").exists() else {}
    prev = json.load(open(OUT)) if OUT.exists() else {"v": 1, "symbols": {}}
    if arg("--symbols"):
        todo = [x.strip().upper() for x in arg("--symbols").split(",") if x.strip()]
    else:
        todo = [s for s, x in univ.items() if x.get("scope") == "in_scope"]
    if "--new-only" in argv:
        todo = [s for s in todo if prev["symbols"].get(s, {}).get("status") != "ok"]
    if arg("--limit"):
        todo = todo[: int(arg("--limit"))]
    plist, pmeta = company_list()
    def fye(s):
        fy = ((dps.get(s) or {}).get("profile") or {}).get("fiscal_year_end", "") or ""
        return MONTHS.get(fy.strip().title())
    print("indexing", len(todo), "companies; portal lists", len(plist), "codes", flush=True)
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda s: fetch_symbol(s, fye(s)), todo))
    for sym, r in res:
        old = prev["symbols"].get(sym, {})
        if r.get("status") == "ok":
            r["first_indexed"] = old.get("first_indexed") or r["retrieved"]
            r["changed_at"] = old.get("changed_at") if old.get("sha256") == r["sha256"] else r["retrieved"]
        elif old:
            old["last_fetch_error"] = {"at": now(), **{k: r.get(k) for k in ("status", "error")}}
            r = old
        prev["symbols"][sym] = r
    prev["portal_company_list"] = {"meta": pmeta, "codes": plist}
    prev["generated"] = now()
    st = {}
    for r in prev["symbols"].values():
        st[r.get("status")] = st.get(r.get("status"), 0) + 1
    prev["counts"] = {"symbols": len(prev["symbols"]), "status": st,
                      "universe_in_scope_not_on_portal_list": sorted(s for s, x in univ.items() if x.get("scope") == "in_scope" and s not in plist)}
    json.dump(prev, open(OUT, "w"), separators=(",", ":"))
    print(prev["counts"]["status"], "not on portal list:", len(prev["counts"]["universe_in_scope_not_on_portal_list"]))


if __name__ == "__main__":
    main(sys.argv[1:])
