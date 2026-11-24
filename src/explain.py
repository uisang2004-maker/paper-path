"""어려운 단어표의 'AI 풀이(참고용)' 칸: Claude Haiku 5.5가 단어마다 이 논문에서의 뜻을 한국어 한 줄로 풀어 준다.

- 어떤 단어를 넣을지는 정규식·빈도표(src/glossary.py)가 정한다. AI는 고른 단어의 뜻 풀이만 쓴다.
- 단어와 그 단어가 처음 나온 문장만 보낸다. 버튼을 눌렀을 때만 호출한다.
- 답은 JSON 스키마로 받아(output_config.format) 보낸 단어에 해당하는 풀이만 쓴다.
"""

import json

from src import translate as T

MAX_TERMS = 40              # 한 번에 풀이할 단어 수(처음 나온 순서로 앞에서부터)
MAX_MEANING = 80            # 화면에 넣을 풀이 길이 상한(글자). 넘으면 자르고 '…'
SENT_LIMIT = 300            # 함께 보내는 문장 길이 상한(글자)

SYSTEM = """You help Korean undergraduate students who are reading an English academic paper for the first time.
For each term, write what it means in this paper, in Korean, in one short phrase of at most 40 Korean characters
(never more than 60).
Use the given sentence as context. Keep technical terms in their usual Korean form and add the English only when it
helps. If the term is an abbreviation and the full form is given, use it. Do not invent facts about this specific paper
(numbers, results). If you cannot tell the meaning, return an empty string for that term."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["term", "meaning"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def build_request(items: list[tuple[str, str, str]]) -> str:
    """(단어, 처음 나온 문장, 원문에서 찾은 풀네임·설명) 목록 → 모델에 보낼 글."""
    lines = []
    for term, sentence, known in items[:MAX_TERMS]:
        line = f"- term: {term}\n  sentence: {sentence[:SENT_LIMIT]}"
        if known:
            line += f"\n  full form or definition found in the paper: {known}"
        lines.append(line)
    return "Terms:\n" + "\n".join(lines)


def explain_terms(items: list[tuple[str, str, str]], api_key: str, client=None) -> dict[str, str]:
    """{단어: 한국어 풀이}. 실패하면 translate.TranslationError를 올린다."""
    if not items:
        return {}
    text = T._claude_one(build_request(items), api_key, client, system=SYSTEM,
                         fmt={"type": "json_schema", "schema": SCHEMA}, task="단어 풀이")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise T.TranslationError("AI 풀이 답을 읽지 못했습니다. 다시 시도해 주십시오.") from e
    sent = {t for t, _, _ in items[:MAX_TERMS]}
    out = {}
    for row in data.get("items", []):
        term, meaning = str(row.get("term", "")).strip(), str(row.get("meaning", "")).strip()
        if term in sent and meaning:
            out[term] = meaning if len(meaning) <= MAX_MEANING else meaning[:MAX_MEANING - 1] + "…"
    return out
