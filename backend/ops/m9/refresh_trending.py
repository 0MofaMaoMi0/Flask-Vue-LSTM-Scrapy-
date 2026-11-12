"""Refresh TMDB daily/weekly trending snapshots into a controlled file cache.

No database writes. Key is read from a local file and never printed.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

KEY_FILE = Path(os.environ.get("MI2_TMDB_KEY_FILE", "/tmp/tmdb_key.txt"))
CACHE = Path(os.environ.get("MI2_TRENDING_CACHE", "/var/lib/movieinsight-m6-realcopy/appdata/trending"))
BASE = "https://api.tmdb.org/3/trending/movie/{}?api_key={}&language=zh-CN"


def main():
    text = KEY_FILE.read_text(encoding="utf-8", errors="ignore")
    key = next((l.strip() for l in text.splitlines()
                if re.fullmatch(r"[A-Za-z0-9]{32}", l.strip())), None)
    if not key:
        raise SystemExit("tmdb key missing")
    CACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    for window in ("day", "week"):
        with urllib.request.urlopen(BASE.format(window, key), timeout=25) as r:
            data = json.load(r)
        results = []
        for rank, item in enumerate(data.get("results", []), 1):
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            results.append({"id": item["id"], "rank": rank, "title": item.get("title"),
                            "release_date": item.get("release_date")})
        doc = {"window": window, "fetched_at": datetime.now(timezone.utc).isoformat(),
               "source": "TMDB_TRENDING", "results": results}
        tmp = CACHE / f"trending_{window}.json.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE / f"trending_{window}.json")
        out[window] = len(results)
    print(json.dumps({"cache": str(CACHE), "counts": out}))


if __name__ == "__main__":
    main()
