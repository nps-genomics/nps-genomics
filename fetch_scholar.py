#!/usr/bin/env python3
"""
Fetch Google Scholar profile metrics and write data/scholar.json.

Design constraints worth knowing before you touch this:

  * Google Scholar has no public API. This scrapes the profile page, which is
    against Scholar's Terms of Service and is rate-limited by CAPTCHA. Expect
    intermittent failures from GitHub-hosted runners, whose IP ranges Scholar
    blocks more aggressively than residential ones.

  * Therefore this script NEVER overwrites a good file with a bad one. If the
    scrape fails, or returns numbers that look wrong, it leaves the existing
    data/scholar.json untouched and exits 0. The website falls back to the last
    committed values. A stale number is fine; a zero on your homepage is not.

  * Citation counts are monotonically non-decreasing in practice. A large drop
    is almost always a parse failure, not a real event, so we reject it.

Optional: set SERPAPI_KEY as a repo secret to use SerpAPI's Google Scholar
Author endpoint instead of scraping. It is paid past a small free tier but does
not get blocked. If the key is present it is tried first.
"""

import json
import os
import sys
import datetime
import pathlib
import urllib.request
import urllib.parse

SCHOLAR_ID = os.environ.get("GOOGLE_SCHOLAR_ID", "anLGTFwAAAAJ")
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "scholar.json"

# A drop larger than this fraction of the previous total is treated as a
# parse failure rather than a real change.
MAX_ALLOWED_DROP = 0.10


def load_previous():
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return None


def via_serpapi(key):
    url = "https://serpapi.com/search?" + urllib.parse.urlencode(
        {
            "engine": "google_scholar_author",
            "author_id": SCHOLAR_ID,
            "api_key": key,
            "num": 100,
        }
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        raw = json.load(r)

    tbl = {row["citations"]["all"]: row for row in [] }  # placeholder, unused
    cited = raw.get("cited_by", {}).get("table", [])

    def pick(name):
        for row in cited:
            if name in row:
                return row[name].get("all")
        return None

    pubs = [
        {
            "title": a.get("title", ""),
            "year": a.get("year", ""),
            "citations": int(a.get("cited_by", {}).get("value") or 0),
            "link": a.get("link", ""),
        }
        for a in raw.get("articles", [])
    ]

    return {
        "name": raw.get("author", {}).get("name", ""),
        "citations": pick("citations"),
        "h_index": pick("h_index"),
        "i10_index": pick("i10_index"),
        "publications": pubs,
        "source": "serpapi",
    }


def via_scholarly():
    from scholarly import scholarly

    author = scholarly.search_author_id(SCHOLAR_ID)
    author = scholarly.fill(
        author, sections=["basics", "indices", "counts", "publications"]
    )

    pubs = []
    for p in author.get("publications", []):
        bib = p.get("bib", {})
        title = (bib.get("title") or "").strip()
        if not title:
            continue
        pubs.append(
            {
                "title": title,
                "year": str(bib.get("pub_year") or ""),
                "citations": int(p.get("num_citations") or 0),
            }
        )

    return {
        "name": author.get("name", ""),
        "citations": author.get("citedby"),
        "h_index": author.get("hindex"),
        "i10_index": author.get("i10index"),
        "publications": pubs,
        "source": "scholarly",
    }


def sane(new, prev):
    """Reject obviously broken scrapes before they reach the website."""
    if not new.get("citations") or not new.get("publications"):
        print("::warning::empty result - refusing to write")
        return False
    if new["citations"] < 1 or new.get("h_index") is None:
        print("::warning::missing core metrics - refusing to write")
        return False
    if prev and prev.get("citations"):
        drop = (prev["citations"] - new["citations"]) / prev["citations"]
        if drop > MAX_ALLOWED_DROP:
            print(
                "::warning::citations fell %d -> %d (%.0f%%). "
                "Treating as a parse failure and keeping the old file."
                % (prev["citations"], new["citations"], drop * 100)
            )
            return False
    return True


def main():
    prev = load_previous()
    data = None
    errors = []

    key = os.environ.get("SERPAPI_KEY", "").strip()
    if key:
        try:
            data = via_serpapi(key)
        except Exception as e:
            errors.append("serpapi: %s" % e)

    if data is None:
        try:
            data = via_scholarly()
        except Exception as e:
            errors.append("scholarly: %s" % e)

    if data is None or not sane(data, prev):
        for e in errors:
            print("::warning::" + e)
        print("Scholar sync did not produce usable data. Existing file left in place.")
        return 0  # deliberately not a failure - see module docstring

    data["publications"].sort(key=lambda p: -p["citations"])
    data["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )
    data["scholar_id"] = SCHOLAR_ID
    data["profile_url"] = (
        "https://scholar.google.com/citations?user=%s&hl=en" % SCHOLAR_ID
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        "Wrote %s: %s citations, h-index %s, i10 %s, %d publications (via %s)"
        % (
            OUT,
            data["citations"],
            data["h_index"],
            data["i10_index"],
            len(data["publications"]),
            data["source"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
