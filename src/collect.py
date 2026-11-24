"""arXiv 공식 API로 초록을 불러온다.

- 메타데이터(제목·초록)는 CC0 1.0이다. 저자 필드는 저장하지 않는다.
- API 이용약관에 따라 요청 사이에 3초 이상 둔다(프로세스 전체 기준).
- 한 번 불러온 초록은 data/processed/arxiv_cache.json에 저장해 재요청과 시연 중 장애를 줄인다.
"""

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import certifi

from src import patterns as P

API = "https://export.arxiv.org/api/query"
MIN_INTERVAL = 3.0
ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "processed" / "arxiv_cache.json"
SAMPLE_PATH = ROOT / "data" / "processed" / "sample_abstracts.jsonl"
NS = {"a": "http://www.w3.org/2005/Atom"}
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
HEADERS = {"User-Agent": "eng-data-regex-project (course project)"}

_lock = threading.Lock()
_last_call = 0.0


class ArxivError(Exception):
    """화면에 그대로 보여 줄 수 있는 한국어 메시지를 담는다."""


def normalize_id(raw: str) -> str:
    m = P.ARXIV_ID.match(raw or "")
    if not m:
        raise ArxivError("arXiv ID 형식이 아닙니다. 예: 2402.08910 또는 arXiv:2402.08910v2")
    return m.group("id")


def _get(params: dict) -> bytes:
    """3초 간격을 지켜 API를 호출한다."""
    global _last_call
    with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                return resp.read()
        finally:
            _last_call = time.monotonic()


def _parse_entries(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    rows = []
    for entry in root.findall("a:entry", NS):
        summary = entry.findtext("a:summary", namespaces=NS)
        if not summary:
            continue
        rows.append({
            "id": entry.findtext("a:id", namespaces=NS).rsplit("/abs/", 1)[-1],
            "year": int((entry.findtext("a:published", namespaces=NS) or "0000")[:4]),
            "title": " ".join(entry.findtext("a:title", namespaces=NS).split()),
            "abstract": summary.strip(),
        })
    return rows


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def fetch_abstract(raw_id: str, use_cache: bool = True) -> dict:
    """arXiv ID로 {id, year, title, abstract}를 돌려준다. 실패하면 ArxivError."""
    arxiv_id = normalize_id(raw_id)
    cache = _load_cache()
    if use_cache and arxiv_id in cache:
        return {**cache[arxiv_id], "cached": True}
    try:
        rows = _parse_entries(_get({"id_list": arxiv_id, "max_results": 1}))
    except urllib.error.HTTPError as e:
        raise ArxivError(f"arXiv 서버가 요청을 거절했습니다(HTTP {e.code}). 잠시 뒤 다시 시도하십시오.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ArxivError("arXiv에 연결하지 못했습니다. 인터넷 연결을 확인하거나 텍스트 붙여 넣기를 쓰십시오.") from e
    except ET.ParseError as e:
        raise ArxivError("arXiv 응답을 읽지 못했습니다.") from e
    if not rows:
        raise ArxivError(f"arXiv에서 {arxiv_id} 논문을 찾지 못했습니다.")
    row = rows[0]
    cache[arxiv_id] = row
    _save_cache(cache)
    return {**row, "cached": False}


def fetch_samples(category: str = "cs.CL", years=(2012, 2015, 2018, 2021, 2024, 2025), per_year: int = 50) -> list[dict]:
    """연도별 최신 제출 초록 샘플(분석·평가용). 3초 간격이라 연도 6개면 약 20초 걸린다."""
    rows = []
    for year in years:
        query = f"cat:{category} AND submittedDate:[{year}01010000 TO {year}12312359]"
        got = _parse_entries(_get({"search_query": query, "start": 0, "max_results": per_year,
                                   "sortBy": "submittedDate", "sortOrder": "descending"}))
        for r in got:
            r["year"] = year
        rows.extend(got)
    return rows


def load_samples() -> list[dict]:
    with SAMPLE_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


if __name__ == "__main__":
    # 재현: .venv/Scripts/python.exe -m src.collect  → data/processed/sample_abstracts.jsonl
    data = fetch_samples()
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLE_PATH.open("w", encoding="utf-8", newline="\n") as f:
        for r in data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(data)}건 저장: {SAMPLE_PATH}")
