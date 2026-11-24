"""사용자별 번역 한도(이의상 님 결정, 2026-11-07).

- 로그인 사용자: 하루에 **새 논문 1편**까지 번역한다. 그 논문 안에서는 절을 몇 개 번역하든 되고,
  예전에 번역을 시작한 논문(이미 번역된 절이 있는 논문)을 이어서 번역하는 것은 '새 논문'으로 세지 않는다.
  아주 긴 논문에 대비해 한 논문은 하루 6만 자(약 15쪽)까지.
- 로그인하지 않은 사용자: 초록·짧은 글 맛보기로 이 화면에서 3,000자까지. 논문 전체는 로그인해야 한다.
- 저장된 번역을 다시 여는 것은 API를 부르지 않아 세지 않는다(실제로 번역 API를 부른 글자만 센다).
- 하루 사용 기록 모양: {"docs": {문서 id: {"chars": 오늘 번역한 글자 수, "new": 오늘 새로 시작한 논문인지}}}
"""

PAPERS_PER_DAY = 1
MAX_CHARS_PER_PAPER = 60_000
GUEST_CHARS = 3_000


def new_papers(day_usage: dict) -> int:
    return sum(1 for v in (day_usage.get("docs") or {}).values() if v.get("new"))


def check_user(day_usage: dict, doc_id: str, started_before: bool, chars: int) -> str | None:
    """번역해도 되면 None, 안 되면 화면에 보여 줄 이유. started_before: 오늘 전에 이미 번역을 시작한 논문인지."""
    docs = day_usage.get("docs") or {}
    cur = docs.get(doc_id)
    if cur is None and not started_before and new_papers(day_usage) >= PAPERS_PER_DAY:
        return (f"오늘은 새 논문 {PAPERS_PER_DAY}편까지 번역할 수 있습니다. 오늘 번역을 시작한 논문과 예전에 번역하던 논문은 "
                "계속 번역할 수 있고, 새 논문은 내일 다시 번역할 수 있습니다. 독해 표시는 그대로 볼 수 있습니다.")
    used = (cur or {}).get("chars", 0)
    if used + chars > MAX_CHARS_PER_PAPER:
        return (f"한 논문은 하루 {MAX_CHARS_PER_PAPER:,}자까지 번역할 수 있습니다(오늘 이 논문 {used:,}자 사용, "
                f"이번 {chars:,}자). 나머지는 내일 이어서 번역해 주십시오.")
    return None


def add_user(day_usage: dict, doc_id: str, started_before: bool, chars: int) -> dict:
    """번역한 글자 수를 오늘 기록에 더한 새 기록."""
    docs = {k: dict(v) for k, v in (day_usage.get("docs") or {}).items()}
    cur = docs.setdefault(doc_id, {"chars": 0, "new": not started_before})
    cur["chars"] += chars
    return {"docs": docs}


def check_guest(used: int, chars: int) -> str | None:
    if used + chars > GUEST_CHARS:
        return (f"로그인하지 않으면 번역을 맛보기로 {GUEST_CHARS:,}자까지(초록 한 편 정도) 쓸 수 있습니다"
                f"(지금 {used:,}자 사용, 이번 {chars:,}자). 오른쪽 위에서 로그인하면 하루에 논문 1편을 끝까지 번역할 수 있습니다.")
    return None
