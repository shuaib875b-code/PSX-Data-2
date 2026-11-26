import json, sys, glob, datetime as dt
sys.path.insert(0, "model"); import compute_data as c
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
RAW = "/tmp/aa/raw"
P = json.load(open("state/intel_periods.json")); P["data"].update(json.load(open("state/intel_periods_fin.json"))["data"]); fin = json.load(open("state/intel_financials.json")); fin.update(json.load(open("state/intel_financials_fin.json")))
UNI = json.load(open("state/intel_universe.json")); UNI = UNI.get("symbols", UNI)
import glob; Q = {f.split("/")[-1][:-5]: json.load(open(f)) for f in glob.glob("state/intel_qual/*.json")}; idx = json.load(open("state/intel_raw/reports_text/index.json"))
for _f in glob.glob("state/intel_raw/reports_index/*.json"): idx.update(json.load(open(_f)))
wb = Workbook(); HF = Font(bold=True, color="FFFFFF"); HB = PatternFill("solid", fgColor="1F4E78")
def sheet(name, header, rows, widths=None):
    ws = wb.create_sheet(name); ws.append(header)
    for c_ in ws[1]: c_.font = HF; c_.fill = HB; c_.alignment = Alignment(wrap_text=True, vertical="top")
    import re as _re
    for r in rows: ws.append([_re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v) if isinstance(v, str) else v for v in r])
    ws.freeze_panes = "A2"
    for i, h in enumerate(header, 1): ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(11, min(40, len(str(h)) + 3)))
    ws.auto_filter.ref = ws.dimensions; return ws
FL = ["revenue","cost_of_sales","gross_profit","finance_cost","other_income","pbt_before_levy","levy","pbt","tax","pat","eps","cfo","cfi","cff","capex","dividends_paid","depreciation","markup_earned","markup_expensed","net_markup_income","non_markup_income","non_markup_expenses","provisions","net_premium","net_claims","underwriting_result","investment_income","income"]
BSF = ["total_assets","total_equity","current_assets","current_liab","cash","inventory","receivables","total_liabilities","deposits","advances","investments","borrowings"]
PREF = ["consolidated", "standalone", "single_entity"]
# --- quarterly (wide), preferred basis flagged
qrows = []; ttmrows = []; bsrows = []; aa_rows = []
for sym, bases in P["data"].items():
    pref = next((b for b in PREF if b in bases and bases[b]["quarters"]), None)
    if pref is None: continue
    IS = c.load_series(RAW, sym, "IS"); aa = {"revenue": None, "pat": None}
    for k, keys in (("revenue", ("net sales", "net revenue", "revenue", "sales", "rental income")), ("pat", ("profit after tax", "net profit", "profit for"))):
        for key in keys:
            for l, v in IS.items():
                if l.lower().startswith(key): aa[k] = v; break
            if aa[k]: break
    for b, x in bases.items():
        for key, d in x["quarters"].items():
            pe, q = key.split("|")
            qrows.append([sym, b, "yes" if b == pref else "", pe, q] + [d.get(f, {}).get("v") for f in FL] + ["; ".join(sorted({v["how"] for v in d.values()})), "; ".join(sorted({v["src"] for v in d.values()}))[:120]])
        if x["ttm"]: ttmrows.append([sym, b, "yes" if b == pref else "", x["ttm"]["end"]] + [x["ttm"]["fields"].get(f) for f in FL])
        for pe, d in x["balance_sheet"].items():
            bsrows.append([sym, b, "yes" if b == pref else "", pe] + [d.get(f, {}).get("v") for f in BSF] + ["; ".join(sorted({v["src"] for v in d.values()}))[:100]])
        # AA compare: discrete quarters
        for key, d in x["quarters"].items():
            pe, q = key.split("|"); pe_d = dt.date.fromisoformat(pe)
            for f in ("revenue", "pat"):
                if f in d and aa[f] and pe_d in aa[f]:
                    aa_rows.append([sym, b, pe, q, f, d[f]["v"], aa[f][pe_d], None, d[f]["src"][:60]])
# best basis per (sym, period, field)
best = {}
for r in aa_rows:
    k = (r[0], r[2], r[4]); d = abs(r[5] / r[6] - 1) if r[6] else 9
    if k not in best or d < best[k][0]: best[k] = (d, r)
aa_final = [v[1] for v in sorted(best.values(), key=lambda t: (t[1][0], t[1][2], t[1][4]))]
# sheets
ws = wb.active; ws.title = "README"
txt = [("PSX Investment Intelligence - extraction workbook (pilot + rollout, operating companies)", True),
 ("Generated from filings on the PSX financial-reports portal and DPS announcements; every figure keeps report id, basis and PDF page in the repo (state/intel_financials.json, intel_periods.json).", False),
 ("Scope: operating companies on the main board that were processed so far. Banks, insurers, modarabas, REITs and funds need their own templates (pending). Scanned (image) reports are flagged and not yet read.", False),
 ("Units: PKR million (EPS in PKR). Basis: consolidated preferred; standalone and single_entity kept; column 'Preferred basis' marks the one to use.", False),
 ("Quarterly = discrete quarter. Rule: reported quarter column where shown; else YTD(n) - YTD(n-1); Q4 = annual - 9M. Comparative columns fill gaps (marked 'comparative' in source).", False),
 ("TTM = latest 4 consecutive discrete quarters. Cash-flow items are derived the same way from YTD statements.", False),
 ("Checks: assets = equity+liabilities; PBT+levy+tax = PAT; revenue-cost = gross profit; (discrete quarter) + earlier YTD = YTD. Failed -> Quarantine sheet. Scanned (OCR) reports are flagged low confidence.", False),
 ("Second source: Ask Analyst quarterly figures (sheet AA_compare). Differences are shown, not overwritten.", False),
 ("Best_* sheets: merged table. Rule: filing and Ask Analyst agree within 1% = verified; only one source = single source (AA fills gaps, tagged); disagree = CONFLICT (AA shown provisionally, filing value on Conflicts sheet). Ask Analyst cash-flow lines are its own build-up.", False),
 ("Qualitative sheet = verbatim snippets with PDF page (reported facts). No analyst view is included yet; some snippets are noisy because annual reports are multi-column.", False)]
for t, b in txt:
    ws.append([t]); ws.cell(ws.max_row, 1).font = Font(bold=b, size=13 if b else 11); ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True)
ws.column_dimensions["A"].width = 130
sheet("Quarterly", ["Symbol","Basis","Preferred basis","Quarter end","FQ"] + FL + ["Derivation","Source report(s)"], qrows)
sheet("TTM", ["Symbol","Basis","Preferred basis","TTM end"] + FL, ttmrows)
sheet("BalanceSheet", ["Symbol","Basis","Preferred basis","Date"] + BSF + ["Source"], bsrows)
ws = sheet("AA_compare", ["Symbol","Basis (best match)","Quarter end","FQ","Field","Reports (PKR m)","Ask Analyst (PKR m)","Diff %","Source"], aa_final)
for i in range(2, ws.max_row + 1):
    ws.cell(i, 8).value = f"=IF(G{i}=0,\"\",F{i}/G{i}-1)"; ws.cell(i, 8).number_format = "0.0%"
chk = []; quar = []
for sym, recs in fin.items():
    for r in recs:
        for ck in r["checks"]:
            chk.append([sym, r["report"], r["basis"], r["period_end"], r["kind"], "OCR" if r["ocr"] else "", ck["name"], ck["status"], ck.get("a"), ck.get("b")])
        if r["status"] != "ok":
            quar.append([sym, r["report"], r["basis"], r["period_end"], r["status"], "; ".join(f"{c_['name']} ({c_['a']:.0f} vs {c_['b']:.0f})" for c_ in r["checks"] if c_["status"] == "FAIL") or ("scanned report: digits may be misread" if r["ocr"] else ""), r["url"]])
sheet("Checks", ["Symbol","Report","Basis","Period end","Kind","OCR","Check","Status","A","B"], chk)
sheet("Quarantine", ["Symbol","Report","Basis","Period end","Status","Reason","URL"], quar)
rep = []
for sym, d in idx.items():
    for k, v in d.items(): rep.append([sym, k, v.get("kind"), v.get("period_end"), v.get("date_public"), v.get("date_note"), v.get("source"), v.get("pages"), "yes" if v.get("ocr") else "", v.get("url")])
sheet("Report_index", ["Symbol","Report","Kind","Period end","Published","Date note","Source","Pages","OCR","URL"], rep)
qq = [[s, e["report"], i["topic"], i["page"], i["type"], i["text"][:1500]] for s, e in Q.items() for i in e["items"]]
sheet("Qualitative", ["Symbol","Report","Topic","PDF page","Type","Text (verbatim snippet)"], qq, {"Text (verbatim snippet)": 120})
rf = [[s, e["report"], n, v["pages_with_mention"], ", ".join(map(str, v["first_pages"]))] for s, e in Q.items() for n, v in e["red_flag_scan"].items()]
sheet("RedFlag_scan", ["Symbol","Report","Keyword group","Pages mentioning","First pages"], rf)

M = json.load(open("state/intel_merged.json")); MD = M["data"]
QF = ["revenue","cost_of_sales","gross_profit","finance_cost","other_income","pbt","tax","pat","eps","ebitda","depreciation","cfo","capex","markup_earned","markup_expensed","net_markup_income","non_markup_income","non_markup_expenses","provisions","net_premium","net_claims","underwriting_result","investment_income","income"]
BF = ["total_assets","total_equity","total_liabilities","current_assets","current_liab","cash","inventory","receivables","long_term_debt","short_term_debt","current_portion_ltd","deposits","advances","investments","borrowings"]
import collections as _co
bq, bb, bt, cov = [], [], [], []
for sym, x in MD.items():
    tot = _co.Counter()
    for d, fs in x["quarters"].items():
        st = _co.Counter(e["status"].split(":")[0].split(" (")[0] for e in fs.values()); tot.update(st)
        bq.append([sym, d] + [fs[f]["v"] if f in fs else None for f in QF] + [st["verified"], st["CONFLICT"], st["AA only"], st["filing only"]])
    for d, fs in x["balance_sheet"].items():
        bb.append([sym, d] + [fs[f]["v"] if f in fs else None for f in BF] + [", ".join(sorted({e["status"].split(":")[0].split(" (")[0] for e in fs.values()}))])
    if x["ttm"]:
        bt.append([sym, x["ttm"]["end"]] + [x["ttm"]["fields"].get(f, {}).get("v") for f in QF] + [min([v["verified_quarters"] for v in x["ttm"]["fields"].values()] or [0]), max([v["conflict_quarters"] for v in x["ttm"]["fields"].values()] or [0])])
    qs = sorted(x["quarters"]); n = sum(tot.values()) or 1
    cov.append([sym, UNI.get(sym, {}).get("company_class"), len(qs), qs[0] if qs else None, qs[-1] if qs else None, tot["verified"], tot["CONFLICT"], tot["AA only"], tot["filing only"], round(tot["verified"] / n, 2), "yes" if x["ttm"] else "no"])
sheet("Best_quarterly", ["Symbol","Quarter end"] + QF + ["#verified","#conflict","#AA only","#filing only"], bq)
sheet("Best_TTM", ["Symbol","TTM end"] + QF + ["min verified quarters","max conflict quarters"], bt)
sheet("Best_balance_sheet", ["Symbol","Date"] + BF + ["Status"], bb)
sheet("Coverage", ["Symbol","Class","# quarters","First","Latest","verified","conflict","AA only","filing only","verified share","TTM available"], cov)
sheet("Conflicts", ["Symbol","Date","Field","Filing value (PKR m)","AA value (PKR m)","Filing source"], [[c_["sym"], c_["date"], c_["field"], c_["filing"], c_["aa"], c_["filing_src"]] for c_ in M["conflicts"]])
for s_ in wb.worksheets:
    if s_.title.startswith("Best_"):
        for row in s_.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, float): cell.number_format = "#,##0.0"
for s in wb.worksheets:
    if s.title in ("Quarterly", "TTM", "BalanceSheet"):
        for row in s.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, float): cell.number_format = "#,##0.0"
RT = json.load(open("state/intel_ratios.json")); RT = RT.get("data", RT); GT = json.load(open("state/intel_gates.json")); GT = GT.get("data", GT)
rf = ["class","latest_quarter","bs_date","price","price_date","shares_source","market_cap_m","ttm_revenue","ttm_pat","ttm_eps","gross_margin","operating_margin","net_margin","roe","roa","debt_to_equity","interest_cover","current_ratio","cash_conversion","pe","pb","ev_ebitda","earnings_yield","fcf_yield","dps_ttm","dps_years_paid_of_5","payout","dividend_yield","scale_suspect"]
sheet("Ratios", ["Symbol"] + rf, [[k] + [v.get(f) for f in rf] for k, v in sorted(RT.items())])
STR = ["Value","Quality","Magic Formula","Momentum","Growth/GARP","Dividend","Low Volatility"]
sheet("Gates", ["Symbol","Class","Confidence grade"] + [x + " | status" for x in STR] + [x + " | reasons" for x in STR], [[k, g.get("class"), g.get("confidence_grade")] + [(g["gates"].get(x) or {}).get("status") for x in STR] + ["; ".join((g["gates"].get(x) or {}).get("reasons", [])) for x in STR] for k, g in sorted(GT.items())])
issues = [["Scanned reports","LUCK 7 quarterlies and ALAC 6 are images. Re-scan at 300 dpi did not fix LUCK; digits and units may still be misread. Marked low confidence; need manual check or another text source."],
 ["Portal gaps","Portal quarterly index skips some quarters; DPS announcements fill the newest ones; some DPS reports have no period date (unplaced)."],
 ["Unlabelled totals","OGDC balance sheets print totals without labels; total assets is now taken from the largest unlabelled total (derived, flagged). OGDC annual reports still lack it."],
 ["AA basis","AA appears consolidated for LUCK/FFC; AA cash flow is its own build-up (Mar-22 on), so CF is not compared."],
 ["Banks/insurers","MEBL and ALAC need their own templates; only qualitative evidence done."],
 ["Qualitative","Snippets are verbatim but multi-column PDFs make some noisy; analyst view/notes still to be written after review."]]
sheet("Issues", ["Issue","Detail"], issues, {"Detail": 120})
wb.move_sheet("Issues", offset=-(len(wb.sheetnames) - 2))
wb.save("PSX_extraction_all.xlsx")
n = len(aa_final); ok = sum(1 for r in aa_final if r[6] and abs(r[5] / r[6] - 1) <= 0.01)
print("AA compare rows", n, "within 1%:", ok); print(wb.sheetnames)
