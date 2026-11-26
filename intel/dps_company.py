"""Company facts from the PSX DPS portal  ->  state/intel_dps_company.json   (raw pages kept in state/intel_raw/dps/)

For every in-scope company that Ask Analyst does not cover (state/intel_universe.json, data_route == 'dps_reports'),
or for the symbols you name, read the company page and keep:
  profile     name, sector, fiscal year end, auditor, registrar, address, website, key people, business description
  equity      market cap (000s), shares, face value, free float (shares and %)
  summary     DPS headline financials: sales, profit after tax, EPS - annual (4) and quarterly (4), as published
              (thousands of rupees except EPS). Quarter labels are mapped to quarter-end dates from the fiscal year end
              and marked 'derived'. These are summary figures only; full statements come from the report PDFs.
  ratios      DPS ratio table as published
  filings     every announcement with its date (= the date it became public), title and document id; results and
              report transmissions are classified (kind) and the period they cover is read from the title
  (the page's own 'Financial Reports' and 'Payouts' tabs answer 403 to scripts; report history comes from intel/filings_index.py)
Every record keeps URL, retrieval time and SHA-256 of the raw response; content_sha ignores the live price block, so
'changed_at' moves only when the company's own information changes.
usage: python intel/dps_company.py [--symbols A,B,C] [--all-in-scope] [--new-only] [--limit N]"""
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
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
RAW = STATE / "intel_raw" / "dps"
OUT = STATE / "intel_dps_company.json"
UNIV = STATE / "intel_universe.json"
DPS = "https://dps.psx.com.pk"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
PAUSE = 1.0   # seconds before every request (the portal rate-limits bursts)
MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}
KIND = [
    ("annual_report", re.compile(r"transmission of (the )?annual|annual (report|financial statements|accounts)", re.I)),
    ("half_year_report", re.compile(r"transmission of (the )?half|half.?yearly (report|accounts)", re.I)),
    ("quarterly_report", re.compile(r"transmission of (the )?(1st|2nd|3rd|first|third|quarterly)|quarterly (report|accounts)", re.I)),
    ("results", re.compile(r"financial results?", re.I)),
    ("board_meeting", re.compile(r"board meeting", re.I)),
    ("payout", re.compile(r"dividend|bonus|right|book closure|credit of", re.I)),
]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def txt(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def num(s):
    s = (s or "").strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "--", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("%", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def table_rows(tbl):
    heads = [txt(th) for th in tbl.select("thead th")]
    rows = []
    for tr in tbl.select("tbody tr"):
        tds = tr.find_all("td")
        if tds:
            rows.append({"cells": [txt(td) for td in tds], "links": [a["href"] for a in tr.find_all("a", href=True) if a["href"] not in ("javascript:",)]})
    return heads, rows


def fy_month(fye):
    return MONTHS.get((fye or "").strip().title())


def quarter_end(label, fye_month):
    """'Q1 2027' with a June year end -> 2026-09-30 (derived, not published)."""
    m = re.match(r"Q([1-4])\s+(\d{4})", label or "")
    if not m or not fye_month:
        return None
    q, fy = int(m.group(1)), int(m.group(2))
    tot = fy * 12 + fye_month - 1 - (4 - q) * 3
    y, mo = divmod(tot, 12)
    mo += 1
    return f"{y}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"


def parse_fin_panel(panel, fye_month, quarterly):
    tbl = panel.find("table") if panel else None
    if not tbl:
        return None
    heads, rows = table_rows(tbl)
    cols = [h for h in heads if h]
    out = {"columns": cols, "rows": {}}
    for r in rows:
        c = r["cells"]
        out["rows"][c[0]] = [num(x) for x in c[1:]]
    if quarterly:
        out["quarter_end_derived"] = [quarter_end(h, fye_month) for h in cols]
    else:
        out["period_end_derived"] = [f"{h}-{fye_month:02d}-{calendar.monthrange(int(h), fye_month)[1]:02d}" if fye_month and h.isdigit() else None for h in cols]
    return out


def classify(title):
    for k, rx in KIND:
        if rx.search(title):
            return k
    return "other"


def period_in_title(title):
    m = re.search(r"(?:ended|ending)\s+(?:on\s+)?([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})", title, re.I)
    if not m:
        return None
    s = m.group(1).replace(",", "")
    for f in ("%B %d %Y", "%b %d %Y", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def parse_company(html):
    soup = BeautifulSoup(html, "html.parser")
    p = {}
    p["name"] = txt(soup.select_one(".quote__name"))
    p["sector"] = txt(soup.select_one(".quote__sector"))
    prof = soup.select_one("#profile")
    if prof:
        heads = prof.select(".item__head")
        for h in heads:
            k = txt(h).upper()
            nxt = h.find_next_sibling()
            if k == "BUSINESS DESCRIPTION":
                p["description"] = txt(nxt)[:1500]
            elif k in ("ADDRESS", "WEBSITE", "REGISTRAR", "AUDITOR", "FISCAL YEAR END") and nxt is not None:
                p[k.lower().replace(" ", "_")] = txt(nxt)
            elif k == "KEY PEOPLE" and nxt is not None:
                p["key_people"] = [[txt(td) for td in tr.find_all("td")] for tr in nxt.select("tr")]
    eq = {}
    labels = [(txt(i.select_one(".stats_label")), txt(i.select_one(".stats_value"))) for i in soup.select("#equity .stats_item")]
    seen_ff = 0
    for k, v in labels:
        if k.startswith("Free Float"):
            eq["free_float_shares" if seen_ff == 0 else "free_float_pct"] = num(v)
            seen_ff += 1
        elif k.startswith("Market Cap"):
            eq["market_cap_000"] = num(v)
        elif k.startswith("Shares"):
            eq["shares"] = num(v)
        elif k.startswith("Face Value"):
            eq["face_value"] = num(v)
    fm = fy_month(p.get("fiscal_year_end"))
    fin = soup.select_one("#financialTab")
    summ = {}
    if fin:
        for panel in fin.select(".tabs__panel"):
            nm = panel.get("data-name")
            summ[nm.lower()] = parse_fin_panel(panel, fm, nm == "Quarterly")
        summ["unit"] = "PKR thousand (000s) except EPS"
    rat = soup.select_one("#ratios")
    ratios = None
    if rat and rat.find("table"):
        heads, rows = table_rows(rat.find("table"))
        ratios = {"columns": [h for h in heads if h], "rows": {r["cells"][0]: [num(x) for x in r["cells"][1:]] for r in rows}}
    filings, seen = [], set()
    for panel in soup.select("#announcementsTab .tabs__panel"):
        tab = panel.get("data-name")
        tbl = panel.find("table")
        if not tbl:
            continue
        _, rows = table_rows(tbl)
        for r in rows:
            if len(r["cells"]) < 2:
                continue
            m = next((re.search(r"/download/document/(\d+)\.pdf", l) for l in r["links"] if "/download/document/" in l), None)
            try:
                d = datetime.strptime(r["cells"][0], "%b %d, %Y").date().isoformat()
            except ValueError:
                d = None
            key = (d, r["cells"][1], m.group(1) if m else None)
            if key in seen:
                continue
            seen.add(key)
            title = r["cells"][1]
            filings.append({"date": d, "title": title, "doc_id": m.group(1) if m else None, "tab": tab, "kind": classify(title), "period_end": period_in_title(title)})
    return {"profile": p, "equity": eq, "summary": summ, "ratios": ratios, "filings": filings}


def content_sha(page):
    """hash of the company's own sections (profile, equity, financials, ratios, announcements) - not the live quote block"""
    soup = BeautifulSoup(page, "html.parser")
    parts = [str(soup.select_one(i)) for i in ("#profile", "#equity", "#announcements", "#financials", "#ratios")]
    return hashlib.sha256("".join(parts).encode()).hexdigest()


def fetch_symbol(sym):
    out = {"symbol": sym}
    H = {"User-Agent": UA, "Referer": f"{DPS}/company/{sym}"}
    S = requests.Session()
    S.headers.update(H)

    def call(method, url, ajax=False, **kw):
        last = None
        hdr = {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html, */*; q=0.01"} if ajax else {}
        for a in range(6):
            try:
                time.sleep(PAUSE)
                r = S.request(method, url, timeout=45, headers=hdr, **kw)
                if r.status_code in (429, 503):          # rate limited: back off and try again
                    time.sleep(min(60, float(r.headers.get("Retry-After") or 0) or 8 * (a + 1)))
                    last = RuntimeError(f"http {r.status_code}")
                    continue
                return r
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(5 + 8 * a)
        raise last

    try:
        r = call("GET", f"{DPS}/company/{sym}")
        out["page"] = {"url": f"{DPS}/company/{sym}", "retrieved": now(), "http": r.status_code, "sha256": hashlib.sha256(r.content).hexdigest(), "bytes": len(r.content)}
        if r.status_code != 200:
            out["status"] = "page_http_" + str(r.status_code)
            return out
        page = r.text
        RAW.mkdir(parents=True, exist_ok=True)
        with gzip.open(RAW / f"{sym}.page.html.gz", "wb", 9) as g:
            g.write(r.content)
        out.update(parse_company(page))
        out["content_sha"] = content_sha(page)
        out["status"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["status"] = "error"; out["error"] = repr(e)[:200]
        return out
    return out


def main(argv):
    arg = lambda k: argv[argv.index(k) + 1] if k in argv else None
    univ = json.load(open(UNIV))["symbols"]
    if arg("--symbols"):
        todo = [s.strip().upper() for s in arg("--symbols").split(",") if s.strip()]
    else:
        todo = [s for s, x in univ.items() if x.get("scope") == "in_scope" and (x.get("data_route") == "dps_reports" or "--all-in-scope" in argv)]
    prev = json.load(open(OUT)) if OUT.exists() else {"v": 1, "symbols": {}}
    if "--new-only" in argv:   # symbols never fetched, or whose last fetch failed
        todo = [s for s in todo if prev["symbols"].get(s, {}).get("status") != "ok"]
    if arg("--limit"):
        todo = todo[: int(arg("--limit"))]
    print("fetching", len(todo), "companies", flush=True)
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(fetch_symbol, todo))
    for r in res:
        old = prev["symbols"].get(r["symbol"], {})
        if r.get("status") == "ok":
            r["changed_at"] = old.get("changed_at") if old.get("content_sha") == r.get("content_sha") else r["page"]["retrieved"]
            r["first_fetched"] = old.get("first_fetched") or r["page"]["retrieved"]
        else:  # keep the last good record, note the failure
            if old:
                old["last_fetch_error"] = {"at": now(), "status": r.get("status"), "error": r.get("error")}
                r = old
        prev["symbols"][r["symbol"]] = r
    prev["generated"] = now()
    st = {}
    for r in prev["symbols"].values():
        st[r.get("status")] = st.get(r.get("status"), 0) + 1
    prev["counts"] = {"symbols": len(prev["symbols"]), "status": st}
    json.dump(prev, open(OUT, "w"), separators=(",", ":"))
    print(prev["counts"])


if __name__ == "__main__":
    main(sys.argv[1:])
