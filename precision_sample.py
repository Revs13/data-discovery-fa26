"""Precision check: draw a seeded random sample of rows, fetch each document, and write
output/precision_sample.csv with its title line and the passage around the first AI term, for a
human to mark relevant yes/no.   Usage: python precision_sample.py [priority=1] [n=40] [seed=42]"""
import csv, html, random, re, sys, time, urllib.request
import os

from scraper import OUT

AI = re.compile(r"artificial intelligence|machine learning|large language model|generative ai|\bgpus?\b|"
                r"nvidia|neural network|deep learning|foundation model|\bai\b|\bllms?\b|inference|"
                r"high performance computing|computer vision|natural language", re.I)


def text_of(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"UC Berkeley AI licensing research {os.environ['SEC_CONTACT']}"})
    for attempt in range(4):
        try:
            raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
            break
        except Exception as e:                         # 503 = SEC busy: back off and retry
            if attempt == 3:
                return f"FETCH ERROR: {e}"
            time.sleep(2 ** (attempt + 1))
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def main(priority="1", n="40", seed="42"):
    rows = [r for r in csv.DictReader(open(OUT / "documents.csv", encoding="utf-8")) if r["priority"] == priority]
    sample = random.Random(int(seed)).sample(rows, min(int(n), len(rows)))
    out = []
    for r in sample:
        t = text_of(r["url"])
        time.sleep(0.2)
        m = re.search(r"[A-Z][A-Z0-9 ,.&'\-]{8,120}AGREEMENT[A-Z ,.&'\-]{0,60}", t[:8000])
        a = AI.search(t)
        out.append([r["doc_id"], r["company"], r["filed"], r["form / exhibit"], r["query"], r["url"],
                    m.group(0).strip() if m else t[:120], t[max(0, a.start() - 200): a.end() + 200] if a else "", ""])
    with open(OUT / f"precision_sample_p{priority}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "company", "filed", "form / exhibit", "query", "url", "title_line", "ai_passage", "relevant (Y/N/?)"])
        w.writerows(out)
    print(f"wrote {len(out)} rows -> output/precision_sample_p{priority}.csv")


if __name__ == "__main__":
    main(*sys.argv[1:])
