# EDGAR AI-Agreement Scraper (Phase 1: discovery)

Finds publicly filed documents in **SEC EDGAR** that may contain AI-related commercial agreements.
The focus is **Master Services Agreements (MSAs) and similar contracts**. Licenses, M&A deals, 10-Ks and press releases are also kept at lower priority.
The output is a **candidate list for human review**. A row is not a confirmed AI agreement.

## Quick start

```powershell
python -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt
$env:SEC_CONTACT = "you@berkeley.edu"     # SEC requires a contact email in the User-Agent
.\.venv\Scripts\python scraper.py count   # optional: hits per query, 1 request each (~40 min)
.\.venv\Scripts\python scraper.py collect # fetch all results into cache/ (~3 h; rerun to resume)
.\.venv\Scripts\python scraper.py build   # cache/ -> output/ (offline, ~1 min)
.\.venv\Scripts\python scraper.py texts   # download the ~4,000 P1-P2 texts -> cache/text/ (~15-20 min; rerun to resume)
.\.venv\Scripts\python scraper.py build   # rebuild: now also groups re-filed copies (adds ~1-2 min)
.\.venv\Scripts\python -m unittest test_scraper
```

## How it works

```
keywords.py --(2,166 queries)--> EDGAR Full-Text Search API --> cache/ (1 gz file per query)
                                                                    |
                         output/ai_agreements.xlsx + CSVs <-- build (dedupe, classify, sort)
```

1. **Queries.** [keywords.py](keywords.py) holds term lists. Most queries pair a **contract term** with an **AI term**, e.g. `"master services agreement" "GPU capacity"`. Both phrases must appear in the same document.
2. **Search.** `scraper.py` calls `efts.sec.gov/LATEST/search-index`, the API behind sec.gov's full-text search. It fetches every result page (100 hits per page), throttled to about 5 requests/s, with retry and backoff.
3. **Too-broad queries get narrowed.** The API stops at 10,000 hits, so a bigger query gives incomplete results. In the first run, 30 queries hit that cap, for example `"services agreement" "artificial intelligence"`. Each now carries a third phrase, `"in witness whereof"`, the line just above the signature block of almost every signed contract. That keeps signed agreements and drops most 10-Ks and fund reports. All 30 now fit (110–5,200 hits each). Any query still over the cap would be skipped and logged.
4. **Build.** Hits from all queries are deduplicated into one row per document. Each row gets a priority from fixed rules, and the output is sorted. This step makes no network calls.
5. **Re-filed copies (optional, P1–P2 only).** The same contract is often filed several times: with an 8-K, again with the next 10-Q, again in an S-1 or DEFA14A. Each copy has its own accession number, so step 4 keeps them as separate rows. `texts` downloads each P1–P2 document; `build` then compares their text (lowercased words, HTML stripped, 5-word shingles). Two documents with **Jaccard similarity ≥ 0.95** are treated as the same contract. Copies score ~0.99+, since only cover lines like "EX-10.25" differ. Amendments and amended-and-restated agreements usually fall below the threshold and stay separate. The kept copy is the highest-priority one, then the **earliest filing** (usually the original 8-K). Metadata alone can't do this: grouping by company + doc type + matched queries also lumped together different exhibits from the same filing (e.g. 10 unrelated contracts in one 10-KSB).

**Unit of data:** one file inside a filing, such as the main 10-K or one exhibit like `EX-10.1`. `doc_id` is `accession:filename`.

## Search strategy (5 families)

| Family | Built as | # queries | Example |
|---|---|---|---|
| **MSA** | 23 MSA synonyms × 48 AI terms + 14 phrases | 1,118 | `"statement of work" "large language model"`, `"GPU as a service"` |
| **LICENSE** | 17 license/distribution terms × 28 AI terms + 18 phrases | 494 | `"data license agreement" "machine learning"`, `"model license"` |
| **DEAL** | 19 M&A/partnership terms × 11 AI terms | 209 | `"joint development agreement" "foundation model"` |
| **PARTY** | AI company legal and brand names | 65 | `"OpenAI OpCo"`, `"Anthropic PBC"`, `"CoreWeave"`, `"Cerebras Systems"` |
| **STACK** | 35 mentor AI-stack terms × 8 contract types | 280 | `"high bandwidth memory" "supply agreement"` |

The terms come from the mentor's list plus extensions: MSA synonyms (SOW, order form, cloud/colocation/hosting agreements), GPU and neocloud vocabulary, and the legal entity names used in contract preambles.
**Ambiguous terms were removed using evidence.** Each was the sole match for many noisy rows. Examples: "Blackwell" (a surname), "TPU" (thermoplastic polyurethane), "ASIC" (the Australian securities regulator), "training data" (airline pilot training). The full list with counts is at the top of `keywords.py`. The 30 narrowed queries are listed there as `NARROWED`.

## Output (`output/`)

| File | Content |
|---|---|
| `ai_agreements.xlsx` | Sheets: **Contracts (P1-P2)** (one row per contract), **Other docs (P3-P4)**, **Duplicate copies (P1-P2)** (re-filed copies left out of the first sheet, each with a link to the kept copy), **Queries** (search log) |
| `documents.csv` * | Every row, copies included, plus `duplicate_of`, `similarity` and `doc_id` |
| `queries.csv` | Every query: exact text, family, hits reported/collected, notes (narrowed, API duplicates) |
| `matches.csv` * | Every document ↔ query pair (full traceability) |
| `query_counts.csv` * | Step-1 count pass (run 1 query list) |
| `precision_sample_p1.csv` | 40 random P1 rows with provisional Y/P/N judgments |
| `run_info.json` | Window, counts, SHA-256 of `documents.csv` and `keywords.py` |

\* Not in git (30–36 MB each). `collect` + `build` regenerate them.

**Columns:** `priority`, `doc_type`, `title` (the SEC's exhibit description, often just "EX-10.1"), `company` (the filer), `form / exhibit`, `filed`, `url`, `strategy` + `query` (the best-matching query: most important family first, then lowest ID), `n_queries` (how many queries matched; higher means more AI and contract signal), `copies` (how many filings of this contract were found; 1 = no other copy, or texts not downloaded). CSV only: `duplicate_of` (the `doc_id` of the kept copy, blank if this row is kept) and `similarity` (Jaccard to the kept copy).

**Priority rules** (deterministic):

| P | Rule | Rows |
|---|---|---|
| **1** | Contract exhibit (`EX-10.x`, Reg A `EX1A-6`, foreign `20-F` `EX-4.x`) **found by an MSA query** | 896 |
| **2** | Other contract exhibits (2,048), plus merger/acquisition exhibits (`EX-2.x`, 1,018) | 3,066 |
| **3** | Descriptive filings: 10-K/10-Q, 8-K body, S-1/S-4, press releases (`EX-99`) | 97,185 |
| **4** | Fund holdings, ownership and fund-prospectus forms (NPORT-P, 13F, 485BPOS…): almost always noise | 38,645 |

Merger exhibits are kept out of P1 because, since about 2023, nearly every merger agreement contains boilerplate "AI representations". In the sample, 17 of 20 were false positives.

## Results (run of 2026-11-10, window 2001-01-01 → 2026-10-31)

- 2,166 queries: all collected completely (30 of them narrowed, none skipped, no hits lost), 369 with zero hits. **139,792 unique documents.**
- **P1 precision (random sample of 40, seed 2026, provisional, taken before narrowing):** 22% clearly AI agreements, 40% clear or plausible. Among **2015+** rows it is **55%**. All 12 pre-2015 sampled rows were false positives. **Review P1 with `filed ≥ 2015` first** (749 of 896 rows).
- **Narrowing added 3,711 documents**: 70 new P1 and 977 new P2. Their quality is lower. Of 20 sampled new P1 rows, 2 were clear AI agreements (a SaaS AI license, an AI decision-software license), 4 plausible and 14 false. Most false ones were stock-purchase deals whose opening recitals describe the company as "AI-powered". That is expected: the broad pairs were generic to begin with.
- **Sampled P1 finds:** Anthropic S-1 Master Equipment Lease (Broadcom ↔ AI Infrastructure OpCo II), Boost Run GPU compute service agreements, the RiskOn–MeetKai generative-AI platform SOW, the Innoviz–BMW computer-vision SOW, and a WhiteFiber–Nscale MSA (P2).
- **Recall check:** known AI infrastructure filers appear in P1/P2: CoreWeave, Nscale, Cerebras, IREN, Nebius, Applied Digital, Anthropic. OpenAI, Microsoft, Lambda and Crusoe never appear *as filers*, which is expected: they are private, or they don't file these contracts. They appear as counterparties in other filers' documents. Use the PARTY queries to find them.

**Main false-positive pattern:** two phrases anywhere in one long document. One example is an MSA whose only AI mention is a clause saying "notify us if you use AI tools". Co-occurrence is a lead, not evidence.

## Reproducibility

- Fixed date window, fixed query list, deterministic dedupe/classify/sort. `build` from the same cache produces a byte-identical `documents.csv` (verified by hash).
- In a live re-fetch of 15 random queries hours later, **15/15 returned identical results.**
- `run_info.json` also records `p1_p2_texts_compared`, `p1_p2_duplicate_copies` and `by_priority_without_copies`. Grouping is deterministic given the same `cache/text/`.
- To compare a rerun, check `sha256_documents_csv` and `sha256_keywords_py` in `run_info.json`. The `.xlsx` bytes differ between runs (embedded timestamp), so compare the CSV.
- **Known limitation:** for queries with more than 100 hits, the API can shuffle equal-score documents across pages. This run saw 223 duplicate hits out of 625,991 (0.036%) in 90 queries, meaning up to 223 hits may be missing. Most of those documents are also caught by other queries. Reruns may differ at this margin.
- Results also depend on SEC's search index. Late-indexed filings inside the window could change counts slightly.

## Limitations and next steps

1. **No text check yet.** Relevance comes from search hits plus exhibit type. Phase 2: download the P1/P2 texts (about 2,900) and score how densely AI terms appear near contract language. That would raise precision a lot.
2. **EDGAR's blind spots.** Private AI companies don't file. Routine contracts don't have to be filed. Pricing is redacted (`[***]`).
3. **Titles are thin.** The search index often gives only "EX-10.1"; the real title is inside the document (another reason for phase 2).
4. **Duplicate rows.** Re-filed copies of a P1–P2 contract are grouped by text (see step 5). P3–P4 rows are not grouped. A contract filed by **both parties** is grouped too, since the text is the same. Groups chain: if A≈B and B≈C, all three are one group even if A and C alone score a bit lower; `similarity` shows each copy's score against the kept one, so borderline cases can be checked.
5. **Precision is measured on P1 only** (40 + 20 rows, one reviewer). It should be repeated by a human, on P2 too.

## Glossary

- **EDGAR**: the SEC's public filing database. **Filing**: one submission (e.g. an 8-K). **Exhibit**: an attached document.
- **8-K**: current report on a material event. Item 1.01 = "entry into a material agreement", often with the contract attached. **10-K / 10-Q**: annual / quarterly report. **S-1**: IPO registration (lists material contracts). **20-F / 6-K**: the foreign-company versions of the 10-K / 8-K.
- **EX-10.x**: material contract. **EX-2.x**: merger/acquisition plan. **EX-99.x**: press releases and other material. **EX-4.x**: securities instruments (in a 20-F: material contracts).
- **Precision**: relevant ÷ reviewed (sample-based). **Recall**: the share of all relevant documents that were found. It is unknowable exactly, so it is checked against known deals.
