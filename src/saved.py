"""번역·뜻 풀이 결과 저장 형식: 사용자 브라우저 저장(A)과 결과 파일 내려받기·불러오기(B)가 함께 쓴다.

- 서버에는 아무것도 저장하지 않는다. 글(절)의 해시 → 그 글의 번역 결과와 단어 뜻 풀이만 담는다.
  같은 PDF를 다시 넣으면 절마다 같은 해시가 나와, 저장된 결과로 바로 열린다(번역 API를 다시 부르지 않음).
- 원문(영어 본문)과 PDF 파일은 담지 않는다. 한국어 번역문은 담긴다(브라우저·파일은 사용자 기기에만 있음).
- 형광펜·밑줄 위치는 화면에서 튜플로 다루므로 JSON에서 되돌릴 때 튜플로 바꾼다.
"""

import json
import time
from dataclasses import asdict, fields

from src.translate import TranslationResult

FORMAT = "paperpath-saved-1"
MAX_BYTES = 3_500_000          # 브라우저 저장 공간(보통 5MB)을 넘지 않도록 이 크기 안에서 오래된 것부터 뺀다
_FIELDS = {f.name for f in fields(TranslationResult)}


def dump_result(tr: TranslationResult) -> dict:
    d = asdict(tr)
    d.pop("cached", None)
    return d


def load_result(d: dict) -> TranslationResult:
    d = {k: v for k, v in d.items() if k in _FIELDS}
    d["ko_marks"] = [[tuple(x) for x in row] for row in d.get("ko_marks") or []]
    d["ko_underlines"] = [[tuple(x) for x in row] for row in d.get("ko_underlines") or []]
    d["protected"] = [list(x) for x in d.get("protected") or []]
    d["cached"] = True
    return TranslationResult(**d)


def pack_items(translations: dict, ai_gloss: dict, now: float | None = None) -> dict[str, dict]:
    """화면 상태 → {글 해시: {"t": {"1"|"0": 번역}, "g": 뜻 풀이, "ts": 저장 시각}}. 실패한 번역은 담지 않는다."""
    now = now or time.time()
    items: dict[str, dict] = {}
    for (tkey, masked), tr in translations.items():
        if tr is None or (tr.ko is None and tr.paragraph is None):
            continue
        items.setdefault(tkey, {"t": {}, "g": {}, "ts": now})["t"]["1" if masked else "0"] = dump_result(tr)
    for tkey, meanings in ai_gloss.items():
        if meanings:
            items.setdefault(tkey, {"t": {}, "g": {}, "ts": now})["g"] = dict(meanings)
    return items


def signature(item: dict) -> str:
    """바뀌었는지 볼 짧은 표시(어떤 번역이 있고 뜻 풀이가 몇 개인지)."""
    return f"{','.join(sorted(item.get('t', {})))}|{len(item.get('g', {}))}"


def unpack_items(items: dict) -> tuple[dict, dict]:
    """{글 해시: …} → (번역 {(해시, 마스킹): TranslationResult}, 뜻 풀이 {해시: {단어: 뜻}}). 깨진 항목은 건너뛴다."""
    translations, gloss = {}, {}
    for tkey, item in (items or {}).items():
        if not isinstance(item, dict):
            continue
        for m, d in (item.get("t") or {}).items():
            try:
                translations[(tkey, m == "1")] = load_result(d)
            except (TypeError, ValueError, AttributeError):
                continue
        g = item.get("g")
        if isinstance(g, dict) and g:
            gloss[tkey] = {str(k): str(v) for k, v in g.items()}
    return translations, gloss


def trim(items: dict, max_bytes: int = MAX_BYTES) -> dict:
    """크기 상한 안에 들도록 오래된 글부터 뺀다."""
    keep = dict(items)
    for tkey in sorted(keep, key=lambda k: keep[k].get("ts", 0)):
        if len(json.dumps(keep, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            break
        keep.pop(tkey)
    return keep


def to_file(items: dict, title: str = "", docs: dict | None = None) -> bytes:
    """결과 파일(B) 내용. 다른 PC에서 '결과 파일 불러오기'로 다시 넣는다. docs(다시 열 문서)도 함께 담는다."""
    return json.dumps({"format": FORMAT, "title": title, "saved_at": time.strftime("%Y-%m-%d %H:%M"),
                       "items": items, "docs": docs or {}}, ensure_ascii=False).encode("utf-8")


def from_file(data: bytes) -> dict:
    """결과 파일 → items(번역 결과). 형식이 다르면 ValueError. 문서는 docs_from_file."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("PaperPath 결과 파일이 아닙니다(JSON을 읽지 못함).") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or not isinstance(doc.get("items"), dict):
        raise ValueError("PaperPath 결과 파일이 아닙니다(형식이 다릅니다).")
    return doc["items"]


def docs_from_file(data: bytes) -> dict:
    """결과 파일 속 다시 열 문서(예전 파일에는 없을 수 있음). 깨진 문서는 뺀다."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    docs = doc.get("docs") if isinstance(doc, dict) else None
    return {k: v for k, v in (docs or {}).items() if valid_doc(v)} if isinstance(docs, dict) else {}


# ---------------------------------------------------------------- 다시 열 문서(사용자 브라우저에만)

MAX_DOCS = 30


def text_hash(text: str) -> str:
    """app.text_key와 같은 글 해시(번역 결과를 찾는 열쇠)."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def doc_record(kind: str, title: str, sections: list[dict], extra: dict | None = None,
               now: float | None = None) -> dict:
    """다시 열 문서: 종류(pdf·arxiv·paste), 제목, 절 목록 [{title, text, subheads}].
    PDF 파일과 표·그림 이미지는 담지 않는다(크고, 같은 PDF를 다시 넣으면 다시 만든다)."""
    body = "\x1f".join(s["text"] for s in sections)
    return {"id": text_hash(kind + "\x1f" + body), "kind": kind, "title": title[:200], "ts": now or time.time(),
            "sections": [{"title": s["title"], "text": s["text"], "subheads": list(s.get("subheads") or []),
                          "sub_at": [int(x) for x in (s.get("sub_at") or [])]}
                         for s in sections], **(extra or {})}


def valid_doc(rec) -> bool:
    return (isinstance(rec, dict) and isinstance(rec.get("sections"), list) and rec.get("kind") in ("pdf", "arxiv", "paste")
            and all(isinstance(s, dict) and isinstance(s.get("text"), str) for s in rec["sections"]))
