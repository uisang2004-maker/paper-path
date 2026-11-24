"""공개 사이트에서 번역 비용(Claude)과 무료 한도(DeepL)를 지키기 위한 번역 사용 제한.

- 한 번에 번역할 수 있는 글자 수, 한 사람(세션)당 번역 횟수, 하루 전체 글자 수에 상한을 둔다.
- 하루 전체 사용량은 서버의 작은 파일에 날짜와 함께 센다. 서버가 재시작되면 0부터 다시 셀 수 있어
  대략적인 보호 장치다(DeepL 계정의 실제 사용량이 기준).
- 사용자 텍스트는 저장하지 않고 글자 수만 센다.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USAGE_PATH = ROOT / "data" / "processed" / "usage_today.json"

MAX_CHARS_PER_REQUEST = 3000     # 초록 한 편 정도
MAX_CALLS_PER_SESSION = 10       # 번역하기·마스킹 비교를 합친 DeepL 호출 횟수
DAILY_CHAR_CAP = 30000           # 하루 전체(월 50만 자 무료 한도의 약 6%)


@dataclass(frozen=True)
class Limits:
    per_request: int
    per_session: int
    daily: int


LIMITS = {
    "deepl": Limits(MAX_CHARS_PER_REQUEST, MAX_CALLS_PER_SESSION, DAILY_CHAR_CAP),
    # Claude Haiku 5.5: 논문 한 편(약 3만~5만 자)을 절별로 번역할 수 있게 넓혔다. 30만 자 ≈ 하루 $0.1 안팎(요금표 추정).
    # 긴 절은 이 글자 수 이하 묶음으로 나눠 보내므로, 한 화면 호출 수는 묶음 수로 센다.
    # 하루 사이트 전체 150만 자: 사용자별 한도(src/quota.py, 로그인 사용자 하루 논문 1편)가 생겨 마지막 안전장치로 넓힘
    # (동료 평가 날 여러 명이 동시에 써도 막히지 않도록). 150만 자 ≈ 하루 1달러 안팎(요금표 추정).
    "claude": Limits(4000, 60, 1_500_000),
}


def _today() -> str:
    return date.today().isoformat()


def used_today(path: Path | None = None) -> int:
    path = path or USAGE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    return int(data.get("chars", 0)) if data.get("date") == _today() else 0


def record(chars: int, path: Path | None = None) -> None:
    path = path or USAGE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": _today(), "chars": used_today(path) + chars}), encoding="utf-8")


def check_batch(chunk_chars: list[int], session_calls: int, path: Path | None = None,
                engine: str = "deepl") -> str | None:
    """긴 글을 여러 묶음으로 동시에 보낼 때: 보내기 전에 묶음 전체를 한꺼번에 확인한다."""
    if not chunk_chars:
        return None
    reason = check(max(chunk_chars), session_calls + len(chunk_chars) - 1, path, engine)
    if reason:
        return reason
    if used_today(path) + sum(chunk_chars) > LIMITS[engine].daily:
        return "오늘 사이트 전체의 번역 한도에 도달했습니다. 내일 다시 시도해 주십시오. 독해 표시는 그대로 볼 수 있습니다."
    return None


def check(chars: int, session_calls: int, path: Path | None = None, engine: str = "deepl",
          per_request: bool = True) -> str | None:
    """번역해도 되면 None, 안 되면 화면에 보여 줄 한국어 이유.
    per_request=False는 단어 뜻 풀이처럼 한 번에 보내는 글 길이를 따로 정해 둔 요청(한 번 한도는 보지 않음)."""
    lim = LIMITS[engine]
    if per_request and chars > lim.per_request:
        return (f"번역은 한 번에 {lim.per_request:,}자까지만 할 수 있습니다(지금 {chars:,}자). "
                "초록이나 단락 하나씩 넣어 주십시오. 독해 표시는 그대로 볼 수 있습니다.")
    if session_calls >= lim.per_session:
        return f"이 화면에서는 번역을 {lim.per_session}번까지 쓸 수 있습니다. 새로고침하면 다시 쓸 수 있습니다."
    if used_today(path) + chars > lim.daily:
        return "오늘 사이트 전체의 번역 한도에 도달했습니다. 내일 다시 시도해 주십시오. 독해 표시는 그대로 볼 수 있습니다."
    return None
