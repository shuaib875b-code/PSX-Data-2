"""Download filed reports for chosen companies and keep their text (with page breaks) + provenance.
usage: python intel/fetch_reports_pilot.py SYMBOLS [--annual N] [--quarterly N]
Sources: PSX financial reports portal (state/intel_filings_index.json) and DPS announcement documents
(state/intel_dps_company.json) for anything newer than the portal lists.
Output: state/intel_raw/reports_text/<SYM>/<key>.txt.gz  (pages separated by form feed) and
        state/intel_raw/reports_text/index.json  (url, sha256 of the PDF, bytes, pages, chars, kind, period, posting/announcement date, ocr flag).
PDFs themselves are not kept in the repo; the hash identifies exactly what was read."""
import gzip, hashlib, json, re, subprocess, sys, time, tempfile, os
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = STATE / "intel_raw" / "reports_text"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def dl(url, tries=4):
    last = None
    for a in range(tries):
        try:
            time.sleep(1.0)
            r = requests.get(url, headers=UA, timeout=240)
            if r.status_code in (429, 503):
                time.sleep(10 * (a + 1)); continue
            return r
        except Exception as e:  # noqa
            last = e; time.sleep(6 * (a + 1))
    raise last or RuntimeError("download failed")


def page_sizes(path):
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "100000", path], capture_output=True).stdout.decode("utf8", "ignore")
    return {int(m.group(1)): (float(m.group(2)), float(m.group(3))) for m in re.finditer(r"Page\s+(\d+)\s+size:\s+([\d.]+) x ([\d.]+)", out)}


def is_spread(path, n, w, h):
    """Annual reports are often printed as two-page spreads on one landscape sheet. A spread has a clean gutter at the
    centre (almost no word crosses it) and text on both sides; a wide single page does not."""
    if w / h < 1.25:
        return False
    x = subprocess.run(["pdftotext", "-bbox", "-f", str(n), "-l", str(n), path, "-"], capture_output=True).stdout.decode("utf8", "ignore")
    ws = [(float(a), float(b)) for a, b in re.findall(r'xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)"', x)]
    if len(ws) < 40:
        return False
    c = w / 2
    cross = sum(1 for a, b in ws if a < c - 2 and b > c + 2)
    left = sum(1 for a, b in ws if b <= c + 2)
    right = sum(1 for a, b in ws if a >= c - 2)
    return cross / len(ws) < 0.01 and left / len(ws) > 0.25 and right / len(ws) > 0.25


def to_text(pdf_bytes):
    """Text per PDF page; a spread page is read as two pages (left then right). Each page starts with a marker line
    [[PDFPAGE n]] or [[PDFPAGE n L]] / [[PDFPAGE n R]] so every later figure can cite its page."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.pdf"); open(p, "wb").write(pdf_bytes)
        sizes = page_sizes(p)
        parts, spreads, ocr_pages = [], 0, 0
        for n in sorted(sizes):
            w, h = sizes[n]
            def one(tag, extra):
                t = subprocess.run(["pdftotext", "-layout", "-f", str(n), "-l", str(n)] + extra + [p, "-"], capture_output=True).stdout.decode("utf8", "ignore").replace("\f", "")
                return f"[[PDFPAGE {n}{tag}]]\n" + t
            if is_spread(p, n, w, h):
                spreads += 1
                parts.append(one(" L", ["-x", "0", "-y", "0", "-W", str(int(w / 2)), "-H", str(int(h))]))
                parts.append(one(" R", ["-x", str(int(w / 2)), "-y", "0", "-W", str(int(w / 2)), "-H", str(int(h))]))
            else:
                parts.append(one("", []))
        txt = "\f".join(parts) + "\f"
        pages = len(parts)
        body = sum(len(re.sub(r"\s", "", x)) for x in parts)
        ocr = False
        if pages and body / max(pages, 1) >= 400 and os.environ.get("MIXED_OCR", "1") == "1":     # text layer present, but statement pages can be pictures: OCR only the near-empty pages
            weak = [i for i, x in enumerate(parts) if len(re.sub(r"\s|\[\[PDFPAGE[^\]]*\]\]", "", x)) < 150 and " L" not in x[:20] and " R" not in x[:20]][:120]
            if weak and len(weak) < 0.6 * pages:
                from concurrent.futures import ThreadPoolExecutor
                env = dict(os.environ, OMP_THREAD_LIMIT="1")
                def runp(i):
                    m = re.match(r"\[\[PDFPAGE (\d+)", parts[i]); n = int(m.group(1))
                    try:
                        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(n), "-l", str(n), p, os.path.join(d, f"mx{n:04d}")], check=False)
                        f = [x for x in os.listdir(d) if x.startswith(f"mx{n:04d}") and x.endswith(".png")]
                        t = subprocess.run(["tesseract", os.path.join(d, f[0]), "-", "--psm", "6"], capture_output=True, env=env, timeout=240).stdout.decode("utf8", "ignore") if f else ""
                    except Exception: t = ""
                    return i, t
                with ThreadPoolExecutor(4) as ex:
                    for i, t in ex.map(runp, weak):
                        if len(t.strip()) > 100: parts[i] = parts[i].split("\n", 1)[0] + "\n" + t
                txt = "\f".join(parts) + "\f"
        if pages and body / max(pages, 1) < 400:       # scanned: no usable text layer
            ocr = True
            if os.environ.get("NO_OCR") == "1":
                return "\f".join(f"[[PDFPAGE {i + 1}]]" for i in range(pages)) + "\f", pages, True, spreads
            dpi = os.environ.get("OCR_DPI", "200"); maxp = int(os.environ.get("OCR_MAXPAGES", "0")) or None
            cmd = ["pdftoppm", "-r", dpi, "-gray", "-png"] + (["-l", str(maxp)] if maxp else []) + [p, os.path.join(d, "pg")]
            subprocess.run(cmd, check=False)
            files = sorted(x for x in os.listdir(d) if x.startswith("pg") and x.endswith(".png"))
            from concurrent.futures import ThreadPoolExecutor
            env = dict(os.environ, OMP_THREAD_LIMIT="1")
            def run(f):
                try: return subprocess.run(["tesseract", os.path.join(d, f), "-", "--psm", "6"], capture_output=True, env=env, timeout=240).stdout.decode("utf8", "ignore")
                except Exception: return ""
            with ThreadPoolExecutor(4) as ex:
                texts = list(ex.map(run, files))
            out = [f"[[PDFPAGE {i + 1}]]\n" + t for i, t in enumerate(texts)]
            out += [f"[[PDFPAGE {i + 1}]]" for i in range(len(out), pages)]      # pages beyond the OCR limit: marker only
            txt = "\f".join(out) + "\f"
            pages = len(out)
        return txt, pages, ocr, spreads


def pick(sym, nA, nQ, F, D):
    reps = (F.get(sym) or {}).get("reports") or []
    ann = sorted([r for r in reps if r["kind"] == "annual"], key=lambda r: r["period_end"] or r["period_label"])[-nA:]
    qtr = sorted([r for r in reps if r["kind"] == "quarterly"], key=lambda r: r["period_end"] or "")[-nQ:]
    jobs = [dict(source="portal", key=f"portal_{r['portal_id']}", url=r["url"], kind=r["kind"], period_end=r["period_end"], date_public=r["posting_date"], date_note=r["posting_date_note"], title=None) for r in ann + qtr]
    newest = max([r["posting_date"] for r in reps if r["posting_date"]] or [""])
    seen_ids = set(); portal_periods = {r["period_end"] for r in reps if r["period_end"]}   # periods the portal index skipped are taken from the DPS announcements
    for f in sorted((D.get(sym) or {}).get("filings") or [], key=lambda f: f["date"] or ""):
        if f["kind"] in ("annual_report", "quarterly_report", "half_year_report") and f["doc_id"] and ((f["date"] or "") > newest or (f["period_end"] and f["period_end"] not in portal_periods)) and f["doc_id"] not in seen_ids:
            seen_ids.add(f["doc_id"])
            jobs.append(dict(source="dps", key=f"dps_{f['doc_id']}", url=f"https://dps.psx.com.pk/download/document/{f['doc_id']}.pdf", kind=f["kind"], period_end=f["period_end"], date_public=f["date"], date_note="announcement_date", title=f["title"]))
    return jobs


def main(argv):
    syms = [s.strip().upper() for s in argv[0].split(",")]
    nA = int(argv[argv.index("--annual") + 1]) if "--annual" in argv else 2
    nQ = int(argv[argv.index("--quarterly") + 1]) if "--quarterly" in argv else 5
    F = json.load(open(STATE / "intel_filings_index.json"))["symbols"]
    D = json.load(open(STATE / "intel_dps_company.json"))["symbols"] if (STATE / "intel_dps_company.json").exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    ip = OUT / "index.json"
    idx = json.load(open(ip)) if ip.exists() else {}
    for f_ in sorted((STATE / "intel_raw" / "reports_index").glob("*.json")):
        for s_, d_ in json.load(open(f_)).items(): idx.setdefault(s_, {}).update(d_)
    only_ocr = os.environ.get("ONLY_OCR") == "1"; kinds = tuple(os.environ.get("OCR_KINDS", "quarterly").split(","))
    touched = {}
    for s in syms:
        for j in pick(s, nA, nQ, F, D):
            path = OUT / s / f"{j['key']}.txt.gz"
            if only_ocr:
                m_ = idx.get(s, {}).get(j["key"], {})
                if not (m_.get("ocr") and m_.get("status") == "ok" and not m_.get("ocr_done") and j["kind"].startswith(kinds)): continue
            elif os.environ.get("REFETCH_EMPTY") == "1":
                try: _ex = json.load(open(STATE / "intel_extract" / f"{s}.json")).get(j["key"])
                except Exception: _ex = None
                if _ex is None or _ex.get("statements"): continue
            elif path.exists() and not (os.environ.get("REOCR") == "1" and idx.get(s, {}).get(j["key"], {}).get("ocr")):
                continue
            try:
                r = dl(j["url"])
                if r.status_code != 200 or r.content[:4] != b"%PDF":
                    print(s, j["key"], "not a PDF", r.status_code); idx.setdefault(s, {})[j["key"]] = {**j, "status": f"http_{r.status_code}", "retrieved": now()}; continue
                txt, pages, ocr, spreads = to_text(r.content)
                (OUT / s).mkdir(exist_ok=True)
                with gzip.open(path, "wb", 9) as g:
                    g.write(txt.encode())
                idx.setdefault(s, {})[j["key"]] = {**j, "status": "ok", "retrieved": now(), "sha256": hashlib.sha256(r.content).hexdigest(), "bytes": len(r.content), "pages": pages, "spread_pages": spreads, "chars": len(txt), "ocr": ocr, "ocr_done": bool(ocr and os.environ.get("NO_OCR") != "1")}
                touched.setdefault(s, {})[j["key"]] = idx[s][j["key"]]
                print(s, j["key"], j["kind"], j["period_end"], "pages", pages, "spreads", spreads, "chars", len(txt), "OCR" if ocr else "", flush=True)
            except Exception as e:  # noqa
                print(s, j["key"], "ERROR", repr(e)[:150]); idx.setdefault(s, {})[j["key"]] = {**j, "status": "error", "error": repr(e)[:200], "retrieved": now()}
            json.dump(idx, open(ip, "w"), indent=1)
    json.dump(idx, open(ip, "w"), indent=1)
    if only_ocr and touched:
        (STATE / "intel_raw" / "reports_index").mkdir(parents=True, exist_ok=True)
        json.dump(touched, open(STATE / "intel_raw" / "reports_index" / f"ocr_{os.environ.get('SHARD', '0')}.json", "w"), separators=(",", ":"))


if __name__ == "__main__":
    main(sys.argv[1:])
