"""Listed-company universe for the Investment Intelligence System  ->  state/intel_universe.json

Sources (every record says which ones it came from; each source keeps URL, retrieval time and SHA-256):
  seed       PSX Daily Quotations PDF, parsed (intel/inputs/quotation_seed_*.json): official segment + sector, rough names.
  symbols    DPS /symbols JSON (search list): clean names, sector, ETF / debt flags.      (used when reachable)
  summary    DPS daily market summary: every symbol that traded on the latest trading day, with sector code and name.
  screener   DPS screener page: index membership, free float. Hides symbols that are ex-dividend/ex-right that day,
             so it is only ever used to ADD information, never to decide that something is missing.
  askanalyst state/latest_quarters.json (read-only): which companies the existing financial layer already covers.

Rules: a symbol is never deleted. A symbol that stops appearing is marked 'absent' (after ABSENT_RUNS runs) and a
symbol seen for the first time after the baseline run is flagged 'new_listing' with its first_seen date.
Rights issues and preference shares are only TAGGED (scope 'tag_only'); nothing is extracted for them.
Run:  python intel/universe.py [--offline]   (offline = use files already in state/intel_raw; no fetching)"""
import glob
import gzip
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
RAW = STATE / "intel_raw"
OUT = STATE / "intel_universe.json"
DPS = "https://dps.psx.com.pk"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
XHR = {"User-Agent": UA, "Referer": DPS + "/", "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/plain, */*"}
PKT = timezone(timedelta(hours=5))
ABSENT_RUNS = 5
VERSION = 1

FIN_CLASS = {
    "COMMERCIAL BANKS": ("bank", "bank"),
    "INSURANCE": ("insurer", "insurer"),
    "INV. BANKS / INV. COS. / SECURITIES COS.": ("investment_finance", "other_financial"),
    "LEASING COMPANIES": ("leasing", "other_financial"),
    "MODARABAS": ("modaraba", "other_financial"),
    "REAL ESTATE INVESTMENT TRUST": ("reit", "reit"),
    "CLOSE - END MUTUAL FUND": ("closed_end_fund", "fund"),
}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def get(url, headers=None, tries=3, timeout=45):
    import requests
    last = None
    for a in range(tries):
        try:
            r = requests.get(url, headers=headers or {"User-Agent": UA}, timeout=timeout)
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 + 5 * a)
    raise last


def clean_sector(s):
    s = re.sub(r"\s+", " ", (s or "").replace("\r", " ").replace("\n", " ")).strip().upper()
    s = s.replace("PAPER, BOARD & PACKAGING", "PAPER, BOARD & PACKAGING")
    return s


# ------------------------------------------------------------------ sources
def load_seed():
    files = sorted(glob.glob(str(ROOT / "intel" / "inputs" / "quotation_seed_*.json")))
    if not files:
        return None
    d = json.load(open(files[-1]))
    d["_path"] = files[-1].replace(str(ROOT) + "/", "")
    return d


def fetch_symbols(offline):
    """DPS /symbols (JSON list). Returns (list|None, meta)."""
    meta = {"source": "dps_symbols", "url": DPS + "/symbols", "status": "not_run"}
    if offline:
        return None, meta
    try:
        r = get(DPS + "/symbols", XHR)
        meta.update(retrieved=now(), http=r.status_code, sha256=sha(r.content), bytes=len(r.content))
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, list) and d:
                meta["status"] = "ok"; meta["rows"] = len(d)
                return d, meta
        meta["status"] = "unusable"
    except Exception as e:  # noqa: BLE001
        meta.update(status="error", error=repr(e)[:200])
    return None, meta


def parse_summary_zip(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    out = {}
    for n in z.namelist():
        for ln in z.read(n).decode("latin-1").splitlines():
            p = ln.split("|")
            if len(p) >= 10 and p[1].strip():
                try:
                    out[p[1].strip().upper()] = {"sector_code": p[2].strip(), "name": p[3].strip(), "close": float(p[7]), "volume": p[8].strip()}
                except ValueError:
                    pass
    return out


def fetch_summary(offline):
    meta = {"source": "dps_market_summary", "status": "not_run"}
    if offline:
        return None, None, meta
    d = datetime.now(PKT).date()
    for _ in range(10):
        if d.weekday() < 5:
            url = f"{DPS}/download/mkt_summary/{d.isoformat()}.Z"
            try:
                r = get(url)
                if r.status_code == 200 and r.content[:2] == b"PK":
                    rows = parse_summary_zip(r.content)
                    meta.update(url=url, retrieved=now(), sha256=sha(r.content), status="ok", trading_date=d.isoformat(), rows=len(rows))
                    return d.isoformat(), rows, meta
            except Exception as e:  # noqa: BLE001
                meta.update(status="error", error=repr(e)[:200])
            time.sleep(1)
        d -= timedelta(days=1)
    return None, None, meta


def fetch_screener(offline):
    meta = {"source": "dps_screener", "url": DPS + "/screener", "status": "not_run"}
    if offline:
        return None, meta
    try:
        r = get(DPS + "/screener")
        meta.update(retrieved=now(), http=r.status_code, sha256=sha(r.content), bytes=len(r.content))
        if r.status_code != 200:
            meta["status"] = "unusable"; return None, meta
        RAW.mkdir(parents=True, exist_ok=True)
        with gzip.open(RAW / "screener_latest.html.gz", "wb", 9) as g:
            g.write(r.content)
        html = r.content.decode("utf8", "ignore")
        out = {}
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)[1:]:
            c = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(c) >= 11 and c[0]:
                out[c[0].upper()] = {"sector_code": c[1], "indices": [i for i in c[2].split(",") if i], "market_cap": c[3], "free_float": c[9]}
        meta["status"] = "ok"; meta["rows"] = len(out)
        return out, meta
    except Exception as e:  # noqa: BLE001
        meta.update(status="error", error=repr(e)[:200])
        return None, meta


def load_askanalyst():
    p = STATE / "latest_quarters.json"
    d = json.load(open(p)) if p.exists() else {}
    return {k.upper(): v for k, v in d.items()}, ("state/latest_quarters.json" if d else None)


# ------------------------------------------------------------------ classification
def instrument_type(sym, name, all_syms):
    n = name or ""
    if re.search(r"\(R\)|\bright", n, re.I):
        return "right"
    m = re.match(r"^(.+?)R\d?$", sym)
    if m and m.group(1) in all_syms and sym not in ("PAKQATAR",):
        return "right"
    if re.search(r"\(PRS?\)|\bpref|convt", n, re.I):
        return "preference"
    m = re.match(r"^(.+?)PS$", sym)
    if m and (m.group(1) in all_syms):
        return "preference"
    return "equity"


def build(offline=False):
    seed = load_seed()
    if not seed:
        sys.exit("no quotation seed in intel/inputs/")
    symlist, m_sym = fetch_symbols(offline)
    tdate, summary, m_sum = fetch_summary(offline)
    screener, m_scr = fetch_screener(offline)
    aa, aa_path = load_askanalyst()
    prev = json.load(open(OUT)) if OUT.exists() else None
    prev_syms = (prev or {}).get("symbols", {})
    # an offline / incomplete earlier run does not count as a baseline: nothing seen later is a "new listing" until a full run exists
    baseline = prev is None or (prev.get("baseline_run") and (prev.get("sources", {}).get("dps_market_summary", {}).get("status") != "ok"))
    today = datetime.now(PKT).date().isoformat()

    seed_rows = {r["symbol"]: r for r in seed["rows"]}
    seed_all = set(seed_rows)
    dps_syms = {}
    if symlist:
        for x in symlist:
            s = str(x.get("symbol") or x.get("s") or "").upper()
            if s:
                dps_syms[s] = x
    # sector-code -> name map learned from symbols that appear in both the summary and the seed
    votes = defaultdict(Counter)
    for s, r in seed_rows.items():
        if summary and s in summary and r["segment"] == "main_normal":
            votes[summary[s]["sector_code"]][clean_sector(r["sector"])] += 1
    code_name = {c: v.most_common(1)[0][0] for c, v in votes.items()}

    syms = {}
    def rec(s):
        return syms.setdefault(s, {"symbol": s, "flags": [], "sources": []})

    for s, r in seed_rows.items():
        x = rec(s)
        x.update(name_rough=r["name_rough"], sector=clean_sector(r["sector"]), segment=r["segment"], last_seen_quotation=seed["quotation_date"])
        x["sources"].append("quotation")
    if summary:
        for s, r in summary.items():
            if "-" in s or r["sector_code"] in ("40", "41", "36"):
                continue                      # futures / derivative contracts, not companies
            nm = code_name.get(r["sector_code"])
            if s in syms:
                x = syms[s]
            elif nm is not None and nm not in ("CLOSE - END MUTUAL FUND",) or s in dps_syms:
                x = rec(s)
                x["segment"] = "main_normal_unconfirmed"; x["sector"] = nm
                x["flags"].append("traded_but_not_in_quotation_seed")   # parse miss, new listing, ETF or debt: look at it
            else:
                continue                      # unmapped sector code with no DPS entry: ETF / debt / other instruments
            x.update(name=r["name"], sector_code=r["sector_code"], last_seen_traded=tdate, last_close=r["close"])
            x["sources"].append("market_summary")
    if screener:
        for s, r in screener.items():
            if s in syms:
                syms[s].update(indices=r["indices"], free_float_pct=r["free_float"], market_cap_screener=r["market_cap"], in_screener=True)
                syms[s]["sources"].append("screener")
    for s, x in dps_syms.items():
        if s in syms:
            if x.get("name"):
                syms[s]["name"] = x["name"]
            syms[s]["sources"].append("dps_symbols")
    # Ask Analyst coverage; also keep Ask Analyst symbols the exchange lists no longer show (stale or renamed)
    for s, a in aa.items():
        x = syms.get(s)
        if x is None:
            x = rec(s)
            x.update(segment="askanalyst_only", name=a.get("name"), sector=clean_sector(a.get("sector")))
            x["flags"].append("in_askanalyst_not_on_current_exchange_list")
        x["askanalyst"] = {"covered": True, "latest_is": a.get("is"), "latest_bs": a.get("bs")}
        x["sources"].append("askanalyst")

    all_syms = set(syms)
    for s, x in syms.items():
        x.setdefault("askanalyst", {"covered": False})
        x["name"] = x.get("name") or x.get("name_rough")
        nm = x.get("name") or ""
        x["instrument_type"] = instrument_type(s, x.get("name_rough") or nm, all_syms)
        sec = x.get("sector") or ""
        cc, mf = FIN_CLASS.get(sec, ("operating", "industrial"))
        x["company_class"], x["model_family"] = cc, mf
        if x["instrument_type"] in ("right", "preference"):
            x["scope"] = "tag_only"
        elif x["segment"] == "main_normal":
            x["scope"] = "in_scope"
        elif x["segment"] == "main_normal_unconfirmed":
            x["scope"] = "check"
        elif x["segment"] in ("main_noncompliant", "gem"):
            x["scope"] = "later"                       # kept in the master, not extracted yet
        else:
            x["scope"] = "check"
        if x["scope"] == "in_scope":
            x["data_route"] = "askanalyst" if x["askanalyst"]["covered"] else "dps_reports"
        # lifecycle
        old = prev_syms.get(s, {})
        x["first_seen"] = old.get("first_seen") or (today)
        if not baseline and not old:
            x["flags"].append("new_listing")
        elif old and "new_listing" in old.get("flags", []) and old.get("first_seen", "") >= (date.fromisoformat(today) - timedelta(days=30)).isoformat():
            x["flags"].append("new_listing")
        x.setdefault("last_seen_traded", old.get("last_seen_traded"))
        seen_now = bool(x.get("last_seen_traded") == tdate) if tdate else None
        x["absent_runs"] = 0 if (seen_now or "quotation" in x["sources"] or "dps_symbols" in x["sources"]) else old.get("absent_runs", 0) + (1 if (summary and not offline) else 0)
        x["status"] = "absent" if x["absent_runs"] >= ABSENT_RUNS else "listed"
        x["sources"] = sorted(set(x["sources"]))
        x["flags"] = sorted(set(x["flags"]))
    # symbols we knew about earlier that no source mentions now: keep, count the run
    for s, old in prev_syms.items():
        if s not in syms:
            old = dict(old)
            old["absent_runs"] = old.get("absent_runs", 0) + 1
            old["status"] = "absent" if old["absent_runs"] >= ABSENT_RUNS else old.get("status", "listed")
            old["sources"] = []
            syms[s] = old

    scope_c = Counter(x["scope"] for x in syms.values())
    insc = [x for x in syms.values() if x["scope"] == "in_scope"]
    doc = {
        "v": VERSION, "generated": now(), "baseline_run": baseline,
        "sources": {"quotation": {"file": seed["file"], "date": seed["quotation_date"], "sha256": seed["sha256"], "seed": seed["_path"]},
                    "dps_symbols": m_sym, "dps_market_summary": m_sum, "dps_screener": m_scr, "askanalyst": {"path": aa_path}},
        "sector_code_map": code_name,
        "counts": {"symbols": len(syms), "scope": dict(scope_c),
                   "in_scope_by_route": dict(Counter(x.get("data_route") for x in insc)),
                   "in_scope_by_class": dict(Counter(x["company_class"] for x in insc)),
                   "tag_only_types": dict(Counter(x["instrument_type"] for x in syms.values() if x["scope"] == "tag_only")),
                   "flags": dict(Counter(f for x in syms.values() for f in x["flags"])),
                   "new_listings": sorted(s for s, x in syms.items() if "new_listing" in x["flags"])},
        "symbols": dict(sorted(syms.items())),
    }
    json.dump(doc, open(OUT, "w"), indent=1)
    print(json.dumps(doc["counts"], indent=1))
    print("sources:", {k: v.get("status") for k, v in doc["sources"].items() if isinstance(v, dict)})
    return doc


if __name__ == "__main__":
    build(offline="--offline" in sys.argv)
