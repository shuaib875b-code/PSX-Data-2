"""Phase 3 (test version): ratios, data-confidence grade and the seven eligibility gates, from state/intel_merged.json + prices.
Thresholds are STARTING values for testing (roadmap section 6), not calibrated. Governance checks are not loaded yet -> flagged 'unchecked'.
Output: state/intel_ratios.json, state/intel_gates.json"""
import os, json, math, statistics, datetime as dt
import pandas as pd
M = json.load(open("state/intel_merged.json"))["data"]
U = json.load(open("state/intel_universe.json")); U = U.get("symbols", U)
px = pd.read_csv("tech/ohlcv_universe.csv", usecols=["date", "symbol", "close", "volume"], parse_dates=["date"])
import glob
_extra = [pd.read_csv(f, usecols=["date", "symbol", "close", "volume"], parse_dates=["date"]) for f in sorted(glob.glob("state/intel_prices/part_*.csv")) if os.path.getsize(f) > 100]
if _extra: px = pd.concat([px] + _extra).drop_duplicates(["date", "symbol"])    # backfilled from DPS market-summary files for companies outside the daily universe
PX = {s: g.sort_values("date").reset_index(drop=True) for s, g in px.groupby("symbol")}
TODAY = px.date.max()
FIN_CLS = {"bank", "insurer", "investment_finance", "leasing", "modaraba", "reit", "closed_end_fund"}
def val(d, f): return (d.get(f) or {}).get("v") if d else None
def div(a, b): return None if a is None or b in (None, 0) else a / b
def ttm_from(q, dates, f):
    v = [val(q.get(d), f) for d in dates]
    return None if any(x is None for x in v) else sum(v)
def shift(d, m):
    import calendar; mm = d.month - 1 + m; y, mo = d.year + mm // 12, mm % 12 + 1
    return dt.date(y, mo, calendar.monthrange(y, mo)[1])
def grade(q, dates, bsd, latest, cls="operating"):
    """A official+reconciled+recent, B official minor gaps, C estimates/incomplete, D weak/stale/inconsistent"""
    st = []
    for d in dates:
        for f in (("revenue", "pbt", "pat") if cls == "operating" else ("pbt", "pat")):      # banks/insurers: AA has no like-for-like revenue line, so grade on profit lines AA also carries
            if f in (q.get(d) or {}): st.append(q[d][f]["status"])
    for f in ("total_assets", "total_equity"):
        if bsd and f in bsd: st.append(bsd[f]["status"])
    if not st: return "D", "no data"
    age = (TODAY.date() - latest).days
    ver = sum(s.startswith(("verified", "minor")) for s in st) / len(st); conf = sum(s.startswith("CONFLICT") for s in st) / len(st)
    if age > 400 or conf > 0.3: return "D", f"stale {age}d / conflicts {conf:.0%}"
    if age <= 200 and ver >= 0.8 and conf == 0: return "A", f"{ver:.0%} cross-verified, latest {age}d old"
    if ver >= 0.5 and conf <= 0.1: return "B", f"{ver:.0%} cross-verified"
    return "C", f"{ver:.0%} cross-verified, {conf:.0%} conflicts, rest single-source"
DPSC = json.load(open("state/intel_dps_company.json")).get("symbols", {})
import sys; sys.path.insert(0, "model")
def AADPS(sym):
    try:
        from compute_data import load_series
        v = load_series("/tmp/aa/raw", sym, "IS").get("DPS") or {}
        return {k: float(x) for k, x in v.items() if x is not None} if isinstance(v, dict) else None
    except Exception: return None
def AAPU(sym):
    try:
        from compute_data import load_series
        b = load_series("/tmp/aa/raw", sym, "BS"); v = b.get("Paid-up capital") or {}
        ks = sorted(v) if isinstance(v, dict) else []
        return float(v[ks[-1]]) if ks else None
    except Exception: return None
R, G = {}, {}
for sym, x in M.items():
    cls = U.get(sym, {}).get("company_class", "operating"); q = {dt.date.fromisoformat(k): v for k, v in x["quarters"].items()}; bs = {dt.date.fromisoformat(k): v for k, v in x["balance_sheet"].items()}
    if not q: continue
    ds = sorted(q); latest = ds[-1]; last4 = [shift(latest, -3 * i) for i in range(4)]; prev4 = [shift(latest, -12 - 3 * i) for i in range(4)]
    if not all(d in q for d in last4): last4 = None
    def okbs(b):
        e, a = val(b, "total_equity"), val(b, "total_assets")
        if e is None or a is None or not (0 < e < a): return False
        return not str((b.get("total_assets") or {}).get("status", "")).startswith("filing only") or not str((b.get("total_equity") or {}).get("status", "")).startswith("filing only")
    good = [d for d in sorted(bs, reverse=True) if okbs(bs[d])]
    # prefer the newest sheet that is not filing-only on both lines; else newest sane one
    bsdate = good[0] if good else (max(bs) if bs else None); bsd = bs.get(bsdate) if bsdate else None
    t = lambda f, dd=last4: ttm_from(q, dd, f) if dd else None
    r = {"class": cls, "latest_quarter": str(latest), "bs_date": str(bsdate) if bsdate else None}
    for f in ("revenue", "gross_profit", "pbt", "pat", "eps", "finance_cost", "cfo", "capex", "ebitda", "depreciation", "net_markup_income", "net_premium", "income"):
        r["ttm_" + f] = t(f)
    for f in ("total_assets", "total_equity", "total_liabilities", "current_assets", "current_liab", "cash"): r[f] = val(bsd, f)
    debts = [val(bsd, f) for f in ("long_term_debt", "short_term_debt", "current_portion_ltd")]
    r["debt"] = sum(v for v in debts if v is not None) if any(v is not None for v in debts) else None
    # price, implied shares, market cap
    p = PX.get(sym); price = float(p.close.iloc[-1]) if p is not None and len(p) else None
    r["price"] = price; r["price_date"] = str(p.date.iloc[-1].date()) if p is not None and len(p) else None
    dd = DPSC.get(sym, {}).get("equity") or {}
    sh = None; r["shares_source"] = None
    if dd.get("shares"): sh = dd["shares"] / 1e6; r["shares_source"] = "DPS company page (equity block)"
    else:
        pu = AAPU(sym)
        if pu: sh = pu / 10.0; r["shares_source"] = "AA paid-up capital / face value 10 (assumed, flagged)"
        elif r["ttm_eps"] and abs(r["ttm_eps"]) > 0.01 and r["ttm_pat"]: sh = div(r["ttm_pat"], r["ttm_eps"]); r["shares_source"] = "implied PAT/EPS (weak, flagged)"
    r["implied_shares_m"] = sh if sh and sh > 0 else None
    mcap = price * r["implied_shares_m"] if price and r["implied_shares_m"] else None; r["market_cap_m"] = mcap
    ebit = (r["ttm_pbt"] + r["ttm_finance_cost"]) if r["ttm_pbt"] is not None and r["ttm_finance_cost"] is not None else ((r["ttm_ebitda"] - abs(r["ttm_depreciation"])) if r["ttm_ebitda"] is not None and r["ttm_depreciation"] is not None else None)
    if ebit is not None and r["ttm_finance_cost"] is not None and r["ttm_pbt"] is not None: ebit = r["ttm_pbt"] + abs(r["ttm_finance_cost"])
    r["ttm_ebit"] = ebit
    ev = (mcap + (r["debt"] or 0) - (r["cash"] or 0)) if mcap is not None else None; r["ev_m"] = ev
    r.update({"gross_margin": div(r["ttm_gross_profit"], r["ttm_revenue"]), "operating_margin": div(ebit, r["ttm_revenue"]), "net_margin": div(r["ttm_pat"], r["ttm_revenue"]),
              "roe": div(r["ttm_pat"], r["total_equity"]), "roa": div(r["ttm_pat"], r["total_assets"]), "debt_to_equity": div(r["debt"], r["total_equity"]),
              "interest_cover": div(ebit, abs(r["ttm_finance_cost"]) if r["ttm_finance_cost"] else None), "current_ratio": div(r["current_assets"], r["current_liab"]),
              "cash_conversion": (lambda c: c if c is not None and -5 < c < 20 else None)(div(r["ttm_cfo"], r["ttm_pat"])), "pe": div(price, r["ttm_eps"]), "pb": div(mcap, r["total_equity"]),
              "ev_ebitda": div(ev, r["ttm_ebitda"]), "earnings_yield": div(ebit, ev), "fcf": (r["ttm_cfo"] + (-abs(r["ttm_capex"]))) if r["ttm_cfo"] is not None and r["ttm_capex"] is not None else None})
    r["fcf_yield"] = div(r["fcf"], mcap)
    r["scale_suspect"] = bool(r["roe"] is not None and abs(r["roe"]) > 3) or bool(r["roa"] is not None and abs(r["roa"]) > 1)
    if r["scale_suspect"]:
        for k in ("roe", "roa", "net_margin", "gross_margin", "operating_margin", "pe", "ev_ebitda", "earnings_yield", "fcf_yield", "cash_conversion"): r[k] = None
    nwc = (r["current_assets"] - r["current_liab"]) if r["current_assets"] is not None and r["current_liab"] is not None else None
    nfa = (r["total_assets"] - r["current_assets"]) if r["total_assets"] is not None and r["current_assets"] is not None else None
    r["roc_magic"] = div(ebit, (nwc + nfa)) if nwc is not None and nfa is not None and (nwc + nfa) > 0 else None
    r["roc_note"] = "net fixed assets proxied by total assets - current assets"
    # growth
    pv = lambda f: ttm_from(q, prev4, f) if all(d in q for d in prev4) else None
    r["revenue_growth"] = div((r["ttm_revenue"] - pv("revenue")) if r["ttm_revenue"] is not None and pv("revenue") else None, abs(pv("revenue")) if pv("revenue") else None)
    r["eps_growth"] = div((r["ttm_eps"] - pv("eps")) if r["ttm_eps"] is not None and pv("eps") else None, abs(pv("eps")) if pv("eps") else None)
    pats = [val(q.get(d), "pat") for d in [shift(latest, -3 * i) for i in range(8)]]
    pats = [v for v in pats if v is not None]; r["quarters_profit_8"] = f"{sum(v > 0 for v in pats)}/{len(pats)}"
    r["earnings_cv"] = (statistics.pstdev(pats) / abs(statistics.mean(pats))) if len(pats) >= 6 and statistics.mean(pats) else None
    r["quarters_available"] = len(ds)
    # price-based
    if p is not None and len(p) > 60:
        c = p.close; ret = lambda n: (c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else None
        r.update({"ret_3m": ret(63), "ret_6m": ret(126), "ret_12m": ret(252), "ma50": float(c.tail(50).mean()), "ma200": float(c.tail(200).mean()) if len(c) >= 200 else None,
                  "vol_90d": float(c.pct_change().tail(90).std() * math.sqrt(252)), "vol_1y": float(c.pct_change().tail(252).std() * math.sqrt(252)) if len(c) > 252 else None,
                  "max_drawdown_1y": float((c.tail(252) / c.tail(252).cummax() - 1).min()), "avg_value_3m_pkr_m": float((p.close * p.volume).tail(63).mean() / 1e6), "price_days": int(len(p))})
    g_, why = grade(q, last4 or ds[-4:], bsd, latest, r.get("class", "operating")); r["confidence_grade"] = g_; r["confidence_note"] = why
    R[sym] = r
    # ---------- gates ----------
    fin = cls in FIN_CLS; gate = {}
    def put(name, status, reasons): gate[name] = {"status": status, "reasons": reasons}
    prof = r["ttm_pat"] is not None and r["ttm_pat"] > 0
    unk = "governance not loaded" ; stale = g_ == "D"
    # Value
    rs = []; st = "Eligible"
    if r["ttm_pat"] is None: st = "Excluded"; rs.append("no 4-quarter earnings")
    elif not prof: st = "Excluded"; rs.append("loss-making on TTM (multiples not forecastable)")
    if r["total_equity"] is not None and r["total_equity"] <= 0: st = "Excluded"; rs.append("non-positive equity")
    if st == "Eligible" and stale: st = "Watchlist"; rs.append("data confidence D")
    if st == "Eligible" and r.get("scale_suspect"): st = "Watchlist"; rs.append("TTM scale suspect (unit error in a recent quarter)")
    if st == "Eligible" and (r["pe"] is None and r["pb"] is None): st = "Watchlist"; rs.append("no price/shares to value")
    put("Value", st, rs + [unk])
    # Quality
    rs = []; st = "Eligible"; qp = r["quarters_profit_8"]
    n_pos, n_all = map(int, qp.split("/"))
    if n_all < 6: st = "Watchlist"; rs.append("less than 6 quarters of history")
    elif n_pos < 6: st = "Excluded"; rs.append(f"profitable in only {qp} of last quarters")
    if r["cash_conversion"] is not None and prof and r["cash_conversion"] < 0.5 and not fin: st = "Watchlist" if st == "Eligible" else st; rs.append(f"cash conversion {r['cash_conversion']:.2f} below 0.5")
    if r["debt_to_equity"] is not None and r["debt_to_equity"] > 1.5 and not fin: st = "Watchlist" if st == "Eligible" else st; rs.append(f"debt/equity {r['debt_to_equity']:.2f} above 1.5")
    if st == "Eligible" and stale: st = "Watchlist"; rs.append("data confidence D")
    put("Quality", st, rs + [unk])
    # Magic Formula
    if fin: put("Magic Formula", "Excluded", ["financial company: separate treatment"])
    else:
        rs = []; st = "Eligible"
        if ebit is None or ebit <= 0: st = "Excluded"; rs.append("EBIT missing or not positive")
        if ev is None or ev <= 0: st = "Excluded"; rs.append("enterprise value not available")
        if r["roc_magic"] is None and st == "Eligible": st = "Watchlist"; rs.append("capital base not available")
        if st == "Eligible" and (stale or g_ == "C"): st = "Watchlist"; rs.append(f"statement confidence {g_}")
        put("Magic Formula", st, rs)
    # Price-based
    pdays = r.get("price_days", 0); liq = r.get("avg_value_3m_pkr_m")
    if pdays < 252: pst, prs = "Excluded", [f"only {pdays} trading days of price history" if pdays else "no price history in the loaded file"]
    elif liq is not None and liq < 2: pst, prs = "Excluded", [f"3-month average traded value PKR {liq:.1f}m below 2m"]
    elif liq is not None and liq < 10: pst, prs = "Watchlist", [f"thin trading: PKR {liq:.1f}m a day"]
    else: pst, prs = "Eligible", []
    put("Momentum", pst, prs + ["prices not yet checked for corporate-action adjustment", "KSE-100 history missing for relative strength"])
    # Growth
    rs = []; st = "Eligible"
    if not prof: st = "Excluded"; rs.append("no positive earnings")
    elif (r["eps_growth"] is None and r["revenue_growth"] is None): st = "Watchlist"; rs.append("growth not measurable (needs 8 quarters)")
    elif (r["eps_growth"] or 0) <= 0 and (r["revenue_growth"] or 0) <= 0: st = "Excluded"; rs.append("no growth in EPS or revenue")
    if st == "Eligible" and r["pe"] is not None and r["pe"] > 25: st = "Watchlist"; rs.append(f"P/E {r['pe']:.1f} above 25")
    if st == "Eligible" and stale: st = "Watchlist"; rs.append("data confidence D")
    put("Growth/GARP", st, rs + ["organic vs one-off growth not decomposed", unk])
    # Dividend
    dps = AADPS(sym); r["dps_ttm"] = r["dps_years_paid_of_5"] = r["payout"] = r["dividend_yield"] = None
    if dps:
        dts = sorted(dps); lastd = dts[-1]
        wins = []
        for i in range(5):
            w = [shift(lastd, -3 * (4 * i + j)) for j in range(4)]
            if all(x in dps for x in w): wins.append(sum(dps[x] for x in w))
        if wins:
            r["dps_ttm"] = wins[0]; r["dps_years_paid_of_5"] = f"{sum(1 for x in wins if x > 0)}/{len(wins)}"
            r["payout"] = div(wins[0], r["ttm_eps"]) if r["ttm_eps"] and r["ttm_eps"] > 0 else None
            r["dividend_yield"] = div(wins[0], price) if price else None
            paid = sum(1 for x in wins if x > 0); rs = []
            if len(wins) < 3: st = "Watchlist"; rs.append("under 3 years of dividend history")
            elif paid < len(wins) - 1: st = "Excluded"; rs.append(f"paid in only {paid} of {len(wins)} years")
            elif wins[0] <= 0: st = "Excluded"; rs.append("no dividend in latest 12 months")
            else:
                st = "Eligible"
                if r["payout"] is not None and r["payout"] > 1: st = "Watchlist"; rs.append(f"payout {r['payout']:.0%} above 100%")
                if r["payout"] is None: st = "Watchlist"; rs.append("payout not measurable (no positive EPS)")
                if wins[0] < wins[-1] * 0.5 and len(wins) >= 3: st = "Watchlist"; rs.append("dividend more than halved vs oldest year")
            if st == "Eligible" and stale: st = "Watchlist"; rs.append("data confidence D")
            put("Dividend", st, rs + ["source: Ask Analyst DPS (declared per period), not a PSX payout record"])
        else: put("Dividend", "Not assessed", ["Ask Analyst DPS has gaps in the last 4 quarters"])
    else: put("Dividend", "Not assessed", ["no dividend history in Ask Analyst for this company"])
    # Low vol
    rs = []; st = pst
    if st != "Excluded" and r.get("vol_1y") is None: st = "Watchlist"; rs.append("under 1 year of volatility data")
    if r["total_equity"] is not None and r["total_equity"] <= 0: st = "Excluded"; rs.append("non-positive equity")
    if st == "Eligible" and r["debt_to_equity"] is not None and r["debt_to_equity"] > 2 and not fin: st = "Watchlist"; rs.append("high leverage")
    put("Low Volatility", st, prs + rs + ["beta needs KSE-100 history", unk])
    G[sym] = {"class": cls, "confidence_grade": g_, "gates": gate}
json.dump(R, open("state/intel_ratios.json", "w"), separators=(",", ":"), default=lambda o: None)
json.dump(G, open("state/intel_gates.json", "w"), separators=(",", ":"))
import collections
for s in ("Value", "Quality", "Magic Formula", "Momentum", "Growth/GARP", "Low Volatility"):
    print(s, dict(collections.Counter(g["gates"][s]["status"] for g in G.values())))
print("grades", dict(collections.Counter(g["confidence_grade"] for g in G.values())), len(G))
