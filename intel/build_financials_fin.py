"""Standardise banks, insurers, modarabas, finance cos, REITs, funds -> state/intel_financials_fin.json (same record format as build_financials)."""
import json, re, sys, collections
from pathlib import Path
sys.path.insert(0, "intel"); import build_financials as bf
EX = Path("state/intel_extract"); OUT = Path("state/intel_financials_fin.json")
U = json.load(open("state/intel_universe.json")); U = U.get("symbols", U)
BANK = {"IS": [("markup_earned", r"^mark-?up\s*/\s*return\s*/\s*interest earned|^mark-?up/return/interest earned|^return on|^profit on"),
               ("markup_expensed", r"^mark-?up\s*/\s*return\s*/\s*interest expensed|^mark-?up/return/interest expensed"),
               ("net_markup_income", r"^net mark-?up\s*/\s*(return\s*/\s*)?interest income|^net mark-?up|^net profit / return"),
               ("non_markup_income", r"^total non[- ]?mark-?up\s*/?\s*(return\s*/\s*)?(interest )?income"),
               ("non_markup_expenses", r"^total non[- ]?mark-?up\s*/?\s*(return\s*/\s*)?(interest )?expenses|^operating expenses"),
               ("provisions", r"credit loss allowance and write|^provisions? (and|&) write|^provision"),
               ("pbt", r"^profit before (income )?tax(ation)?$|^\(?loss\)?.*before tax"), ("tax", r"^taxation$|^income tax"),
               ("pat", r"^profit after tax(ation)?|^net profit for"), ("eps", r"per share")],
        "BS": [("total_assets", r"^total assets"), ("total_liabilities", r"^total liabilities$"), ("total_equity", r"^net assets|^total equity$|^equity$|^shareholders'? equity"),
               ("deposits", r"^deposits"), ("advances", r"^advances"), ("investments", r"^investments"), ("borrowings", r"^borrowings"), ("cash", r"^cash and balances with treasury")],
        "CF": []}
INS = {"IS": [("net_premium", r"^net (insurance )?premium|^net premium"), ("net_claims", r"^net insurance claims|^net claims"), ("underwriting_result", r"^underwriting results?"),
              ("investment_income", r"^investment income"), ("pbt", r"^profit before (income )?tax(ation)?$|^profit before tax"), ("tax", r"^(income )?tax(ation)?( expense)?$"),
              ("pat", r"^profit after tax(ation)?|^profit for the"), ("eps", r"per share")],
       "BS": [("total_assets", r"^total assets$"), ("total_equity_liab", r"^total equity and liabilities"), ("total_equity", r"^total equity$|^total shareholders'? equity"),
              ("total_liabilities", r"^total liabilities$"), ("cash", r"^cash and bank")], "CF": []}
GEN = {"IS": [("income", r"^total (income|revenue)|^revenue|^total operating income|^income$"), ("pbt", r"^profit before (income )?tax(ation)?|^profit before"), ("tax", r"^(income )?tax(ation)?$|^taxation"),
              ("pat", r"^(net )?profit after tax|^profit for the|^net income|^net (profit|surplus)|^total comprehensive income"), ("eps", r"per share|per certificate|per unit")],
       "BS": [("total_assets", r"^total assets"), ("total_liabilities", r"^total liabilities$"), ("total_equity", r"^total equity$|^net assets|^total (certificate|unit)holders|^total shareholders'? equity|^equity$")], "CF": []}
def maps(cls): return BANK if cls == "bank" else INS if cls == "insurer" else GEN
def main(syms):
    res = {}
    for sym in syms:
        cls = U.get(sym, {}).get("company_class", "investment_finance"); bf.MAP = maps(cls)
        try: d = json.load(open(EX / f"{sym}.json"))
        except Exception: continue
        items = []
        for rid, rep in d.items():
            pe_note = None
            doc_unit = max([st["unit_multiplier"] or 1 for st in rep["statements"]] or [1])
            if not rep.get("period_end"):
                pd_ = bf.title_date(rep)
                if pd_: rep["period_end"] = pd_; pe_note = "parsed from statement title"
            for basis in ("consolidated", "standalone", "single_entity"):
                sts = {s["type"]: s for s in rep["statements"] if s["basis"] == basis}
                if not sts: continue
                rec = {"report": rid, "url": rep["url"], "period_end": rep["period_end"], "period_end_note": pe_note, "date_public": rep["date_public"], "kind": rep["kind"], "basis": basis, "ocr": rep["ocr"], "class": cls, "fields": {}, "checks": [], "pages": {}}
                for kind, s in sts.items():
                    rec["pages"][kind] = s["pdf_pages"]; unit = doc_unit
                    for f, x in bf.std(s, kind, rep["kind"], unit).items():
                        rec["fields"][f] = {"values": [None if v is None else v * unit for v in x["vals"]] if f != "eps" else x["vals"], "src_label": x["label"], "unit": unit if f != "eps" else 1}
                F = rec["fields"]; g = lambda f, i=0: (F.get(f) or {}).get("values", [None] * 4)[i]
                def chk(name, a, b, tol=0.01):
                    if a is None or b is None: rec["checks"].append({"name": name, "status": "skipped"}); return
                    rec["checks"].append({"name": name, "status": "pass" if abs(a - b) <= tol * max(abs(a), abs(b), 1) else "FAIL", "a": a, "b": b})
                if g("total_assets") is not None:
                    if g("total_equity_liab") is not None: chk("assets=equity+liabilities", g("total_assets"), g("total_equity_liab"))
                    elif g("total_liabilities") is not None and g("total_equity") is not None: chk("assets=liabilities+equity", g("total_assets"), g("total_liabilities") + g("total_equity"), 0.03 if cls == "insurer" else 0.01)
                t, pat, pb = g("tax"), g("pat"), g("pbt")
                if t is not None and pat is not None and pb is not None: chk("pbt+tax=pat", min((pb + t, pb - abs(t), pb - abs(t) * -1), key=lambda c: abs(c - pat)), pat, 0.02)
                if cls == "bank" and None not in (g("markup_earned"), g("markup_expensed"), g("net_markup_income")): chk("net markup = earned - expensed", g("markup_earned") - abs(g("markup_expensed")), g("net_markup_income"))
                bf.fix_units(sym, rec)
                fails = [c["name"] for c in rec["checks"] if c["status"] == "FAIL"]
                soft = fails and all(f == "pbt+tax=pat" for f in fails)          # associates, takaful and discontinued items sit between PBT and PAT
                rec["status"] = "review" if soft else "quarantine" if fails else "low_confidence" if rec["ocr"] else "ok"
                items.append(rec)
        bf.fix_units_peers(items)
        res[sym] = items
    OUT.write_text(json.dumps(res, indent=1))
    for cl in sorted({U.get(s, {}).get("company_class") for s in res}):
        ss = [s for s in res if U.get(s, {}).get("company_class") == cl]
        c = collections.Counter(r["status"] for s in ss for r in res[s]); print(cl, len(ss), dict(c))
if __name__ == "__main__":
    ex = {p.stem for p in EX.glob("*.json")}
    sy = sorted(s for s in ex if U.get(s, {}).get("company_class") not in (None, "operating"))
    main(sy)
