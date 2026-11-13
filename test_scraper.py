"""Run: python -m unittest test_scraper   (offline checks of the deterministic rules)"""
import gzip, json, random, tempfile, unittest
from pathlib import Path
from unittest import mock

import keywords
import scraper
from scraper import doc_type, find_copies, normalize, shingles


def hit(form, file_type):
    return {"form": form, "file_type": file_type}


class Rules(unittest.TestCase):
    def test_doc_types(self):
        cases = [
            (("8-K", "EX-10.1"), "Contract exhibit"),
            (("S-1", "EX-2.1"), "Merger/acquisition exhibit"),
            (("6-K", "EX-2"), "Other exhibit"),                 # foreign EX-2 = report section, not a merger
            (("10-K", "EX-21"), "Other exhibit"),               # subsidiaries list, NOT EX-2.x
            (("10-K", "EX-4.1"), "Other exhibit"),              # US EX-4 = securities instruments
            (("20-F", "EX-4.3"), "Contract exhibit"),           # 20-F EX-4 = material contracts
            (("NPORT-P", "NPORT-P"), "Fund/ownership filing"),
            (("8-K", "EX-99.1"), "Press release/other exhibit"),
            (("10-K", "10-K"), "Annual/quarterly report"),
            (("8-K/A", "8-K/A"), "Current report (8-K)"),
        ]
        for (form, ft), want in cases:
            self.assertEqual(doc_type(hit(form, ft)), want, (form, ft))

    def test_queries_unique_and_stable(self):
        q = keywords.build_queries()
        self.assertEqual(len({t.lower() for _, _, t in q}), len(q))
        self.assertEqual(q[0], ("MSA-0001", "MSA", '"master services agreement" "artificial intelligence"'))

    def test_every_broad_query_is_narrowed(self):
        """Catches a typo in NARROWED: a misspelled entry would silently leave that query broad."""
        qs = {qid: q for qid, _, q in keywords.build_queries()}
        narrowed = [q for q in qs.values() if q.endswith(keywords.CONTRACT_SIGNAL)]
        self.assertEqual(len(narrowed), 30)
        self.assertEqual(qs["MSA-0289"], '"services agreement" "artificial intelligence" "in witness whereof"')
        self.assertEqual(qs["PARTY-0029"], '"CoreWeave" "in witness whereof"')


def contract(seed, n=800):
    """Synthetic contract body: n random words (so different seeds share almost no 5-grams)."""
    rng = random.Random(seed)
    return " ".join(rng.choice(["party", "service", "fee", "term", "model", "gpu", "data", "license",
                                "shall", "the", "of", "provider", "customer", "notice", "law", "ai"])
                    for _ in range(n))


class Copies(unittest.TestCase):
    def test_normalize_ignores_formatting(self):
        a = normalize("<p>Master&nbsp;Services <b>AGREEMENT</b>,</p>\n<script>x()</script>Section 1.")
        self.assertEqual(a, "master services agreement section 1")

    def test_groups_refiled_copies_only(self):
        body = contract(1)
        texts = {
            "a:8k.htm": "ex 10 1 2 ex10 1 htm exhibit 10 1 " + body,         # original 8-K exhibit
            "b:10q.htm": "ex 10 25 3 ex10 25 htm exhibit 10 25 " + body,     # same contract, re-filed
            "c:amend.htm": body[: len(body) // 2] + " " + contract(2, 400),  # amendment: half rewritten
            "d:other.htm": contract(3),                                      # unrelated contract
            "e:short.htm": "ex 10 1",                                        # stub: too short to compare
        }
        def load(d):
            s = shingles(texts[d])
            return s if len(s) >= scraper.MIN_SHINGLES else None
        filed = {"a:8k.htm": "2024-01-02", "b:10q.htm": "2024-05-01", "c:amend.htm": "2025-01-01",
                 "d:other.htm": "2024-01-02", "e:short.htm": "2024-01-02"}
        compared, got = find_copies(texts, rank=lambda d: (filed[d], d), load=load)
        self.assertEqual(compared, 4)                                        # stub skipped
        self.assertEqual(set(got), {"b:10q.htm"})
        self.assertEqual(got["b:10q.htm"][0], "a:8k.htm")                    # earliest filing is kept
        self.assertGreaterEqual(got["b:10q.htm"][1], scraper.DUP_THRESHOLD)

    def test_build_end_to_end(self):
        """Offline build from a fake cache: copies collapse in the xlsx, stay traceable in the CSV."""
        def h(adsh, fn, form, ft, filed):
            return {"id": f"{adsh}:{fn}", "form": form, "file_type": ft, "title": "EX-10.1",
                    "filed": filed, "cik": "123", "company": "ACME AI INC  (ACME)"}
        hits = [h("0001-24-000001", "ex101.htm", "8-K", "EX-10.1", "2024-01-02"),
                h("0001-24-000002", "ex1025.htm", "10-Q", "EX-10.25", "2024-05-01"),
                h("0001-24-000003", "ex102.htm", "8-K", "EX-10.2", "2024-01-02"),
                h("0001-24-000004", "acme10k.htm", "10-K", "10-K", "2024-03-01")]
        body = {0: contract(1), 1: contract(1), 2: contract(3)}
        with tempfile.TemporaryDirectory() as tmp:
            cache, out = Path(tmp, "cache"), Path(tmp, "output")
            q = '"master services agreement" "artificial intelligence"'
            with mock.patch.multiple(scraper, CACHE=cache, OUT=out, TEXTS=cache / "text"), \
                    mock.patch.object(keywords, "build_queries", return_value=[("MSA-0001", "MSA", q)]):
                (cache / "text").mkdir(parents=True)
                with gzip.open(scraper.cache_path(q), "wt", encoding="utf-8") as f:
                    json.dump({"query": q, "reported": 4, "rows": 4, "skipped": False, "hits": hits}, f)
                for i, t in body.items():
                    with gzip.open(scraper.text_path(hits[i]["id"]), "wt", encoding="utf-8") as f:
                        f.write(f"exhibit {i} " + t)
                with mock.patch("builtins.print"):
                    scraper.cmd_build()
                info = json.loads((out / "run_info.json").read_text())
                self.assertEqual(info["p1_p2_duplicate_copies"], 1)
                self.assertEqual(info["by_priority_without_copies"]["1"], 2)
                import csv
                with open(out / "documents.csv", encoding="utf-8") as f:
                    rows = {r["doc_id"]: r for r in csv.DictReader(f)}
                self.assertEqual(rows[hits[0]["id"]]["copies"], "2")
                self.assertEqual(rows[hits[1]["id"]]["duplicate_of"], hits[0]["id"])
                from openpyxl import load_workbook
                wb = load_workbook(out / "ai_agreements.xlsx", read_only=True)
                self.assertEqual(wb.sheetnames[:3], ["Contracts (P1-P2)", "Other docs (P3-P4)", "Duplicate copies (P1-P2)"])
                contracts = list(wb["Contracts (P1-P2)"].values)
                self.assertEqual(len(contracts), 1 + 2)                    # header + original + other contract
                self.assertEqual(contracts[0][-1], "copies")
                dups = list(wb["Duplicate copies (P1-P2)"].values)
                self.assertEqual(len(dups), 2)
                self.assertTrue(dups[1][-1].endswith("/ex101.htm"))
                wb.close()


if __name__ == "__main__":
    unittest.main()
