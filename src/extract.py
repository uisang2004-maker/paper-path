"""정규식 결과를 모아 약어·3줄 뼈대·추측/확신/참조 표시를 만든다.

정규식은 patterns.py에 있다. 이 파일은 그 결과를 조합하는 Python 로직(약어 글자 맞추기,
뼈대 선택, 겹침 정리)만 담는다.
"""

import re
from dataclasses import dataclass, field

from src import patterns as P
from src.clean import clean, split_sentences

CARD_KINDS = ("problem", "method", "result")
RULE_BY_KIND = {r.category: r for r in P.SKELETON_RULES}


@dataclass
class Sentence:
    idx: int          # 0부터
    start: int
    end: int
    text: str


@dataclass
class Mark:
    start: int
    end: int
    category: str     # abbr / abbr-missing / hedge / confident / reference
    rule: str
    tip: str


@dataclass
class SkeletonCard:
    kind: str                     # problem / method / result
    sentence_idx: int | None
    basis: str                    # signal / position / none
    label: str                    # 카드 오른쪽 위 근거 라벨
    reason: str                   # 카드 아래 한국어 이유
    signal: tuple[int, int] | None = None   # 문장 안의 신호 문구 위치
    rule: str = ""
    underline: tuple[int, int] | None = None   # 문장 안의 핵심 내용 밑줄 위치(신호 문구로 찾은 카드만)


@dataclass
class Analysis:
    text: str
    removed_lines: list[str]
    sentences: list[Sentence]
    acronyms: dict[str, str | None]
    marks: list[Mark]
    skeleton: list[SkeletonCard]
    limit_idx: list[int]
    counts: dict[str, int]
    active_rules: frozenset = field(default_factory=frozenset)
    explanations: list[tuple[int, int, str]] = field(default_factory=list)   # 용어 설명 구간(점선 밑줄)과 툴팁
    roles: list[str] = field(default_factory=list)   # 문장별 역할(흐름도): background/problem/method/result/limit/detail


# ---------------------------------------------------------------- 약어 (P0-3)

def _strip_plural(abbr: str) -> str:
    return abbr[:-1] if abbr.endswith("s") and abbr[:-1].isupper() and len(abbr) > 2 else abbr


def match_long_form(abbr: str, before: str, all_letters: bool = False) -> str | None:
    """약어 글자를 뒤에서부터 앞 단어 첫 글자(하이픈 조각 포함)와 맞춘다.

    all_letters=False면 약어의 대문자만, True면 약어의 모든 글자를 맞춘다(CoT 같은 경우).
    맞추지 못하면 None — 틀린 풀네임을 지어내지 않는다.
    """
    core = _strip_plural(abbr)
    letters = "".join(ch for ch in core if (ch.isalpha() if all_letters else ch.isupper())).lower()
    if len(letters) < 2:
        return None
    words = P.WORD.findall(before)[-(len(letters) * 2 + 3):]
    i, used = len(letters) - 1, []
    for w in reversed(words):
        heads = [p[0].lower() for p in w.split("-") if p]
        k = 0   # 이 단어가 맡는 약어 글자 수(하이픈 조각마다 하나)
        while k < len(heads) and i - k >= 0 and heads[-1 - k] == letters[i - k]:
            k += 1
        if k < len(heads):
            k = 1 if heads[0] == letters[i] else 0
        if k > 0:
            i -= k
            used.insert(0, w)
        elif used and w.lower() in P.LINKER_WORDS:
            used.insert(0, w)
        else:
            break
        if i < 0:
            break
    if i >= 0 or not used:
        return None
    while used and used[0].lower() in P.LINKER_WORDS:
        used.pop(0)
    return " ".join(used) or None


def find_acronyms(text: str) -> dict[str, str | None]:
    """{약어: 풀네임 또는 None}. 정의를 못 찾은 약어도 None으로 넣는다."""
    found: dict[str, str | None] = {}
    for m in P.ACRONYM_CANDIDATE.finditer(text):
        abbr = m.group("abbr")
        key = _strip_plural(abbr)
        before = text[max(0, m.start() - 200):m.start()]
        long_form = match_long_form(abbr, before) or match_long_form(abbr, before, all_letters=True)
        if long_form:
            found[key] = long_form
        else:
            found.setdefault(key, None)
    for m in P.BARE_ACRONYM.finditer(text):
        key = _strip_plural(m.group(0))
        if key in P.PROTO_MARK_WORDS:            # 'CAUTION'·'CRITICAL STEP'은 약어가 아니라 프로토콜 표시
            continue
        upper = sum(ch.isupper() for ch in key)
        if upper >= 2 and upper / len(key) >= P.BARE_MIN_UPPER_RATIO:
            found.setdefault(key, None)
    return found


# ---------------------------------------------------------------- 단어 단위 표시 (P0-3, P0-4)

PRIORITY = {"reference": 0, "abbr": 1, "abbr-missing": 1, "number": 2, "definition": 2, "confident": 3, "hedge": 4, "term": 5}


def _norm_term(w: str) -> str:
    w = w.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ss", "us", "is")):
        return w
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def frequent_terms(text: str) -> list[tuple[str, int, list[tuple[int, int]]]]:
    """3번 이상 나오는 용어와 등장 위치. 두 단어 용어를 먼저, 남은 한 단어를 그다음에 찾는다."""
    toks = [(m.start(), m.end(), _norm_term(m.group(0))) for m in P.TERM_WORD.finditer(text)]
    content = [t for t in toks if t[2] not in P.TERM_STOPWORDS]
    pairs: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for (a1, b1, w1), (a2, b2, w2) in zip(content, content[1:]):
        if re.fullmatch(r"[ \t]+", text[b1:a2]) and w1 not in P.TERM_GENERIC:
            pairs.setdefault((w1, w2), []).append((a1, b2))
    out, taken = [], []
    for (w1, w2), spans in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
        free = [sp for sp in spans if all(sp[1] <= a or sp[0] >= b for a, b in taken)]
        if len(free) >= P.TERM_MIN_COUNT:
            out.append((f"{w1} {w2}", len(free), free))
            taken += free
    singles: dict[str, list[tuple[int, int]]] = {}
    for a, b, w in content:
        if w in P.TERM_GENERIC or w.endswith("ed") or any(x <= a < y for x, y in taken):
            continue
        singles.setdefault(w, []).append((a, b))
    for w, spans in singles.items():
        if len(spans) >= P.TERM_MIN_COUNT:
            out.append((w, len(spans), spans))
    return out


def _marks(text: str, acronyms: dict[str, str | None], active: frozenset) -> list[Mark]:
    marks: list[Mark] = []
    if "REFERENCE" in active:
        for m in P.REFERENCE.finditer(text):
            marks.append(Mark(m.start(), m.end(), "reference", "REFERENCE", f"참조 · {P.REFERENCE_RULE.reason}"))
    if "ABBR" in active:
        for abbr, long_form in acronyms.items():
            for m in re.finditer(rf"\b{re.escape(abbr)}s?\b", text):
                if long_form:
                    marks.append(Mark(m.start(), m.end(), "abbr", "ABBR", f"{abbr} = {long_form} · 규칙 ABBR"))
                else:
                    marks.append(Mark(m.start(), m.end(), "abbr-missing", "ABBR", f"{abbr}: 이 글에서 정의를 찾지 못함 · 규칙 ABBR"))
    if "CONFIDENT" in active:
        for m in P.CONFIDENT.finditer(text):
            marks.append(Mark(m.start(), m.end(), "confident", "CONFIDENT", f"확신 · {P.CONFIDENT_RULE.reason}"))
    if "NUMBER" in active:
        for m in P.EVIDENCE_NUMBER.finditer(text):
            marks.append(Mark(m.start(), m.end(), "number", "NUMBER", f"수치 근거 · {P.NUMBER_RULE.reason}"))
    if "DEFINITION" in active:
        for m in P.DEFINITION_CUE.finditer(text):
            marks.append(Mark(m.start(), m.end(), "definition", "DEFINITION", f"용어 설명·예시 · {P.DEFINITION_RULE.reason}"))
    if "TERM" in active:
        for term, n, spans in frequent_terms(text):
            for x, y in spans:
                marks.append(Mark(x, y, "term", "TERM", f"반복 용어 · '{term}'이(가) 이 글에 {n}번 나와요"))
    if "HEDGE" in active:
        excluded = [(m.start(), m.end()) for m in P.HEDGE_EXCLUDE.finditer(text)] if "HEDGE_EXCLUDE" in active else []
        for m in P.HEDGE.finditer(text):
            if any(a <= m.start() < b for a, b in excluded):
                continue
            marks.append(Mark(m.start(), m.end(), "hedge", "HEDGE", f"추측·가능성 · {P.HEDGE_RULE.reason}"))

    # 시약 품번 괄호 '(Sigma-Aldrich, cat. no. B3883)' 안에는 표시하지 않는다('cat'이 반복 용어가 되는 등)
    catalog = [(m.start(), m.end()) for m in P.CATALOG_PAREN.finditer(text)]
    if catalog:
        marks = [mk for mk in marks if not any(a <= mk.start and mk.end <= b for a, b in catalog)]
    # 겹치면 우선순위(참조 > 약어 > 수치·용어 설명 > 확신 > 추측)가 높은 것, 같으면 긴 것을 남긴다.
    marks.sort(key=lambda x: (PRIORITY[x.category], -(x.end - x.start), x.start))
    kept: list[Mark] = []
    for mk in marks:
        if all(mk.end <= k.start or mk.start >= k.end for k in kept):
            kept.append(mk)
    return sorted(kept, key=lambda x: x.start)


# ---------------------------------------------------------------- 핵심 내용 밑줄

def _cap(s: str, a: int, b: int, stop: list[re.Pattern]) -> tuple[int, int] | None:
    """[a, b) 구간을 경계에서 멈추고 단어 상한으로 자른 뒤, 2단어 이상이면 돌려준다."""
    seg = s[a:b]
    for pat in stop:
        m = pat.search(seg)
        if m and m.start() > 0:
            seg = seg[:m.start()]
    words = list(re.finditer(r"\S+", seg))
    if len(words) > P.UNDERLINE_MAX_WORDS:
        seg = seg[:words[P.UNDERLINE_MAX_WORDS - 1].end()]
        seg = P.UL_TRAILING_FUNCTION.sub("", seg)   # 상한에서 잘릴 때 'compared with the'처럼 기능어로 끝나지 않게
    seg = seg.rstrip(" ,.;:")
    if len(re.findall(r"[A-Za-z0-9]+", seg)) < 2:
        return None
    return a, a + len(seg)


def _after(s: str, pos: int) -> int:
    return pos + P.UL_LEADING_SKIP.match(s[pos:]).end()


def underline_span(kind: str, s: str, signal: tuple[int, int]) -> tuple[int, int] | None:
    """신호 문구가 가리키는 핵심 내용 구간. 형광펜은 신호(어떻게 말하나), 밑줄은 내용(무엇을 말하나)."""
    a, b = signal
    sig = s[a:b]
    stop = [P.UL_STOP] if kind == "method" else [P.UL_STOP, P.UL_STOP_WHICH]

    # 수동태: 'X is introduced', 'Y were observed' → 동사 앞 주어
    if P.UL_PASSIVE.match(sig):
        head = s[:a]
        commas = list(P.UL_SUBJECT_COMMA.finditer(head))
        start = _after(s, commas[-1].end() if commas else (head.rfind(",") + 1 if "," in head else 0))
        comma = s.find(",", start, a)          # 주어 뒤 삽입절(', when evaluated on X,')은 뺀다
        return _cap(s, start, comma if comma > 0 else a, [])

    # 양보절로 시작: 'Although X, Y' → 주어가 시작되는 쉼표 뒤의 주절
    if kind == "problem" and P.UL_CONCESSION_START.match(s):
        commas = list(P.UL_SUBJECT_COMMA.finditer(s, b))
        start = commas[-1].end() if commas else (s.rfind(",") + 1 if s.rfind(",") > b else None)
        return _cap(s, _after(s, start), len(s), stop) if start else None

    # 대조어·보고 동사·제안 동사 뒤: 신호 뒤부터
    if (kind == "problem" and P.UL_CONTRAST_START.match(sig)) or P.UL_REPORTING_END.search(sig) \
            or kind == "method":
        start = b
        if kind == "method" and re.search(r"\bwe$", sig, re.I):      # 'In this paper, we propose ...' → 동사 건너뛰기
            m = re.match(r"\s+\w+", s[b:])
            start = b + (m.end() if m else 0)
        return _cap(s, _after(s, start), len(s), stop)

    # 신호가 내용 안에 있는 경우('... remains challenging', 'outperforms ...', 'by 8.4%') → 신호가 든 절
    starts = [m.end() for m in P.UL_CLAUSE_START.finditer(s[:a])]
    start = starts[-1] if starts else 0
    end = len(s)
    m = re.search(r"[,;]\s", s[b:])
    if m:
        end = b + m.start()
    return _cap(s, _after(s, start), end, stop)


# ---------------------------------------------------------------- 용어 설명 구간

EXAMPLE_CUES = ("such as", "including", "for example", "for instance", "namely", "e.g.")


def _term_before(text: str, pos: int) -> str:
    """설명 신호 앞의 용어(최대 4단어)를 툴팁용으로 고른다."""
    head = text[max(0, pos - 80):pos].rstrip(" ,(")
    words = re.findall(r"[A-Za-z0-9][\w-]*", head)[-3:]
    while words and words[0].lower() in {"we", "our", "this", "the", "a", "an", "is", "are", "and", "of", "to", "it"}:
        words.pop(0)
    return " ".join(words)


def explanation_spans(text: str, sentences: list[Sentence]) -> list[tuple[int, int, str]]:
    """용어 설명·예시 신호 뒤의 설명 구간(점선 밑줄). 문장을 넘지 않고, 12단어 이내."""
    out: list[tuple[int, int, str]] = []

    def add(a: int, b: int, tip: str) -> None:
        sent = next((s for s in sentences if s.start <= a < s.end), None)
        if sent is None:
            return
        b = min(b, sent.end)
        seg = text[a:b]
        words = list(re.finditer(r"\S+", seg))
        if len(words) > P.UNDERLINE_MAX_WORDS:
            seg = P.UL_TRAILING_FUNCTION.sub("", seg[:words[P.UNDERLINE_MAX_WORDS - 1].end()])
        seg = seg.rstrip(" ,.;:")
        if seg.endswith(")") and seg.count("(") < seg.count(")"):
            seg = seg[:-1].rstrip()
        if len(re.findall(r"[A-Za-z0-9]+", seg)) >= 1 and all(b0 <= a or a0 >= a + len(seg) for a0, b0, _ in out):
            out.append((a, a + len(seg), tip))

    for m in P.DEFINITION_CUE.finditer(text):
        cue = m.group(0).lower()
        start = m.end() + len(re.match(r"[\s,]*", text[m.end():]).group(0))
        rest = text[start:]
        if cue.startswith(("i.e", "e.g")) and text[m.start() - 1:m.start()] == "(":
            stop = re.search(r"\)", rest)                       # (i.e., …) → 닫는 괄호까지
        elif any(cue.startswith(c) for c in EXAMPLE_CUES):
            stop = re.search(r"[.;]|,\s(?:which|and\s+(?:we|it|they)|but|while)\b", rest)   # 예시 목록은 쉼표로 끊지 않음
        else:
            stop = re.search(r"[,.;)]", rest)
        end = start + (stop.start() if stop else len(rest))
        if text[start:end].count("(") > text[start:end].count(")"):   # '(NLI' 처럼 괄호가 열린 채 끝나면 닫는 괄호까지
            close = text.find(")", end)
            end = close + 1 if close > 0 else end
        kind = "예시" if any(cue.startswith(c) for c in EXAMPLE_CUES) else "설명"
        term = "" if cue.startswith(("i.e", "that is", "namely")) else _term_before(text, m.start())
        add(start, end, f"용어 {kind} · '{term}'의 {kind}" if term else f"용어 {kind} · 앞 내용을 풀어 쓴 부분")
    for m in P.APPOSITIVE.finditer(text):
        a = m.start("art")
        b = text.find(",", a)
        add(a, b if b > 0 else a, f"용어 설명 · '{m.group('term')}'의 설명(동격)")
    return sorted(out)


# ---------------------------------------------------------------- 3줄 뼈대 (P0-2)

def _signal(kind: str, sent: Sentence):
    m = RULE_BY_KIND[kind].pattern.search(sent.text)
    return (m.start(), m.end()) if m else None


def _excluded_for_fallback(sent: Sentence) -> bool:
    return bool(P.PUBLIC_RELEASE.search(sent.text) or P.SK_LIMIT.search(sent.text))


def select_skeleton(sentences: list[Sentence], use_fallback: bool = True) -> tuple[list[SkeletonCard], list[int]]:
    """문제·방법·결과 카드 3장과 한계 후보 문장 번호를 돌려준다. 빈 카드는 없다(찾지 못하면 그 상태를 표시)."""
    used: set[int] = set()
    cards: dict[str, SkeletonCard] = {}

    # 1) 신호 문구 — 방법 → 결과 → 문제 순서로 고른다(방법·결과 신호가 더 구체적이라서).
    for kind in ("method", "result", "problem"):
        rule = RULE_BY_KIND[kind]
        for s in sentences:
            if s.idx in used:
                continue
            sig = _signal(kind, s)
            if sig and not (kind == "result" and P.PUBLIC_RELEASE.search(s.text)):
                cards[kind] = SkeletonCard(kind, s.idx, "signal", "신호 문구로 찾음", rule.reason, sig, rule.name,
                                           underline_span(kind, s.text, sig))
                used.add(s.idx)
                break

    # 2) 위치 대체 — 신호 문구가 없을 때만.
    if use_fallback:
        if "problem" not in cards and sentences and sentences[0].idx not in used and not _excluded_for_fallback(sentences[0]):
            s = sentences[0]
            cards["problem"] = SkeletonCard("problem", s.idx, "position", "분야 배경 (첫 문장)",
                                            "신호 문구가 없어 첫 문장을 보여 줘요. 초록 첫 문장은 대개 분야 배경이에요",
                                            None, "SK_FALLBACK")
            used.add(s.idx)
        if "method" not in cards:
            for s in sentences:
                if s.idx not in used and P.AUTHOR_VOICE.search(s.text) and not _excluded_for_fallback(s):
                    m = P.AUTHOR_VOICE.search(s.text)
                    cards["method"] = SkeletonCard("method", s.idx, "position", "저자 서술 문장 (방법 추정)",
                                                   "방법 신호가 없어 저자(we/our)가 쓴 첫 문장을 골랐어요",
                                                   (m.start(), m.end()), "SK_FALLBACK")
                    used.add(s.idx)
                    break
        if "result" not in cards:
            pool = [s for s in sentences[1:] if s.idx not in used and not _excluded_for_fallback(s)]
            hinted = [s for s in pool if P.RESULT_HINT.search(s.text)]
            pick = (hinted or pool)[-1] if (hinted or pool) else None
            if pick:
                cards["result"] = SkeletonCard("result", pick.idx, "position", "마무리 문장 (결과 추정)",
                                               "결과 신호가 없어 뒤쪽 문장 중 수치·결과 동사가 있는 문장을 골랐어요",
                                               None, "SK_FALLBACK")
                used.add(pick.idx)

    missing = {"problem": "문제 문장을 찾지 못함", "method": "방법 문장을 찾지 못함", "result": "결과 문장을 찾지 못함"}
    out = [cards.get(k) or SkeletonCard(k, None, "none", missing[k], "규칙에 맞는 문장이 없어 비워 두지 않고 상태를 표시해요")
           for k in CARD_KINDS]
    limits = [s.idx for s in sentences if P.SK_LIMIT.search(s.text)]
    return out, limits


# ---------------------------------------------------------------- 문장 역할(흐름도)

ROLE_ORDER = ("background", "problem", "method", "result", "limit")


def sentence_roles(sentences: list[Sentence], skeleton: list[SkeletonCard]) -> list[str]:
    """문장마다 흐름도의 역할을 붙인다.

    3줄 뼈대 카드로 뽑힌 문장은 그 역할(첫 문장 대체 카드는 '배경'), 나머지는 신호 문구로
    한계 → 방법 → 결과 → 문제 순서로 고른다. 신호가 없으면 첫 문제·방법 문장 앞은 '배경', 뒤는 '보충'.
    """
    roles: list[str | None] = [None] * len(sentences)
    for c in skeleton:
        if c.sentence_idx is not None:
            roles[c.sentence_idx] = "background" if (c.kind == "problem" and c.basis == "position") else c.kind
    checks = (("limit", P.SK_LIMIT), ("method", P.SK_METHOD), ("result", P.SK_RESULT), ("problem", P.SK_PROBLEM))
    for s in sentences:
        if roles[s.idx] is None:
            roles[s.idx] = next((r for r, pat in checks if pat.search(s.text)), None)
    first_core = next((i for i, r in enumerate(roles) if r in ("problem", "method")), len(roles))
    return [r or ("background" if i < first_core else "detail") for i, r in enumerate(roles)]


# ---------------------------------------------------------------- 전체

def analyze(raw: str, active_rules: frozenset | set | None = None) -> Analysis:
    active = frozenset(P.DEFAULT_ACTIVE_RULES if active_rules is None else active_rules)
    cleaned = clean(raw)
    text = cleaned.text
    sentences = [Sentence(i, s, e, text[s:e]) for i, (s, e) in enumerate(split_sentences(text))]
    acronyms = find_acronyms(text)
    marks = _marks(text, acronyms, active)
    skeleton, limits = select_skeleton(sentences, use_fallback="SK_FALLBACK" in active)
    explanations = explanation_spans(text, sentences) if "DEFINITION" in active else []
    counts = {
        "abbr": len(acronyms) if "ABBR" in active else 0,
        "hedge": sum(m.category == "hedge" for m in marks),
        "confident": sum(m.category == "confident" for m in marks),
        "reference": sum(m.category == "reference" for m in marks),
        "definition": len(explanations),
        "number": sum(m.category == "number" for m in marks),
        "term": len({m.tip for m in marks if m.category == "term"}),
    }
    return Analysis(text, cleaned.removed_lines, sentences, acronyms, marks, skeleton, limits, counts, active, explanations,
                    sentence_roles(sentences, skeleton))
