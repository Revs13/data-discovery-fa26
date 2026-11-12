"""EDGAR AI-agreement scraper.  Usage (run in order):

    set SEC_CONTACT=you@berkeley.edu        (PowerShell: $env:SEC_CONTACT="you@berkeley.edu")
    python scraper.py count     # 1 request per query -> output/queries.csv with hit totals
    python scraper.py collect   # fetch every result page -> cache/  (resumable: rerun after a crash)
    python scraper.py build     # cache -> output/ai_agreements.xlsx + CSVs (offline, no network)
    python scraper.py texts     # download P1-P2 document texts -> cache/text/  (resumable)
    python scraper.py build     # again: now also groups re-filed copies of the same contract

Source: EDGAR Full-Text Search API, the backend of https://www.sec.gov/edgar/search/.
One search hit = one file inside a filing (main document or an exhibit).
"""
import csv, datetime as dt, gzip, hashlib, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request, zlib
from collections import defaultdict
from pathlib import Path

import keywords

START, END = "2001-01-01", "2026-10-31"  # fixed window => same results on rerun (FTS starts 2001)
API = "https://efts.sec.gov/LATEST/search-index"
PAGE = 100                       # API returns 100 hits/page, max 10,000 per query
MIN_GAP = 0.15                   # seconds between requests (SEC limit is 10/s; we stay ~5/s)
HERE = Path(__file__).parent
CACHE, OUT = HERE / "cache", HERE / "output"
TEXTS = CACHE / "text"
_last = 0.0


def user_agent():
    contact = os.environ.get("SEC_CONTACT")
    if not contact:
        sys.exit("Set SEC_CONTACT to your email (SEC fair-access policy requires it in the User-Agent).")
    return {"User-Agent": f"UC Berkeley AI licensing research {contact}"}


def throttle():
    """Shared by every SEC request (API and archive), so the whole tool stays ~5 requests/s."""
    global _last
    time.sleep(max(0, MIN_GAP - (time.time() - _last)))
    _last = time.time()


def get(params):
    """One API call, throttled, with retry + exponential backoff on server/network errors."""
    headers = user_agent()
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        throttle()
        try:
            req = urllib.request.Request(url, headers=headers)
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


# --- duplicate copies (same contract filed more than once) ---------------------------------
# The same agreement is often filed several times: with an 8-K, again with the 10-Q, again in an
# S-1 or a DEFA14A. Each copy has its own accession number, so it is a separate search hit. Metadata
# can't tell copies apart from different contracts (many titles are just "EX-10.1"), so we compare
# the documents' text: word 5-gram shingles, Jaccard similarity >= DUP_THRESHOLD = same contract.
SHINGLE = 5
DUP_THRESHOLD = 0.95             # copies score ~0.99+ (only cover-page/header lines differ)
SKETCH = 64                      # bottom-k sketch size, used only to find candidate pairs quickly
MIN_SHINGLES = 50                # shorter texts (fetch stubs, index fragments) are never grouped


def text_path(doc_id):
    return TEXTS / (hashlib.sha1(doc_id.encode()).hexdigest()[:16] + ".txt.gz")


def normalize(raw):
    """HTML/SGML -> lowercase words. Formatting, entities and punctuation differences between copies vanish."""
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return " ".join(re.findall(r"[a-z0-9]+", html.unescape(re.sub(r"<[^>]+>", " ", raw)).lower()))


def shingles(text):
    w = text.split()
    return {zlib.crc32(" ".join(w[i:i + SHINGLE]).encode()) for i in range(len(w) - SHINGLE + 1)}


def fetch_doc(url):
    """Raw document text, or None if it doesn't exist (404). Raises after repeated other failures."""
    headers = user_agent()
    for attempt in range(5):
        throttle()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = repr(e)                              # 403/429/503 = SEC throttling: back off
        except (urllib.error.URLError, TimeoutError) as e:
            err = repr(e)
        wait = 2 ** (attempt + 1)
        print(f"  retry {attempt + 1} in {wait}s: {err}")
        time.sleep(wait)
    raise RuntimeError(f"failed after retries: {url}")


def cmd_texts():
    """Download the text of every P1-P2 document listed in output/documents.csv (run `build` first)."""
    docs = OUT / "documents.csv"
    if not docs.exists():
        sys.exit("Run `python scraper.py build` first: texts reads the P1-P2 list from output/documents.csv.")
    with open(docs, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["priority"] in ("1", "2")]
    TEXTS.mkdir(parents=True, exist_ok=True)
    todo = [r for r in rows if not text_path(r["doc_id"]).exists() and not r["url"].endswith("-index.htm")]
    print(f"{len(rows)} P1-P2 documents, {len(todo)} to download (~{len(todo) * MIN_GAP / 60:.0f}+ min)")
    for i, r in enumerate(todo, 1):
        raw = fetch_doc(r["url"])
        p = text_path(r["doc_id"])
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(normalize(raw) if raw else "")   # empty file = 404, so reruns don't retry it
        tmp.replace(p)
        if i % 100 == 0 or i == len(todo):
            print(f"[{i}/{len(todo)}] {r['doc_id']}")


def load_shingles(doc_id):
    """Shingle set of a downloaded document, or None if not downloaded / too short to compare."""
    p = text_path(doc_id)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        s = shingles(f.read())
    return s if len(s) >= MIN_SHINGLES else None


def find_copies(doc_ids, rank, load=load_shingles):
    """Group documents whose texts are near-identical (Jaccard >= DUP_THRESHOLD).
    rank: doc_id -> sort key; the lowest-ranked copy of each group is the one kept.
    load: doc_id -> shingle set or None. Only small sketches stay in memory; full sets are
    reloaded for candidate pairs, so ~4,000 long contracts fit in a laptop's RAM.
    Returns (number of documents compared, {copy doc_id: (kept doc_id, similarity)})."""
    sketch = {}
    for d in sorted(doc_ids):
        s = load(d)
        if s is not None:
            sketch[d] = sorted(s)[:SKETCH]
    # Candidate pairs: documents sharing at least half of their bottom-k sketch values. Near-identical
    # texts share almost all of them; this avoids comparing all ~8M pairs in full.
    post = defaultdict(list)
    for d, sk in sketch.items():
        for v in sk:
            post[v].append(d)
    shared = defaultdict(int)
    for docs in post.values():
        if len(docs) > 500:                            # boilerplate shingle in hundreds of contracts: no signal
            continue
        for i, a in enumerate(docs):
            for b in docs[i + 1:]:
                shared[a, b] += 1
    cands = sorted(k for k, n in shared.items() if n * 2 >= min(len(sketch[k[0]]), len(sketch[k[1]])))
    full, sim = {}, {}

    def get(d):                                        # small cache: pairs are sorted, so reuse is local
        if d not in full:
            if len(full) > 200:
                full.clear()
            full[d] = load(d)
        return full[d]

    parent = {d: d for d in sketch}

    def root(d):
        while parent[d] != d:
            parent[d] = parent[parent[d]]
            d = parent[d]
        return d

    for a, b in cands:
        A, B = get(a), get(b)
        j = len(A & B) / len(A | B)
        if j >= DUP_THRESHOLD:
            sim[a, b] = sim[b, a] = j
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[max(ra, rb, key=rank)] = min(ra, rb, key=rank)
    copies = {}
    for d in sketch:
        keep = root(d)
        if keep != d:
            if (d, keep) not in sim:                   # joined through a third copy: measure directly
                A, B = get(d), get(keep)
                sim[d, keep] = len(A & B) / len(A | B)
            copies[d] = (keep, round(sim[d, keep], 3))
    return len(sketch), copies


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
    # Re-filed copies (P1-P2 only, and only once `texts` has downloaded them). Kept copy = highest
    # priority, then the earliest filing (usually the original 8-K), then doc_id.
    by_id = {r[10]: r for r in rows}
    p12 = [d for d, r in by_id.items() if r[0] <= 2]
    compared, copies = find_copies(p12, rank=lambda d: (by_id[d][0], by_id[d][5], d)) if TEXTS.exists() else (0, {})
    n_copies = defaultdict(lambda: 1)
    for keep, _ in copies.values():
        n_copies[keep] += 1
    for r in rows:
        keep, sim = copies.get(r[10], ("", ""))
        r[10:10] = [n_copies[r[10]] if not keep else "", keep, sim]   # doc_id stays last
    rows.sort(key=lambda r: r[-1])                     # stable sorts: priority, then newest, then doc_id
    rows.sort(key=lambda r: r[5], reverse=True)
    rows.sort(key=lambda r: r[0])
    mrows.sort()
    head = ["priority", "doc_type", "title", "company", "form / exhibit", "filed", "url",
            "strategy", "query", "n_queries", "copies", "duplicate_of", "similarity", "doc_id"]
    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "documents.csv", head, rows)
    write_csv(OUT / "queries.csv", ["query_id", "family", "query", "reported_hits", "collected", "note"], qlog)
    write_csv(OUT / "matches.csv", ["doc_id", "query_id"], mrows)
    url_of = {r[-1]: r[6] for r in rows}
    write_xlsx(head, rows, qlog, url_of)
    info = {"window": [START, END], "queries": len(queries), "not_collected": missing, "documents": len(rows),
            "by_priority": {p: sum(r[0] == p for r in rows) for p in (1, 2, 3, 4)},
            "p1_p2_texts_compared": compared, "p1_p2_duplicate_copies": len(copies),
            "by_priority_without_copies": {p: sum(r[0] == p and not r[11] for r in rows) for p in (1, 2)},
            "sha256_documents_csv": sha(OUT / "documents.csv"), "sha256_keywords_py": sha(HERE / "keywords.py"),
            "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (OUT / "run_info.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))


def write_csv(path, head, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def write_xlsx(head, rows, qlog, url_of):
    """Streaming (write-only) workbook. Sheets: contracts (one row per contract), everything else,
    the re-filed copies left out of the contracts sheet, query log."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    wb = Workbook(write_only=True)
    link = Font(color="0563C1", underline="single")
    main = head[:11]                                   # ... n_queries, copies (doc_id stays in CSV only)
    dup = head[:10] + ["similarity", "copy of (url)"]
    sheets = [("Contracts (P1-P2)", main, [r[:11] for r in rows if r[0] <= 2 and not r[11]]),
              ("Other docs (P3-P4)", head[:10], [r[:10] for r in rows if r[0] > 2]),
              ("Duplicate copies (P1-P2)", dup, [r[:10] + [r[12], url_of[r[11]]] for r in rows if r[11]])]
    for name, cols, part in sheets:
        ws = wb.create_sheet(name)
        ws.freeze_panes = "A2"
        for col, w in zip("ABCDEFGHIJKL", (8, 24, 40, 32, 18, 11, 50, 9, 50, 9, 9, 50)):
            ws.column_dimensions[col].width = w
        ws.auto_filter.ref = f"A1:{'ABCDEFGHIJKL'[len(cols) - 1]}{len(part) + 1}"
        ws.append(cols)
        for i, vals in enumerate(part):
            if i < 65_000:                             # Excel allows ~65k links per sheet
                for j in [6] + ([11] if cols is dup else []):
                    c = WriteOnlyCell(ws, value=vals[j])
                    c.hyperlink, c.font = vals[j], link
                    vals[j] = c
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
    cmds = {"count": cmd_count, "collect": cmd_collect, "build": cmd_build, "texts": cmd_texts}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
