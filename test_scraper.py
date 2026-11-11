"""Run: python -m unittest test_scraper   (offline checks of the deterministic rules)"""
import unittest

import keywords
from scraper import doc_type


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


if __name__ == "__main__":
    unittest.main()
