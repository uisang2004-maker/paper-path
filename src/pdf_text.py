"""논문 PDF → 읽는 순서대로 된 텍스트 줄.

- 글자와 위치는 pdfplumber(pdfminer.six 기반)로 읽는다. AI·OCR은 쓰지 않는다(스캔 PDF는 글자가 없어 처리 불가).
- 2단 편집: 줄마다 쪽 가운데에 단 사이 빈틈(gutter)이 있는지 보고, 빈틈이 있는 줄이 이어지는 구간은
  왼쪽 단 → 오른쪽 단 순서로 읽는다. 제목·초록처럼 쪽 전체 폭을 쓰는 줄은 그 자리에서 그대로 읽는다.
- 줄은 글자 아랫선(baseline)으로 묶고, 붙어 있는 낱말 조각(작은 대문자 글꼴의 `I` + `NTRODUCTION`)은 띄우지 않고 잇는다.
- 줄 사이 간격이 평소보다 크면 빈 줄(문단 경계)을 넣는다. 절 나누기(src/sections.py)는 이 줄들을 정규식으로 본다.
- 줄 전체가 제목 글꼴이면 제목 후보 표시를 붙인다(본문보다 크면 HEAD_MARK, 같으면 SUB_MARK).
  제목 글꼴 = 이름에 Bold가 든 글꼴, 또는 이름이 가려진 글꼴(AdvOT5777b7ad)이라도 본문 크기 이상에서 짧은 단독 줄로만 쓰이는 글꼴.
  제목이 본문보다 왼쪽으로 내어 쓰인 형식(Nature Protocols)은 내어 쓴 줄이 절, 본문 줄에 맞춘 줄이 소제목.
  번호 없는 제목(백과사전·인문학 글)을 찾기 위한 단서일 뿐이고, 제목으로 받을지는 sections.py의 정규식이 정한다.
"""

import io
import re
from dataclasses import dataclass, field
from statistics import median

from src import patterns as P

GUTTER_MIN = 8.0          # 단 사이 빈틈으로 볼 최소 너비(pt). 낱말 사이 띄어쓰기는 보통 2~4pt
LINE_TOL = 2.5            # 같은 줄로 볼 글자 아랫선 차이(pt). 윗선은 작은 대문자 글꼴에서 글자마다 달라 쓰지 않는다
JOIN_GAP = 0.8            # 낱말 사이가 이보다 좁으면(글꼴만 바뀐 조각) 띄어 쓰지 않고 잇는다
PARA_GAP_RATIO = 1.45     # 줄 간격이 보통의 이 배 이상이면 문단 경계
MAX_PAGES = 40            # 너무 긴 PDF(학위논문·책)는 앞쪽만 읽는다
SMALL_GAP = 0.9           # 본문 글자보다 이만큼(pt) 이상 작은 줄은 각주·캡션·표 글자로 보고 표시해 둔다
SMALL_MARK = "\x1f"       # 작은 글자 줄 앞에 붙이는 표시(src/sections.py가 보고 뗀다)
HEAD_MARK = "\x1e"        # 본문보다 큰 굵은 글꼴 줄(절 제목 후보)
SUB_MARK = "\x1d"         # 본문 크기의 굵은 글꼴 줄(소제목 후보)
RUNIN_MARK = "\x1c"       # 문단 첫머리에 굵게 붙은 제목('Separated. We considered …'): 표시 + 제목 + RUNIN_SEP + 나머지
RUNIN_SEP = "\x1b"
MARKS = SMALL_MARK + HEAD_MARK + SUB_MARK + RUNIN_MARK
RUNIN_MAX_WORDS = 10      # 문단 첫머리 굵은 제목의 최대 낱말 수
RUNIN_NOT = re.compile(r"^(?:Fig(?:ure)?|Table|Algorithm|Eq(?:uation)?|Step|Note)\b", re.I)   # 그림·표 설명 첫머리 등은 제목이 아님
SENT_DONE = re.compile(r"[.!?:)\]”\"']\s*$")
HEAD_GAP = 0.5            # 본문보다 이만큼(pt) 이상 크면 절 제목 후보, 아니면 소제목 후보
BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi|medi(?:um)?\b|[-_,](?:b|bd|bx|hev)\b|cmbx", re.I)
ITALIC_FONT = re.compile(r"ital|obli|[-_.](?:i|it|lightit)$", re.I)
HEAD_FONT_SHORT = 0.75    # 이름에 Bold가 없는 글꼴은 줄의 이만큼 이상이 짧은 단독 줄이어야 제목 글꼴
OUTDENT = 20.0            # 본문 줄 왼쪽 끝보다 이만큼(pt) 이상 왼쪽에서 시작하면 내어 쓴 제목


@dataclass
class PdfText:
    lines: list[str]
    pages: int
    read_pages: int
    title: str = ""
    two_column_pages: int = 0
    notes: list[str] = field(default_factory=list)


class PdfError(Exception):
    pass


def _group_lines(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["bottom"]), w["x0"])):
        if rows and abs(rows[-1][0]["bottom"] - w["bottom"]) <= LINE_TOL:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _split_at_gutter(row: list[dict], mid: float) -> tuple[list[dict], list[dict]] | None:
    """이 줄이 가운데 빈틈으로 나뉘면 (왼쪽 낱말, 오른쪽 낱말), 쪽 전체 폭을 쓰는 줄이면 None."""
    left = [w for w in row if w["x1"] <= mid + 1]
    right = [w for w in row if w["x0"] >= mid - 1]
    if len(left) + len(right) != len(row):
        return None                       # 가운데를 가로지르는 낱말이 있음
    if left and right and right[0]["x0"] - left[-1]["x1"] < GUTTER_MIN:
        return None                       # 가운데에 빈틈이 없음(한 단 문장)
    return left, right


def _text(ws: list[dict]) -> str:
    out = ""
    for i, w in enumerate(ws):
        if i and w["x0"] - ws[i - 1]["x1"] > JOIN_GAP:
            out += " "
        out += w["text"]
    return out


def _font(w: dict) -> str:
    """낱말의 글꼴(글자들이 한 글꼴일 때). 'ABCDEF+Helvetica-Bold' → 'Helvetica-Bold'. 낱말 나누기는 글꼴과 상관없이 그대로 둔다."""
    fonts = {c.get("fontname", "").split("+")[-1] for c in w.get("chars", ())}
    return fonts.pop() if len(fonts) == 1 else "?"


SYMBOL_WORD = re.compile(r"[^\w]+")      # 글자·숫자가 없는 낱말(●, •): 줄 글꼴을 정할 때 뺀다


def _lead(ws: list[dict]) -> tuple | None:
    """줄 첫머리에서 같은 글꼴로 이어지는 낱말들과 나머지: (앞 글자, 앞 글꼴, 나머지 글자, 나머지 첫 글꼴). 한 글꼴이면 None.
    문단 첫머리 굵은 제목('Separated. We considered …')을 찾는 데 쓴다."""
    first = _font(ws[0])
    for k in range(1, len(ws)):
        f = _font(ws[k])
        if f != first and not (SYMBOL_WORD.fullmatch(ws[k]["text"]) and len(ws[k]["text"]) == 1 and ws[k]["text"] in ".:"):
            return _text(ws[:k]), first, _text(ws[k:]), f
    return None


def _line(ws: list[dict], partner: str | None = None) -> tuple:
    """(아랫선, 글자 크기 중앙값, 줄 글자, 가장 큰 글자 크기, 줄 전체가 한 글꼴이면 그 이름 아니면 "", 왼쪽 끝 x,
    반대쪽 칸 글꼴, 첫머리 글꼴 조각(_lead)). 반대쪽 칸 글꼴은 쪽 전체 폭 줄이면 None, 가운데 빈틈으로 가른 줄이면
    같은 높이 반대쪽 칸의 글꼴("" = 없음)."""
    head = ws
    for k in range(1, len(ws)):                 # '제목 ● Timing 5–8 h': 글꼴은 소요 시간 안내 앞 제목 부분으로만 정한다
        if ws[k]["text"] == "Timing" and SYMBOL_WORD.fullmatch(ws[k - 1]["text"]):
            head = ws[:k]
            break
    fonts = {_font(w) for w in head if not SYMBOL_WORD.fullmatch(w["text"])} or {_font(w) for w in ws}
    return (ws[0]["bottom"], median(w["size"] for w in ws), _text(ws), max(w["size"] for w in ws),
            fonts.pop() if len(fonts) == 1 and "?" not in fonts else "", ws[0]["x0"], partner, _lead(ws))


def _page_lines(page) -> tuple[list[tuple[float, float, str, float]], bool]:
    """한 쪽의 줄 목록(_line)을 읽는 순서로. 두 번째 값은 2단 구간이 있었는지."""
    # x_tolerance를 기본(3)보다 줄여야 양쪽 맞춤으로 좁아진 띄어쓰기도 낱말 경계로 본다.
    # 세로로 찍힌 글자(arXiv 옆면 도장 등)는 본문이 아니라서 뺀다.
    words = [w for w in page.extract_words(x_tolerance=1.2, keep_blank_chars=False, use_text_flow=False,
                                           extra_attrs=["size", "upright"], return_chars=True) if w["upright"]]
    if not words:
        return [], False
    mid = float(page.width) / 2
    rows = _group_lines(words)
    out: list[tuple[float, float, str, float]] = []
    block_l: list[tuple[float, float, str, float]] = []
    block_r: list[tuple[float, float, str, float]] = []
    split_rows = 0

    def flush():
        out.extend(block_l)
        out.extend(block_r)
        block_l.clear()
        block_r.clear()

    for row in rows:
        parts = _split_at_gutter(row, mid)
        if parts is None:
            flush()
            out.append(_line(row))
            continue
        left, right = parts
        if left and right:
            split_rows += 1
            block_l.append(_line(left, _line(right)[4]))     # 같은 높이 반대쪽 칸의 글꼴(표 머리글 판단용)
            block_r.append(_line(right, _line(left)[4]))
            continue
        if left:
            block_l.append(_line(left, ""))
        if right:
            block_r.append(_line(right, ""))
    flush()
    return out, split_rows >= 5


def _body_size(pages: list[list[tuple[float, float, str, float]]]) -> float:
    """본문 글자 크기: 글자 수로 가중한 크기 중앙값."""
    sizes = sorted((round(r[1], 1), len(r[2])) for rows in pages for r in rows)
    total, acc = sum(n for _, n in sizes), 0
    for size, n in sizes:
        acc += n
        if acc * 2 >= total:
            return size
    return 10.0


def _body_font(pages) -> str:
    """본문 글꼴: 글자 수가 가장 많은 글꼴(한 글꼴로만 된 줄 기준)."""
    count: dict[str, int] = {}
    for rows in pages:
        for r in rows:
            if r[4]:
                count[r[4]] = count.get(r[4], 0) + len(r[2])
    return max(count, key=count.get) if count else ""


def _head_fonts(pages, body: float, body_font: str) -> set[str]:
    """제목 글꼴: 이름에 Bold가 든 글꼴(굵은 기울임 포함), 또는 본문 크기 이상인 줄이 3개 이상이고 그중
    HEAD_FONT_SHORT 이상이 12낱말 이하인 글꼴. 기울이기만 한 글꼴(책 제목 등)과 본문 글꼴은 뺀다."""
    lines: dict[str, list[int]] = {}
    for rows in pages:
        for r in rows:
            if r[4] and r[4] != body_font and r[1] >= body - 0.2:
                lines.setdefault(r[4], []).append(len(P.HEAD_NOTE.sub("", r[2]).split()))
    out = set()
    for font, words in lines.items():
        if BOLD_FONT.search(font):
            out.add(font)
        elif not ITALIC_FONT.search(font) and len(words) >= 3 and sum(n <= 12 for n in words) >= HEAD_FONT_SHORT * len(words):
            out.add(font)
    return out


def _repeated(pages) -> set[str]:
    """여러 쪽에 똑같이 찍힌 줄(워터마크·쪽 머리글 'PROTOCOL'): 2쪽 이상, 그리고 절반 이상의 쪽에 나온 글자."""
    seen: dict[str, int] = {}
    for rows in pages:
        for t in {r[2] for r in rows}:
            seen[t] = seen.get(t, 0) + 1
    return {t for t, n in seen.items() if len(pages) >= 2 and n >= 2 and n * 2 >= len(pages)}


def _body_left(rows, body_font: str) -> float | None:
    """이 쪽 본문 줄의 왼쪽 끝: 본문 글꼴로 된 긴 줄의 시작 x를 5pt 단위로 묶어, 줄의 25% 이상이 모인 묶음 중
    가장 왼쪽. 2단 논문은 왼쪽 단이 잡혀서 어느 단의 제목도 '내어 쓴 줄'이 되지 않는다."""
    xs = [round(r[5] / 5) * 5 for r in rows if r[4] == body_font and len(r[2].split()) >= 6]
    if not xs:
        return None
    return min(x for x in set(xs) if xs.count(x) * 4 >= len(xs))


def _head_marks(pages, body: float, body_font: str) -> list[dict[int, str]]:
    """쪽마다 {줄 번호: HEAD_MARK|SUB_MARK}. 단계는 기본으로 글자 크기(본문보다 크면 절)로 정하고,
    제목이 본문보다 왼쪽으로 내어 쓰인 형식이면(내어 쓴 제목·안 내어 쓴 제목이 각각 2개 이상) 내어 쓴 줄이 절."""
    fonts = _head_fonts(pages, body, body_font)
    rep = _repeated(pages)
    cands = []                                      # (쪽, 줄, 크기로 정한 표시, 내어 썼는지)
    for p, rows in enumerate(pages):
        left = _body_left(rows, body_font)
        for i, r in enumerate(rows):
            partner = r[6]
            if (r[4] not in fonts or r[2] in rep or len(P.HEAD_NOTE.sub("", r[2]).split()) > 12 or r[1] < body - 0.2
                    or (partner and partner != body_font and partner in fonts)):
                continue                            # 반대쪽 칸도 제목 글꼴이면 표 머리글('Child's Activity | Others Involved')
            by_size = HEAD_MARK if r[1] >= body + HEAD_GAP else SUB_MARK
            out_dent = left is not None and r[5] < left - OUTDENT
            cands.append((p, i, by_size, out_dent))
    n_out = sum(c[3] for c in cands)
    by_indent = n_out >= 2 and len(cands) - n_out >= 2
    marks: list[dict[int, str]] = [{} for _ in pages]
    for p, i, by_size, out_dent in cands:
        marks[p][i] = (HEAD_MARK if out_dent else SUB_MARK) if by_indent else by_size
    return marks


def _bold_variant(font: str, body_font: str) -> bool:
    """본문 글꼴의 굵은 변형: 이름에 Bold가 있거나, 본문 글꼴 이름 뒤에 B가 붙은 것('LinLibertineT' → 'LinLibertineTB')."""
    if not font or font in ("?", body_font):
        return False
    return bool(BOLD_FONT.search(font)) or (bool(body_font) and font.startswith(body_font) and "B" in font[len(body_font):])


def _prose_next(rows, i: int, body: float, body_font: str) -> bool:
    """문단 첫머리 제목 뒤가 정말 본문인지: 다음 줄이 본문 글꼴·본문 크기의 5낱말 이상 줄이고, 이 줄 나머지에 소문자 낱말이
    있어야 한다. 그림 속 글자('person:0.918')·표 첫 칸·작은 글자 각주는 여기서 걸러진다."""
    r = rows[i]
    if not re.search(r"\b[a-z]{3,}\b", r[7][2]):
        return False
    if i + 1 >= len(rows):
        return True                              # 쪽 끝 줄이면 다음 쪽에서 이어진다
    n = rows[i + 1]
    return n[4] == body_font and abs(n[1] - body) <= 0.8 and len(n[2].split()) >= 5


def _runin_marks(pages, body: float, body_font: str, marks: list[dict[int, str]]) -> None:
    """문단 첫머리 굵은 제목 줄에 RUNIN_MARK. 조건: 앞 조각이 본문의 굵은 변형이고 뒤가 본문 글꼴, 앞 조각은 대문자로
    시작하는 10낱말 이하, '.'·':'로 끝나거나 뒤가 대문자로 시작, 앞 줄이 문장부호로 끝남(문단 시작), 본문 크기.
    문장 중간에 굵게 쓴 조건 이름('… within the instructed task, / Unified was 49.9 sec faster')은 앞 줄이 쉼표로 끝나 걸리지 않는다."""
    rep = _repeated(pages)
    prev = ""
    for p, rows in enumerate(pages):
        for i, r in enumerate(rows):
            lead = r[7] if len(r) > 7 else None
            if (lead and i not in marks[p] and abs(r[1] - body) <= 0.8 and r[2] not in rep
                    and _bold_variant(lead[1], body_font) and lead[3].startswith(body_font)
                    and lead[0][:1].isupper() and len(lead[0].split()) <= RUNIN_MAX_WORDS
                    and not RUNIN_NOT.match(lead[0])
                    and (lead[0].rstrip().endswith((".", ":")) or lead[2][:1].isupper())
                    and (not prev or SENT_DONE.search(prev)) and _prose_next(rows, i, body, body_font)):
                marks[p][i] = RUNIN_MARK
            if r[2].strip(" .:"):
                prev = r[2]


def _with_paragraphs(lines: list[tuple], body: float = 0.0, marks: dict[int, str] | None = None) -> list[str]:
    """줄 간격이 크거나(문단·제목 앞뒤) 단이 바뀌어 위로 돌아가면 빈 줄을 넣는다."""
    gaps = [b[0] - a[0] for a, b in zip(lines, lines[1:]) if 0 < b[0] - a[0] < 40]
    usual = median(gaps) if gaps else 12.0
    out: list[str] = []
    for i, row in enumerate(lines):
        top, size, text = row[:3]
        if i:
            prev_top, prev_size = lines[i - 1][:2]
            gap = top - prev_top
            if gap < 0 or gap > usual * PARA_GAP_RATIO or abs(size - prev_size) > 0.8:
                out.append("")
        small = body and size < body - SMALL_GAP
        mark = SMALL_MARK if small else (marks or {}).get(i, "")
        if mark == RUNIN_MARK:                 # 문단 첫머리 굵은 제목: 새 문단으로, 제목과 나머지를 나눠 둔다
            if out and out[-1] != "":
                out.append("")
            out.append(RUNIN_MARK + row[7][0] + RUNIN_SEP + row[7][2])
            continue
        out.append(mark + text)
    return out


def _first_page_title(pages) -> str:
    """첫 쪽 위쪽에서 가장 큰 글자 줄. 여러 쪽에 똑같이 찍힌 줄(출판사 표시 'Author's personal copy' 같은
    워터마크·머리글)은 제목이 아니므로 뺀다."""
    if not pages or not pages[0]:
        return ""
    seen: dict[str, int] = {}
    for rows in pages:
        for t in {r[2] for r in rows[:12]}:
            seen[t] = seen.get(t, 0) + 1
    rep = len(pages) >= 2
    rows = [r for r in pages[0][:12] if not (rep and seen[r[2]] >= 2 and seen[r[2]] * 2 >= len(pages))]
    if not rows:
        return ""
    big = max(r[3] for r in rows)
    return " ".join(r[2] for r in rows if r[3] >= big - 0.5)


def extract(data: bytes) -> PdfText:
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:                # 깨진 파일·암호 걸린 파일
        raise PdfError("PDF를 열지 못했어요. 암호가 걸렸거나 손상된 파일일 수 있어요.") from e
    with pdf:
        total = len(pdf.pages)
        pages: list[list[tuple]] = []
        two_col = 0
        for page in pdf.pages[:MAX_PAGES]:
            rows, is_two = _page_lines(page)
            two_col += is_two
            pages.append(rows)
            page.flush_cache()
        title = _first_page_title(pages)
        body = _body_size(pages)
        body_font = _body_font(pages)
        marks = _head_marks(pages, body, body_font)
        _runin_marks(pages, body, body_font, marks)
        lines: list[str] = []
        for rows, page_marks in zip(pages, marks):
            lines += _with_paragraphs(rows, body, page_marks)
            lines.append("")              # 쪽 경계
        meta_title = (pdf.metadata or {}).get("Title") or ""
    if not any(ln.strip(MARKS + " ") for ln in lines):
        raise PdfError("PDF에서 글자를 찾지 못했어요. 스캔한 이미지 PDF는 지원하지 않아요(글자를 선택할 수 있는 PDF만 가능).")
    notes = []
    if total > MAX_PAGES:
        notes.append(f"{total}쪽 중 앞 {MAX_PAGES}쪽만 읽었어요.")
    if isinstance(meta_title, str) and len(meta_title.split()) >= 3 and not re.search(r"\.(?:dvi|tex|pdf)$", meta_title):
        title = meta_title.strip()
    return PdfText(lines=lines, pages=total, read_pages=min(total, MAX_PAGES), title=title.strip(),
                   two_column_pages=two_col, notes=notes)
