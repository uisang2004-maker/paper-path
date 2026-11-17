"""논문 PDF의 표 찾기: 표 설명(캡션)은 정규식으로 찾고, 표 영역은 가로선 위치로 정해 이미지로 잘라 보여 준다.

- 행·열 구조를 정확히 뽑는 것은 AI 논문 표(세로선 없이 가로선만 쓰는 형식)에서 불안정하다(7편 실측).
  그래서 원본 그대로인 이미지를 기본으로 보여 주고, 칸이 깔끔하게 나뉜 표만 '표로 보기'를 함께 준다.
1. 줄마다(2단이면 단마다) ``Table 3:``·``Table 3.``으로 시작하는지 정규식(patterns.TABLE_CAPTION)으로 본다.
   같은 번호가 여러 번 나오면(본문의 ``Table 6. In this table ...``) 글자가 작은 쪽, 가로선이 가까운 쪽을 캡션으로 고른다.
2. 캡션과 같은 단에 있는 가로선(표의 위·가운데·아래 선) 중 캡션 바로 위나 아래(40pt 이내)에서 시작하는 선 묶음을 표로 본다.
   선과 선 사이가 너무 멀거나 사이에 다른 캡션·긴 문장이 있으면 묶음을 끊는다.
3. 선 묶음을 못 찾으면 그 쪽 전체 이미지를 보여 준다(잘못 자르는 것보다 낫다).
"""

import io
import re
from dataclasses import dataclass, field
from statistics import median

from src import patterns as P
from src.pdf_text import MAX_PAGES, _group_lines, _split_at_gutter, _text

NEAR_CAPTION = 40.0       # 캡션과 표 가장자리(첫 가로선) 사이 최대 거리(pt)
MAX_RULE_GAP = 220.0      # 같은 표로 묶을 가로선 사이 최대 거리(pt). 사이에 본문 크기 긴 문장·다른 캡션이 있으면 끊는다
MIN_RULE_WIDTH = 40.0     # 이보다 짧은 가로선은 표 선으로 보지 않는다(밑줄·기호)
PAD = 4.0
RESOLUTION = 220          # 잘라 낸 표 이미지 해상도(dpi). 화면에는 VIEW_DPI 기준 크기로 보여 고해상도 화면에서도 선명하다
PAGE_RESOLUTION = 130     # 영역을 못 찾아 쪽 전체를 보여 줄 때
VIEW_DPI = 144            # 화면 표시 크기: 인쇄 크기(96dpi 기준)의 1.5배. 이미지 픽셀이 이보다 많아 선명하게 줄여 보인다
MAX_TABLES = 30


@dataclass
class TableInfo:
    num: int
    caption: str
    page: int                                   # 1부터
    png: bytes = b""                            # 이미지(표는 PNG, 그림은 JPEG)
    whole_page: bool = False                    # 영역을 못 찾아 쪽 전체를 보여 주는지
    grid: list[list[str]] = field(default_factory=list)   # 칸이 깔끔하게 나뉜 표만
    kind: str = "table"                         # table / figure
    width: int = 0                              # 이미지 픽셀 크기(화면에서 비율 맞추기)
    height: int = 0
    mime: str = "image/png"
    dpi: int = RESOLUTION                       # 이미지를 만든 해상도(화면 표시 크기 계산)

    @property
    def view_width(self) -> int:
        """화면에 보여 줄 기본 너비(px): 인쇄 크기의 1.5배. 이미지는 이보다 픽셀이 많아 줄여 보이므로 흐리지 않다."""
        return round((self.width or 600) * VIEW_DPI / (self.dpi or RESOLUTION))

    @property
    def label(self) -> str:
        return f"{'Table' if self.kind == 'table' else 'Figure'} {self.num}"


@dataclass
class _Seg:
    top: float
    bottom: float
    x0: float
    x1: float
    size: float
    text: str


def _split_captions(seg: list[dict]) -> list[list[dict]]:
    """나란히 놓인 두 표의 캡션이 한 줄로 읽히면(``Table 1: ... Table 2: ...``) 두 번째 ``Table`` 앞에서 나눈다."""
    cuts = [i for i in range(1, len(seg) - 1)
            if seg[i]["text"] in ("Table", "TABLE") and re.match(r"\d{1,2}[:.]", seg[i + 1]["text"])]
    out, start = [], 0
    for i in cuts:
        out.append(seg[start:i])
        start = i
    return out + [seg[start:]]


def _segments(page) -> list[_Seg]:
    """쪽의 줄 조각(2단이면 단별로 나눈 조각)과 위치."""
    words = [w for w in page.extract_words(x_tolerance=1.2, extra_attrs=["size", "upright"]) if w["upright"]]
    mid = float(page.width) / 2
    out = []
    for row in _group_lines(words):
        parts = _split_at_gutter(row, mid)
        for part in ([row] if parts is None else [p for p in parts if p]):
            for seg in _split_captions(part):
                out.append(_Seg(min(w["top"] for w in seg), max(w["bottom"] for w in seg), seg[0]["x0"], seg[-1]["x1"],
                                median(w["size"] for w in seg), _text(seg)))
    return sorted(out, key=lambda s: (s.top, s.x0))


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _is_prose(s: _Seg, body: float) -> bool:
    """본문 크기 글자의 긴 문장 줄(표 안 글자는 보통 본문보다 작다)."""
    words = s.text.split()
    return (len(words) >= 10 and s.size >= body - 0.3
            and len(P.PDF_WORDLIKE.findall(s.text)) / len(words) >= 0.8)


def _caption_block(cap: _Seg, segs: list[_Seg]) -> tuple[str, _Seg]:
    """캡션 첫 줄에 바로 이어지는(같은 단, 줄 간격 좁음) 줄을 붙인다. (캡션 글, 캡션 마지막 줄)."""
    lines, last = [cap.text], cap
    for s in segs:
        if s.top <= last.top or _overlap(s.x0, s.x1, cap.x0, cap.x1) < 0.5 * (s.x1 - s.x0):
            continue
        if s.top - last.bottom > 4.5 or abs(s.size - cap.size) > 0.6:
            break
        lines.append(s.text)
        last = s
    return P.DEHYPHEN_SPACE.sub("", " ".join(lines)), last


def _table_box(page, cap: _Seg, last: _Seg, segs: list[_Seg], body: float) -> tuple[float, float, float, float] | None:
    """캡션 옆의 가로선 묶음 → (x0, top, x1, bottom). 못 찾으면 None. last는 여러 줄 캡션의 마지막 줄."""
    rules = sorted({(round(e["top"], 1), round(e["x0"], 1), round(e["x1"], 1)) for e in page.edges
                    if e["orientation"] == "h" and e["x1"] - e["x0"] >= MIN_RULE_WIDTH
                    and _overlap(e["x0"], e["x1"], cap.x0 - 5, cap.x1 + 5) >= 0.6 * (e["x1"] - e["x0"])})
    if not rules:
        return None
    above = [r for r in rules if r[0] < cap.top]
    below = [r for r in rules if r[0] > last.bottom]
    gap_up = cap.top - above[-1][0] if above else 1e9
    gap_down = below[0][0] - last.bottom if below else 1e9
    if min(gap_up, gap_down) > NEAR_CAPTION:
        return None
    # 캡션에서 먼 쪽으로 선을 하나씩 이어 붙인다
    side = list(reversed(above)) if gap_up < gap_down else below
    others = [s for s in segs if s is not cap and (P.TABLE_CAPTION.match(s.text) or P.FIGURE_CAPTION.match(s.text))]
    cluster = [side[0]]
    for r in side[1:]:
        lo, hi = sorted((cluster[-1][0], r[0]))
        if hi - lo > MAX_RULE_GAP:
            break
        between = [s for s in segs if lo < s.top < hi and _overlap(s.x0, s.x1, cap.x0, cap.x1) > 0]
        if any(s in others or _is_prose(s, body) for s in between):
            break
        cluster.append(r)
    if len(cluster) < 2:
        return None
    top, bottom = min(r[0] for r in cluster), max(r[0] for r in cluster)
    x0, x1 = min(r[1] for r in cluster), max(r[2] for r in cluster)
    # 선 밖에 붙은 표 줄(위 선이 없는 머리글, 아래 선 뒤의 마지막 행)도 넣는다: 12pt 안에 붙은 짧은 줄
    table_like = [s for s in segs if s is not cap and s not in others and not _is_prose(s, body)
                  and _overlap(s.x0, s.x1, x0, x1) > 0.5 * (s.x1 - s.x0) and not (cap.top <= s.top <= last.bottom)]
    grown = True
    while grown:
        grown = False
        for s in table_like:
            if top - 12 <= s.bottom <= top and s.top < top:
                top, grown = s.top, True
            elif bottom <= s.top <= bottom + 12 and s.bottom > bottom:
                bottom, grown = s.bottom, True
    return (max(0, x0 - PAD), max(0, top - PAD), min(float(page.width), x1 + PAD), min(float(page.height), bottom + PAD))


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _info(num, caption, pn, img, **kw) -> TableInfo:
    """이미지와 크기를 담는다. 그림은 사진이 섞여 있어 JPEG로 줄인다."""
    if kw.get("kind") == "figure":
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
        data, mime = buf.getvalue(), "image/jpeg"
    else:
        data, mime = _png(img), "image/png"
    return TableInfo(num, caption, pn + 1, data, width=img.width, height=img.height, mime=mime, **kw)


def _grid(region) -> list[list[str]]:
    """칸이 깔끔하게 나뉘면 행 목록, 아니면 빈 목록(이미지만 보여 준다)."""
    try:
        rows = region.extract_table({"vertical_strategy": "text", "horizontal_strategy": "text"}) or []
    except Exception:
        return []
    rows = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return []
    filled = sum(1 for r in rows for c in r if c) / sum(len(r) for r in rows)
    return rows if filled >= 0.6 else []


def find_tables(data: bytes) -> list[TableInfo]:
    import pdfplumber

    found: dict[int, tuple] = {}          # 번호 → (점수, TableInfo 재료)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages[:MAX_PAGES]
        body = median([s.size for p in pages[:3] for s in _segments(p)] or [10.0])
        for pn, page in enumerate(pages):
            segs = _segments(page)
            for cap in segs:
                m = P.TABLE_CAPTION.match(cap.text)
                if not m:
                    continue
                caption, last = _caption_block(cap, segs)
                box = _table_box(page, cap, last, segs, body)
                # 점수: 표 영역을 찾았는가 > 본문보다 작은 글자인가 > 먼저 나왔는가
                score = (box is not None, cap.size < body - 0.3)
                num = int(m.group("num"))
                if num not in found or score > found[num][0]:
                    found[num] = (score, pn, cap, box, caption)
            page.flush_cache()

        out = []
        for num in sorted(found)[:MAX_TABLES]:
            _, pn, cap, box, caption = found[num]
            page = pdf.pages[pn]
            if box:
                region = page.crop(box)
                info = _info(num, caption, pn, region.to_image(resolution=RESOLUTION).original, grid=_grid(region),
                             dpi=RESOLUTION)
            else:
                info = _info(num, caption, pn, page.to_image(resolution=PAGE_RESOLUTION).original, whole_page=True,
                             dpi=PAGE_RESOLUTION)
            out.append(info)
            page.flush_cache()
    return out


def mentioned(text: str, pattern: re.Pattern = P.TABLE_MENTION) -> list[int]:
    """글에서 언급한 번호(처음 나온 순서): ``Table 3``, ``Tables 2 and 4``, ``Tab. 5``. 그림은 FIGURE_MENTION."""
    nums: list[int] = []
    for m in pattern.finditer(text):
        for n in re.findall(r"\d+", m.group(0)):
            if int(n) not in nums:
                nums.append(int(n))
    return nums


def first_mentions(text: str, sentences) -> dict[tuple[str, int], int]:
    """(table|figure, 번호) → 처음 언급한 문장 번호. 표·그림을 그 문장 바로 아래에 끼우는 자리."""
    out: dict[tuple[str, int], int] = {}
    for kind, pat in (("table", P.TABLE_MENTION), ("figure", P.FIGURE_MENTION)):
        for m in pat.finditer(text):
            idx = next((s.idx for s in sentences if s.start <= m.start() < s.end), None)
            if idx is None:
                continue
            for n in re.findall(r"\d+", m.group(0)):
                out.setdefault((kind, int(n)), idx)
    return out


# ---------------------------------------------------------------- 그림(Figure)

FIG_RESOLUTION = 200      # 그림 해상도(dpi): 110이면 막대그래프 숫자가 뭉개졌다
FIG_PAGE_RESOLUTION = 120
MIN_FIG_HEIGHT = 30.0


def _figure_box(page, cap: _Seg, segs: list[_Seg], body: float) -> tuple[float, float, float, float] | None:
    """캡션 위쪽의 그림 영역. AI 논문 그림은 대부분 캡션이 그림 아래에 있다.
    가로: 캡션이 한 단 안에 있으면 그 단, 아니면 쪽 글 폭. 세로: 캡션 위로 가장 가까운 본문 문장(또는 다른 캡션)
    아래부터 캡션 위까지. 그 사이에 그림 요소(이미지·선·도형)가 있으면 그 범위로 좁힌다."""
    mid = float(page.width) / 2
    left = min((s.x0 for s in segs), default=36.0)
    right = max((s.x1 for s in segs), default=float(page.width) - 36)
    if cap.x1 <= mid + 1 and right > mid + 20:
        x0, x1 = left, mid - 2
    elif cap.x0 >= mid - 1 and left < mid - 20:
        x0, x1 = mid + 2, right
    else:
        x0, x1 = left, right
    blockers = [s for s in segs if s.bottom <= cap.top + 1 and _overlap(s.x0, s.x1, x0, x1) > 0
                and (_is_prose(s, body) or P.TABLE_CAPTION.match(s.text) or P.FIGURE_CAPTION.match(s.text))]
    # 위에 막는 글이 없으면(쪽 맨 위 그림) 쪽 위쪽 여백부터
    top = max((s.bottom for s in blockers), default=min([s.top for s in segs if s.bottom <= cap.top] + [40.0]))
    bottom = cap.top
    if bottom - top < MIN_FIG_HEIGHT:
        return None
    objs = [o for o in page.images + page.rects + page.curves + page.lines
            if o["top"] >= top - 1 and o["bottom"] <= bottom + 1 and _overlap(o["x0"], o["x1"], x0, x1) > 0]
    if objs:
        gtop = min(o["top"] for o in objs)
        labels = [s for s in segs if gtop - 14 <= s.top and s.bottom <= bottom and _overlap(s.x0, s.x1, x0, x1) > 0
                  and not _is_prose(s, body)]      # 그림 안 글자(축 이름·범례)도 넣는다
        top = max(top, min([gtop] + [s.top for s in labels]) - PAD)
        x0 = max(x0, min([o["x0"] for o in objs] + [s.x0 for s in labels]) - PAD)
        x1 = min(x1, max([o["x1"] for o in objs] + [s.x1 for s in labels]) + PAD)
    return (max(0.0, x0), max(0.0, top), min(float(page.width), x1), min(float(page.height), bottom - 1))


def find_figures(data: bytes) -> list[TableInfo]:
    """그림 캡션(정규식 FIGURE_CAPTION_NUM)을 찾고 그 위의 그림 영역을 이미지로 자른다. 못 찾으면 쪽 전체."""
    import pdfplumber

    found: dict[int, tuple] = {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages[:MAX_PAGES]
        body = median([s.size for p in pages[:3] for s in _segments(p)] or [10.0])
        for pn, page in enumerate(pages):
            segs = _segments(page)
            for cap in segs:
                m = P.FIGURE_CAPTION_NUM.match(cap.text)
                if not m:
                    continue
                caption, _ = _caption_block(cap, segs)
                box = _figure_box(page, cap, segs, body)
                score = (box is not None, cap.size < body - 0.3)
                num = int(m.group("num"))
                if num not in found or score > found[num][0]:
                    found[num] = (score, pn, box, caption)
            page.flush_cache()
        out = []
        for num in sorted(found)[:MAX_TABLES]:
            _, pn, box, caption = found[num]
            page = pdf.pages[pn]
            if box:
                img = page.crop(box).to_image(resolution=FIG_RESOLUTION).original
                out.append(_info(num, caption, pn, img, kind="figure", dpi=FIG_RESOLUTION))
            else:
                out.append(_info(num, caption, pn, page.to_image(resolution=FIG_PAGE_RESOLUTION).original, kind="figure", dpi=FIG_PAGE_RESOLUTION,
                                 whole_page=True))
            page.flush_cache()
    return out
