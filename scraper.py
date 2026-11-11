"""EDGAR AI-agreement scraper.  Usage (run in order):

    set SEC_CONTACT=you@berkeley.edu        (PowerShell: $env:SEC_CONTACT="you@berkeley.edu")
    python scraper.py count     # 1 request per query -> output/queries.csv with hit totals
    python scraper.py collect   # fetch every result page -> cache/  (resumable: rerun after a crash)
    python scraper.py build     # cache -> output/ai_agreements.xlsx + CSVs (offline, no network)

Source: EDGAR Full-Text Search API, the backend of https://www.sec.gov/edgar/search/.
One search hit = one file inside a filing (main document or an exhibit).
"""
import csv, datetime as dt, gzip, hashlib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from collections import defaultdict
from pathlib import Path

import keywords

START, END = "2001-01-01", "2026-10-31"  # fixed window => same results on rerun (FTS starts 2001)
API = "https://efts.sec.gov/LATEST/search-index"
PAGE = 100                       # API returns 100 hits/page, max 10,000 per query
MIN_GAP = 0.15                   # seconds between requests (SEC limit is 10/s; we stay ~5/s)
HERE = Path(__file__).parent
CACHE, OUT = HERE / "cache", HERE / "output"
_last = 0.0


def get(params):
    """One API call, throttled, with retry + exponential backoff on server/network errors."""
    global _last
    contact = os.environ.get("SEC_CONTACT")
    if not contact:
        sys.exit("Set SEC_CONTACT to your email (SEC fair-access policy requires it in the User-Agent).")
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        time.sleep(max(0, MIN_GAP - (time.time() - _last)))
        _last = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"UC Berkeley AI licensing research {contact}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            if "hits" in data:
                return data
            err = str(data)[:200]                      # API reports some errors as JSON with HTTP 200
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            err = repr(e)
        wait = 2 ** (attempt + 1)
        print(f"  retry {attempt + 1} in {wait}s: {err}")
        time.sleep(wait)
    raise RuntimeError(f"failed after retries: {url}")


def search(q, start, end, frm=0):
    return get({"q": q, "dateRange": "custom", "startdt": start, "enddt": end, "from": frm})


def slim(hit):
    """Keep only the fields the spreadsheet needs."""
    s = hit["_source"]
    return {"id": hit["_id"], "form": s.get("form") or "", "file_type": s.get("file_type") or "",
            "title": (s.get("file_description") or "").strip(), "filed": s.get("file_date") or "",
            "cik": (s.get("ciks") or [""])[0], "company": (s.get("display_names") or [""])[0]}


def collect_query(q):
    """All hits for one query, page by page. Queries with >=10,000 hits are skipped as too broad
    (owner decision 2026-11-10: their top-100 hits were only 2-6% contracts)."""
    first = search(q, START, END)
    total = first["hits"]["total"]
    if total["relation"] == "gte":
        return {"query": q, "reported": total["value"], "skipped": True, "hits": []}
    hits, rows = {}, 0
    pages = [first] + [search(q, START, END, f) for f in range(PAGE, total["value"], PAGE)]
    for page in pages:
        for h in page["hits"]["hits"]:
            hits[h["_id"]] = slim(h)
            rows += 1
    return {"query": q, "reported": total["value"], "rows": rows, "skipped": False,
            "hits": sorted(hits.values(), key=lambda h: h["id"])}


def cache_path(q):
    return CACHE / (hashlib.sha1(q.encode()).hexdigest()[:12] + ".json.gz")


def cmd_count():
    OUT.mkdir(exist_ok=True)
    rows = []
    for qid, fam, q in keywords.build_queries():
        t = search(q, START, END)["hits"]["total"]
        rows.append([qid, fam, q, f"{t['value']}{'+' if t['relation'] == 'gte' else ''}"])
        print(qid, rows[-1][3], q)
    write_csv(OUT / "query_counts.csv", ["query_id", "family", "query", "hits"], rows)


def cmd_collect():
    CACHE.mkdir(exist_ok=True)
    queries = keywords.build_queries()
    for i, (qid, fam, q) in enumerate(queries, 1):
        p = cache_path(q)
        if p.exists():
            continue                                   # already collected in an earlier run
        res = collect_query(q)
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(res, f)
        tmp.replace(p)                                 # atomic: no half-written cache files
        print(f"[{i}/{len(queries)}] {qid} reported={res['reported']} got={len(res['hits'])} {q}")


# --- classification (deterministic rules, see README) ------------------------------------
# Fund holdings / ownership / fund-prospectus forms: mention AI companies as investments, not deals.
NOISE_EXACT = ("3", "4", "5", "144", "D", "FWP", "424B2")
NOISE_PREFIX = ("NPORT", "N-CSR", "N-Q", "N-MFP", "N-1A", "N-2", "13F", "SC 13", "SCHEDULE 13", "485", "497", "40-17")
FOREIGN = ("20-F", "40-F", "6-K", "F-1", "F-4")


def doc_type(h):
    ft, form = h["file_type"].upper(), h["form"].upper()
    base = form.split("/")[0]
    if ft.startswith("EX-10") or ft.startswith("EX-1A-6") or ft.startswith("EX1A-6") \
            or (ft.startswith("EX-4") and base in FOREIGN):
        return "Contract exhibit"
    if ft.startswith("EX-2") and (len(ft) == 4 or ft[4] in ".(") and base not in FOREIGN:
        return "Merger/acquisition exhibit"            # kept out of P1: post-2023 mergers carry boilerplate AI reps
    if base in NOISE_EXACT or base.startswith(NOISE_PREFIX):
        return "Fund/ownership filing"
    if ft.startswith("EX-99"):
        return "Press release/other exhibit"
    if ft.startswith("EX-"):
        return "Other exhibit"
    if base in ("10-K", "10-Q", "20-F", "40-F", "10-KT"):
        return "Annual/quarterly report"
    if base in ("8-K", "6-K"):
        return "Current report (8-K)"
    if base in ("S-1", "S-4", "F-1", "F-4", "424B4", "424B3", "S-3", "DEFM14A", "PREM14A"):
        return "Registration/merger filing"
    return "Other filing"


PRIORITY_OF = {"Contract exhibit": 2, "Merger/acquisition exhibit": 2, "Fund/ownership filing": 4}  # else 3
FAMILY_ORDER = ["MSA", "LICENSE", "DEAL", "PARTY", "STACK"]


def cmd_build():
    queries = keywords.build_queries()
    docs, matches, qlog, missing = {}, defaultdict(list), [], 0
    for qid, fam, q in queries:
        p = cache_path(q)
        if not p.exists():
            missing += 1
            qlog.append([qid, fam, q, "", "", "NOT COLLECTED"])
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            res = json.load(f)
        dupes, lost = res.get("rows", 0) - len(res["hits"]), res["reported"] - res.get("rows", 0)
        note = ("SKIPPED: >=10,000 hits (too broad)" if res["skipped"] else
                "; ".join(x for x in ("narrowed (broad version had >=10,000 hits)"
                                      if q.endswith(keywords.CONTRACT_SIGNAL) else "",
                                      f"{dupes} duplicate hit(s) from API" if dupes else "",
                                      f"{lost} hit(s) NOT RETURNED" if lost else "") if x))
        qlog.append([qid, fam, q, res["reported"], len(res["hits"]), note])
        for h in res["hits"]:
            docs[h["id"]] = h
            matches[h["id"]].append((FAMILY_ORDER.index(fam), qid, fam, q))
    rows, mrows = [], []
    for did, h in docs.items():
        ms = sorted(matches[did])                       # best match = most important family, lowest ID
        _, qid, fam, q = ms[0]
        kind = doc_type(h)
        prio = 1 if kind == "Contract exhibit" and any(m[2] == "MSA" for m in ms) else PRIORITY_OF.get(kind, 3)
        adsh, fname = did.split(":", 1)
        folder = f"https://www.sec.gov/Archives/edgar/data/{int(h['cik'] or 0)}/{adsh.replace('-', '')}/"
        # 2001-era hits named like "0012.txt" are index-only fragments (404 on the archive): link the filing index
        url = folder + (f"{adsh}-index.htm" if re.fullmatch(r"\d{4}\.txt", fname) else fname)
        form = h["form"] + (f" / {h['file_type']}" if h["file_type"] and h["file_type"] != h["form"] else "")
        company = h["company"].split("  (CIK")[0].split(" (CIK")[0].strip()
        rows.append([prio, kind, h["title"], company, form, h["filed"], url, fam, q, len(ms), did])
        mrows += [[did, m[1]] for m in ms]
    rows.sort(key=lambda r: r[10])                     # stable sorts: priority, then newest, then doc_id
    rows.sort(key=lambda r: r[5], reverse=True)
    rows.sort(key=lambda r: r[0])
    mrows.sort()
    head = ["priority", "doc_type", "title", "company", "form / exhibit", "filed", "url",
            "strategy", "query", "n_queries", "doc_id"]
    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "documents.csv", head, rows)
    write_csv(OUT / "queries.csv", ["query_id", "family", "query", "reported_hits", "collected", "note"], qlog)
    write_csv(OUT / "matches.csv", ["doc_id", "query_id"], mrows)
    write_xlsx(head, rows, qlog)
    info = {"window": [START, END], "queries": len(queries), "not_collected": missing, "documents": len(rows),
            "by_priority": {p: sum(r[0] == p for r in rows) for p in (1, 2, 3, 4)},
            "sha256_documents_csv": sha(OUT / "documents.csv"), "sha256_keywords_py": sha(HERE / "keywords.py"),
            "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (OUT / "run_info.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))


def write_csv(path, head, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def write_xlsx(head, rows, qlog):
    """Streaming (write-only) workbook. Sheets: contracts first, everything else second, query log."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    wb = Workbook(write_only=True)
    link = Font(color="0563C1", underline="single")
    sheets = [("Contracts (P1-P2)", [r for r in rows if r[0] <= 2]), ("Other docs (P3-P4)", [r for r in rows if r[0] > 2])]
    for name, part in sheets:
        ws = wb.create_sheet(name)
        ws.freeze_panes = "A2"
        for col, w in zip("ABCDEFGHIJ", (8, 24, 40, 32, 18, 11, 50, 9, 50, 9)):
            ws.column_dimensions[col].width = w
        ws.auto_filter.ref = f"A1:J{len(part) + 1}"
        ws.append(head[:-1])                           # doc_id stays in CSV only
        for i, r in enumerate(part):
            vals = list(r[:-1])
            if i < 65_000:                             # Excel allows ~65k links per sheet
                c = WriteOnlyCell(ws, value=vals[6])
                c.hyperlink, c.font = vals[6], link
                vals[6] = c
            ws.append(vals)
    qs = wb.create_sheet("Queries")
    qs.freeze_panes = "A2"
    qs.append(["query_id", "family", "query", "reported_hits", "collected", "note"])
    for r in qlog:
        qs.append(r)
    wb.save(OUT / "ai_agreements.xlsx")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


if __name__ == "__main__":
    cmds = {"count": cmd_count, "collect": cmd_collect, "build": cmd_build}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
