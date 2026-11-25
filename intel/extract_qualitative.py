"""Qualitative evidence from latest annual report text -> state/intel_qualitative.json.
Everything is a verbatim snippet with report id + PDF page ("reported"). No analyst view is generated here."""
import json, gzip, re, sys
idx = json.load(open("state/intel_raw/reports_text/index.json"))
MARK = re.compile(r"\[\[PDFPAGE ([^\]]+)\]\]")
def load(sym):
    ann = sorted([(k, v) for k, v in idx[sym].items() if v.get("kind") == "annual"], key=lambda kv: kv[1].get("period_end") or "")
    k, v = ann[-1]
    t = gzip.decompress(open(f"state/intel_raw/reports_text/{sym}/{k}.txt.gz", "rb").read()).decode()
    pages = t.split("\f"); out = []
    for p in pages:
        m = MARK.search(p); out.append((m.group(1) if m else "?", re.sub(r"[ \t]{2,}", " ", MARK.sub("", p))))
    return k, v, out
def clean(s, n): return re.sub(r"\s+", " ", s).strip()[:n]
def sents(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)) if 6 <= len(s.split()) <= 60]
def main(syms):
    res = {}
    for sym in syms:
        try: k, v, P = load(sym)
        except Exception as ex: print(sym, "no annual text", ex); continue
        ev = {"report": k, "period_end": v.get("period_end"), "url": v["url"], "ocr": v.get("ocr"), "items": []}
        def add(topic, pg, text, kind="reported"): ev["items"].append({"topic": topic, "page": pg, "text": text, "type": kind})
        # business profile
        for pg, t in P[:90]:
            m = re.search(r"(company profile|company overview|about (us|the company)|nature of (business|operations)|corporate profile)", t, re.I)
            body = t[m.end():m.end() + 900] if m else ""
            if m and len(t) > 800 and sum(c.isdigit() for c in body) < 0.04 * max(len(body), 1) and not re.search(r"contents|investor relations", t[:600], re.I):
                add("business", pg, clean(body, 900)); break
        # subsidiaries / associates
        n = 0
        for pg, t in P:
            if re.search(r"subsidiar|associate|joint venture", t[:1800], re.I) and re.search(r"(holding|ownership|%)", t[:3000]) and re.search(r"(list of|group structure|related|investment in|principal activit)", t[:2500], re.I):
                add("subsidiaries", pg, clean(t, 700)); n += 1
                if n >= 2: break
        # shareholding
        for pg, t in P:
            if re.search(r"pattern of shareholding|categories of shareholders", t[:1500], re.I) and re.search(r"%|percentage", t, re.I):
                rows = [clean(l, 140) for l in t.split("\n") if re.search(r"(director|associated compan|government|foreign|mutual|insurance|bank|modaraba|public|sponsor|general|individual|nit|ici)", l, re.I) and re.search(r"\d", l)]
                if rows: add("ownership", pg, " | ".join(rows[:14])); break
        # board
        for pg, t in P[:60]:
            names = re.findall(r"\b(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Syed|Muhammad|Mian|Sheikh)\s+[A-Z][A-Za-z\.\- ]{3,35}", t)
            if re.search(r"board of directors|directors", t[:900], re.I) and len(set(names)) >= 5:
                add("management_board", pg, "; ".join(dict.fromkeys(clean(x, 40) for x in names))[:700]); break
        # chairman / CEO message and outlook sentences
        for pg, t in P:
            if re.search(r"(chairman|ceo|chief executive)[’'`s]*\s*(review|message|statement|address)|directors.? report", t[:600], re.I) and not re.search(r"compliance|checklist|index", t[:600], re.I):
                add("management_message_start", pg, clean(t, 500)); break
        out = []
        for pg, t in P:
            if re.search(r"outlook|way forward|future prospects|going forward", t, re.I):
                for s in sents(t):
                    if re.search(r"(outlook|expect|anticipate|going forward|forecast|will continue|plans? to|aims? to|projected|likely)", s, re.I) and not re.search(r"forward-looking|risks, uncertainties", s, re.I):
                        out.append((pg, s))
        for pg, s in out[:8]: add("outlook_statement", pg, s)
        # auditor opinion / going concern / KAM
        for pg, t in P:
            if re.search(r"independent auditor.?s report", t[:700], re.I) and re.search(r"opinion", t, re.I):
                low = t.lower()
                kind = "qualified/adverse/disclaimer language present" if re.search(r"qualified opinion|adverse opinion|disclaimer of opinion|basis for qualified", low) else "no qualification language found (check)"
                add("auditor_opinion", pg, f"{kind}. " + clean(t, 450)); break
        gc = [(pg, s) for pg, t in P for s in sents(t) if re.search(r"material uncertainty|going concern", s, re.I) and not re.search(r"we are required|management is responsible|if we conclude|conclude on the appropriateness|responsib", s, re.I)]
        for pg, s in gc[:4]: add("going_concern_mention", pg, s)
        flags = {"going concern / material uncertainty": r"material uncertainty", "emphasis of matter": r"emphasis of matter", "qualified/adverse opinion": r"qualified opinion|adverse opinion|disclaimer of opinion",
                 "default / overdue": r"\bdefault(ed)?\b|overdue", "tax dispute / litigation": r"tax (demand|dispute|litigation)|contingenc", "pledged shares": r"pledg", "change of auditor": r"change of auditor|appointment of auditor|retire", "related party transactions": r"related part(y|ies)"}
        cnt = {}
        for name, pat in flags.items():
            pgs = [pg for pg, t in P if re.search(pat, t, re.I)]
            cnt[name] = {"pages_with_mention": len(pgs), "first_pages": pgs[:6]}
        ev["red_flag_scan"] = cnt
        res[sym] = ev
    import os; os.makedirs("state/intel_qual", exist_ok=True)
    for sym_, e_ in res.items(): json.dump(e_, open(f"state/intel_qual/{sym_}.json", "w"), separators=(",", ":"))
    for s, e in res.items(): print(s, e["report"], {t: sum(1 for i in e["items"] if i["topic"] == t) for t in sorted({i["topic"] for i in e["items"]})})
main(sys.argv[1].split(",") if len(sys.argv) > 1 else ["LUCK", "OGDC", "FFC", "ILP", "SYS", "MEBL", "KOHP", "ALAC"])
