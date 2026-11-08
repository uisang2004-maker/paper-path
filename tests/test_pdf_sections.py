"""PDF 글자 읽기(2단 순서)·절 나누기·긴 글 묶음 번역 테스트. 시험용 PDF는 코드로 직접 만든다(외부 파일 없음)."""

import re

import pytest

from src import patterns as P
from src import translate as T
from src import usage
from src.pdf_text import PdfError, extract
from src.sections import heading, split_paper


def make_pdf(pages: list[list[tuple[float, float, str]]], size: float = 10) -> bytes:
    """(x, y, 글자) 목록으로 된 쪽들을 Helvetica로 찍은 최소 PDF(A4 폭 612pt)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for items in pages:
        body = "".join(f"BT /F1 {size} Tf {x} {y} Td ({t.replace('(', '[').replace(')', ']')}) Tj ET\n"
                       for x, y, t in items)
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}endstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def two_column_page() -> list[tuple[float, float, str]]:
    items = [(200, 760, "A Tiny Two Column Paper")]          # 쪽 전체 폭 제목(가운데)
    left = ["Abstract", "", "We propose a small method.", "It reads the left column first.",
            "One more left line.", "Left side ends here."]
    right = ["1 Introduction", "", "The right column comes second.", "It continues the paper text.",
             "One more right line.", "Right side ends here."]
    for k, (l, r) in enumerate(zip(left, right)):
        y = 700 - k * 14
        if l:
            items.append((60, y, l))
        if r:
            items.append((330, y, r))
    return items


def test_two_column_reading_order():
    pdf = extract(make_pdf([two_column_page()]))
    text = "\n".join(pdf.lines)
    assert text.index("left column first") < text.index("Left side ends") < text.index("1 Introduction")
    assert text.index("1 Introduction") < text.index("right column comes second")
    assert pdf.two_column_pages == 1


def test_split_two_column_pdf_into_sections():
    pdf = extract(make_pdf([two_column_page()]))
    paper = split_paper(pdf.lines, pdf.title)
    assert [s.title for s in paper.sections] == ["Abstract", "1 Introduction"]
    assert paper.sections[0].text.startswith("We propose a small method.")
    assert "Tiny Two Column" not in paper.chars.__str__() + paper.sections[0].text   # 앞머리(제목·저자)는 버림


def test_empty_pdf_is_error():
    with pytest.raises(PdfError):
        extract(make_pdf([[]]))
    with pytest.raises(PdfError):
        extract(b"not a pdf")


@pytest.mark.parametrize("line,last,want", [
    ("1 Introduction", 0, ("top", "1 Introduction")),
    ("1. Introduction", 0, ("top", "1 Introduction")),
    ("II. RELATED WORK", 1, ("top", "II Related Work")),
    ("1 I NTRODUCTION", 0, ("top", "1 Introduction")),                # 작은 대문자 글꼴 조각
    ("R EFERENCES", 4, ("end", "REFERENCES")),
    ("5 RNNenc-50", 4, None),                                       # 표 안 모델 이름
    ("3.2 Pre-training BERT", 3, ("sub", "3.2 Pre-training BERT")),
    ("ABSTRACT", 0, ("top", "Abstract")),
    ("References", 5, ("end", "References")),
    ("5 Experiments", 2, None),                                   # 차례가 안 맞음(3이어야 함)
    ("4 We note that in the literature the bidirectional Trans-", 3, None),   # 번호 각주
    ("2 Results of 12 runs 34 45", 1, None),                      # 숫자가 많은 표 줄
    ("1 We propose a method.", 0, None),                          # 마침표로 끝나는 문장
])
def test_heading_rules(line, last, want):
    assert heading(line, last) == want


def test_noise_and_rejoin():
    lines = ["Abstract", "", "We study broken lines that con-", "", "tinue after a column break.", "",
             "Figure 1: A caption that should go.", "", "∗ Equal contribution.", "", "3 Footnote text here.", "",
             "0.1 0.2 0.3 0.4 0.5", "", "Real text ends.", "", "References", "", "[1] A. Author. Title. 2020."]
    paper = split_paper(lines)
    t = paper.sections[0].text
    assert "continue after a column break." in t
    assert "caption" not in t and "Equal" not in t and "Footnote" not in t and "0.3" not in t
    assert "Author" not in t and paper.sections[0].dropped == 4


def test_no_headings_falls_back_to_one_block():
    paper = split_paper(["Just some text without headings.", "More text."])
    assert [s.title for s in paper.sections] == ["본문"] and paper.notes


# ---------------------------------------------------------------- 긴 글 묶음 번역

def test_chunk_ranges_respect_limit():
    sents = ["a" * 40] * 10
    ranges = T.chunk_ranges(sents, 100)
    assert ranges[0] == (0, 2) and ranges[-1][1] == 10
    assert all(sum(len(s) + 1 for s in sents[a:b]) <= 100 for a, b in ranges)
    assert T.chunk_ranges(["x" * 500], 100) == [(0, 1)]           # 한 문장이 더 길어도 그 문장만 한 묶음


def test_translate_chunked_merges_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "CACHE_PATH", tmp_path / "c.json")
    calls = []

    def fake(texts, key, split):
        calls.append(texts)
        return [re.sub(r"<s>", "<s>KO:", t) for t in texts]

    sents = [f"Sentence number {i} is here." for i in range(9)]
    r = T.translate_chunked(sents, 60, api_key="k", caller=fake, use_file_cache=False)
    assert r.ko == [f"KO:{s}" for s in sents] and r.mode == "aligned"
    assert len(calls) == len(T.chunk_ranges(sents, 60)) > 1
    assert len(r.ko_marks) == len(sents) and len(r.protected) == len(sents)


def test_translate_chunked_reports_error():
    def boom(texts, key, split):
        raise T.TranslationError("막힘")

    r = T.translate_chunked(["One sentence."] * 5, 20, api_key="k", caller=boom, use_file_cache=False)
    assert r.ko is None and r.error == "막힘"


def test_engine_limits(tmp_path):
    path = tmp_path / "u.json"
    assert usage.check(3500, 0, path) is not None                         # DeepL: 한 번 3,000자
    assert usage.check(3500, 0, path, engine="claude") is None            # Claude: 4,000자
    assert usage.check(100, 59, path, engine="claude") is None
    assert "번까지" in usage.check(100, 60, path, engine="claude")


# ---------------------------------------------------------------- 앞머리·저자 정보 거르기, 쪽 묶기

from src.pdf_text import SMALL_MARK
from src.sections import Paper, Section, nice_title, paper_pages


def test_ieee_inline_abstract_and_author_footnotes_removed():
    lines = ["Fast Thing: A Title", "Alice Kim, Bob Lee", "",
             "Abstract—We propose a fast detector. It works well.", "",
             "Index Terms—Object Detection, Region Proposal.", "",
             "1 INTRODUCTION", "", "Detection is an important task in vision research.", "",
             "• A. Kim is with University of Somewhere, Seoul.", "E-mail: alice@uni.ac.kr", "",
             "We contact {alice,bob}@lab.org for code and data. " + "This long paragraph keeps going. " * 30, "",
             "R EFERENCES", "", "[1] Someone. A paper. 2020."]
    paper = split_paper(lines)
    assert [s.title for s in paper.sections] == ["Abstract", "1 Introduction"]
    assert paper.sections[0].text == "We propose a fast detector. It works well."
    body = paper.sections[1].text
    assert "University" not in body and "alice@" not in body and "@lab.org" not in body and "Index Terms" not in body
    assert "We contact for code and data" in body                 # 긴 문단은 남기고 이메일만 지움
    assert "Someone" not in body


def test_small_font_paragraphs_dropped_except_all_small_abstract():
    lines = ["ABSTRACT", "", SMALL_MARK + "Our abstract is printed in a small font.", "",
             "1 Introduction", "", "Body text in the normal font continues here.", "",
             SMALL_MARK + "∗Equal contribution. Work done while at a lab.", "",
             SMALL_MARK + "3 We note a footnote that looks like a heading", "", "More body text follows here."]
    paper = split_paper(lines)
    assert paper.sections[0].text == "Our abstract is printed in a small font."
    t = paper.sections[1].text
    assert "Equal" not in t and "footnote" not in t and "More body text" in t
    assert [s.title for s in paper.sections] == ["Abstract", "1 Introduction"]


def test_nice_title_keeps_acronyms_from_body():
    keep = frozenset({"BERT", "R-CNN"})
    assert nice_title("BERT", keep) == "BERT"
    assert nice_title("RELATED WORK", keep) == "Related Work"
    assert nice_title("LEARNING TO ALIGN AND TRANSLATE", keep) == "Learning to Align and Translate"
    assert nice_title("FASTER R-CNN", keep) == "Faster R-CNN"
    assert nice_title("Mixed Case Title", keep) == "Mixed Case Title"


def test_paper_pages_groups_sections():
    secs = [Section(t, text="x.") for t in ["Abstract", "1 Introduction", "2 Method", "5 Discussion",
                                           "6 Conclusion", "Acknowledgments"]]
    pages = paper_pages(Paper("t", secs))
    assert pages == {"초록": [0], "본문": [1, 2], "결론": [3, 4]}
    assert "결론" not in paper_pages(Paper("t", secs[:3]))


def test_plain_len_ignores_tags_so_chunks_fit_limit():
    xml = '<s>We <m c="hedge" i="0">may</m> win &amp; lose.</s>'
    assert T.plain_len(xml) == len("We may win & lose.")
    sents = [f"Sentence {i} has some words in it." for i in range(200)]
    for a, b in T.chunk_ranges(sents, 4000):
        assert T.plain_len(" ".join(f"<s>{s}</s>" for s in sents[a:b])) <= 4000


# ---------------------------------------------------------------- 표 찾기

from src import tables as TB


def make_pdf_with_rules(items, rules, size=10):
    """make_pdf에 가로선(re 연산자로 그린 얇은 사각형)을 더한 PDF."""
    body = "".join(f"BT /F1 {size} Tf {x} {y} Td ({t}) Tj ET\n" for x, y, t in items)
    body += "".join(f"{x0} {y} {x1 - x0} 0.5 re f\n" for x0, x1, y in rules)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(body)} >>\nstream\n{body}endstream",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 3 0 R >> >> >>"]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def test_find_table_by_caption_and_rules():
    items = [(72, 700, "Table 1: Accuracy of two models on a test set."),
             (80, 670, "Model"), (300, 670, "Accuracy"),
             (80, 650, "Base"), (300, 650, "71.2"),
             (80, 635, "Ours"), (300, 635, "74.9"),
             (72, 560, "Table 2. A table without any rules."),
             (72, 400, "Body text says Table 1 shows our gains over the base model in this short paper.")]
    rules = [(72, 400, 684), (72, 400, 662), (72, 400, 628)]
    ts = TB.find_tables(make_pdf_with_rules(items, rules))
    assert [t.num for t in ts] == [1, 2]
    assert ts[0].caption.startswith("Table 1: Accuracy") and not ts[0].whole_page and ts[0].png[:4] == b"\x89PNG"
    assert ts[1].whole_page                       # 선이 없으면 쪽 전체 이미지로


def test_table_mentions():
    assert TB.mentioned("As Table 3 shows, and Tables 2 and 4, see Tab. 5; also Table 3 again.") == [3, 2, 4, 5]
    assert TB.mentioned("A stable table-like result.") == []


def test_first_mentions_pick_sentence_for_cards():
    from src.extract import analyze
    a = analyze("We show results in Table 2. Fig. 3 plots the loss. Table 2 again and Figures 1 and 4 too.")
    first = TB.first_mentions(a.text, a.sentences)
    assert first[("table", 2)] == 0 and first[("figure", 3)] == 1
    assert first[("figure", 1)] == 2 and first[("figure", 4)] == 2


def test_find_figure_above_caption():
    items = [(72, 720, "Body text before the figure is a long sentence with many words in it here."),
             (100, 640, "Encoder"), (300, 640, "Decoder"),
             (72, 560, "Figure 1: The model architecture with two blocks."),
             (72, 520, "Body text after the figure is another long sentence with many words in it.")]
    rules = [(90, 400, 660), (90, 400, 600)]          # 그림 요소(사각형 선) 두 개
    figs = TB.find_figures(make_pdf_with_rules(items, rules))
    assert [f.num for f in figs] == [1]
    f = figs[0]
    assert f.kind == "figure" and not f.whole_page and f.mime == "image/jpeg" and f.png[:2] == b"\xff\xd8"
    assert f.caption.startswith("Figure 1: The model") and f.width > 0 and f.height > 0


def make_font_pdf(pages: list[list[tuple]]) -> bytes:
    """(x, y, 글자, 글꼴, 크기) 목록: 글꼴 'R'=본문(Times-Roman), 'B'=굵은 제목(Helvetica-Bold),
    'H'=이름에 Bold가 없는 제목 글꼴(Helvetica, 이름이 가려진 출판사 글꼴 대신)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for items in pages:
        body = "".join(f"BT /F{'RBH'.index(f) + 1} {s} Tf {x} {y} Td ({t}) Tj ET\n" for x, y, t, f, s in items)
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}endstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def encyclopedia_pages() -> list[list[tuple]]:
    """번호 없는 굵은 절 제목(11pt)·소제목(10pt), 쪽마다 찍힌 워터마크(20pt)가 있는 백과사전식 글."""
    body = ["Space is the key topic of geography and it is used in many ways.",
            "It has a long history in philosophy and in the sciences of nature.",
            "Writers have used it as a container for things and as a relation."]
    def block(y, head, kind):
        items = [(60, y, head, "B", 11 if kind == "top" else 10)]
        items += [(60, y - 20 - 13 * k, t, "R", 10) for k, t in enumerate(body)]
        return items
    p1 = [(150, 770, "Author's personal copy", "B", 20), (60, 730, "Space I", "B", 14)]
    p1 += block(690, "Introduction", "top") + block(620, "Space in the Philosophical Tradition", "top")
    p1 += block(550, "Space and Time", "sub")
    p2 = [(150, 770, "Author's personal copy", "B", 20)]
    p2 += block(690, "Ways of Thinking Space", "top") + block(620, "Conclusion", "top")
    p2 += [(60, 550, "Further Reading", "B", 11), (60, 530, "Harvey D (1969) Explanation in Geography.", "R", 10)]
    return [p1, p2]


def test_unnumbered_bold_headings_split_sections():
    pdf = extract(make_font_pdf(encyclopedia_pages()))
    assert pdf.title == "Space I"                                     # 쪽마다 찍힌 워터마크는 제목이 아니다
    paper = split_paper(pdf.lines, pdf.title)
    assert [s.title for s in paper.sections] == ["Introduction", "Space in the Philosophical Tradition",
                                                 "Ways of Thinking Space", "Conclusion"]
    assert paper.sections[1].subheads == ["Space and Time"]           # 본문 크기 굵은 줄은 소제목
    assert "Harvey" not in " ".join(s.text for s in paper.sections)   # Further Reading부터는 참고문헌


def test_bold_marks_ignored_when_numbered_sections_exist():
    from src.pdf_text import HEAD_MARK
    say = "We study a small method for reading papers in this section."
    lines = ["Abstract", "", say, "", "1 Introduction", "", say, "",
             HEAD_MARK + "Main Results", "", say, "", "2 Method", "", say]
    paper = split_paper(lines, "T")
    assert [s.title for s in paper.sections] == ["Abstract", "1 Introduction", "2 Method"]


@pytest.mark.parametrize("line,ok", [
    ("Space in the Philosophical Tradition", True), ("Absolute, Relative, and Relational Space", True),
    ("Smooth versus Striated", True), ("Development of the protocol", True), ("We propose a method.", False),
    ("Digestion procedure Timing 5 8 h", False)])
def test_font_head_regex(line, ok):
    assert bool(heading(line, 0, font="top")) == ok
    assert heading(line, 0) is None or line == "Introduction"         # 글꼴 단서가 없으면 받지 않는다


def test_same_size_bold_heads_use_common_section_names():
    """굵은 제목이 모두 같은 크기(Bradley 2021형): 흔한 절 이름은 절, 나머지는 소제목. 첫 절 앞 굵은 줄(부제)은 버린다."""
    from src.pdf_text import SUB_MARK as S
    say = "We watched one child play with a puzzle for twenty minutes at home."
    lines = [S + "Watching Children at Home", "", S + "Abstract:", say, "", S + "Introduction", "", say, "",
             S + "Methods", "", S + "Setting for Child One", "", say, "", S + "Observation of Child One", "", say, "",
             S + "Results", "", say, "", S + "Discussion: What the Puzzle Shows", "", say]
    paper = split_paper(lines, "Naturalistic Child Observation")
    assert [s.title for s in paper.sections] == ["Abstract", "Introduction", "Methods", "Results",
                                                 "Discussion: What the Puzzle Shows"]
    assert paper.sections[2].subheads == ["Setting for Child One", "Observation of Child One"]


def test_page_break_sentence_tail_is_kept():
    say = "Complete value neutrality is impossible yet remains an ideal that allows all perspectives"
    lines = ["Abstract", "", say, "", "\x1f4", "", "to be heard.", "", "1 Introduction", "", say + "."]
    paper = split_paper(lines, "T")
    assert paper.sections[0].text.endswith("allows all perspectives to be heard.")


@pytest.mark.parametrize("line", ["Analysis: A Contemporary Focus on Ibram X. Kendi",
                                  "Literature Review: Understanding Competing Definitions"])
def test_font_head_accepts_colon_and_initials(line):
    assert heading(line, 0, font="top") == ("top", line)


def test_bold_cells_side_by_side_are_table_header_not_subheads():
    """같은 높이에 굵은 칸 두 개가 나란히 있으면 표 머리글(Bradley 관찰표): 소제목이 되지 않는다."""
    say = ["We watched one child play with a puzzle at home today.",
           "The child talked about the pieces and their colors often."]
    items = []
    y = 740
    for head in ("Introduction", "Methods", "Setting for Child One"):
        items.append((60, y, head, "B", 10))
        items += [(60, y - 16 - 13 * k, t, "R", 10) for k, t in enumerate(say)]
        y -= 60
    items += [(60, y, "Child Activity", "B", 10), (330, y, "Others Involved", "B", 10)]
    items += [(60, y - 16, "puzzle on the floor.", "R", 10), (330, y - 16, "mother at the table.", "R", 10)]
    items.append((60, y - 60, "Results", "B", 10))
    items += [(60, y - 76 - 13 * k, t, "R", 10) for k, t in enumerate(say)]
    pdf = extract(make_font_pdf([items]))
    paper = split_paper(pdf.lines, pdf.title)
    methods = next(s for s in paper.sections if s.title == "Methods")
    assert methods.subheads == ["Setting for Child One"]
    assert "Child Activity" in methods.text                            # 머리글 글자는 표 내용으로 남는다


def test_outdented_heads_in_plain_font_are_sections():
    """Nature Protocols형: 제목 글꼴 이름에 Bold가 없고 크기도 같지만, 본문보다 왼쪽으로 내어 쓴 제목은 절,
    본문 줄에 맞춘 제목은 소제목. 쪽마다 찍힌 머리글(PROTOCOL)은 제목이 아니다."""
    say = ["We digest the food sample in three phases with enzymes and salts.",
           "Each phase runs for two hours at body temperature with mixing."]
    plan = [("Introduction", 50), ("Development of the protocol", 160), ("Overview of the procedure", 160),
            ("Materials", 50), ("Reagents", 160), ("Procedure", 50)]
    pages = []
    for chunk in (plan[:3], plan[3:]):
        items = [(450, 770, "PROTOCOL", "H", 14)]
        y = 730
        for head, x in chunk:
            items.append((x, y, head, "H", 10))
            items += [(160, y - 16 - 13 * k, t, "R", 10) for k, t in enumerate(say)]
            y -= 70
        pages.append(items)
    pdf = extract(make_font_pdf(pages))
    paper = split_paper(pdf.lines, pdf.title)
    assert [s.title for s in paper.sections] == ["Introduction", "Materials", "Procedure"]
    assert paper.sections[0].subheads == ["Development of the protocol", "Overview of the procedure"]
    assert paper.sections[1].subheads == ["Reagents"]


def test_unlabeled_abstract_before_first_section():
    """'Abstract' 표시 없는 초록(Nature Protocols): 저자 줄 다음, 첫 절 바로 앞의 긴 글을 초록으로."""
    from src.pdf_text import HEAD_MARK as H
    from src.pdf_text import SMALL_MARK as S
    lines = [H + "A Static Digestion Method", "", H + "Ann Lee , Bo Kim and Cy Park", "", S + "1 Some University, Cork",
             "", "Developing an understanding of food digestion matters for nutrition.", "",
             "Many simulations use different conditions and this impedes comparison of results.", "",
             "The standardized protocol presented here is based on an international consensus.", "",
             "It can be completed in about seven days with standard laboratory equipment.", "",
             H + "Introduction", "", "The worldwide prevalence of diet-related diseases has been increasing."]
    paper = split_paper(lines, "A Static Digestion Method")
    assert [s.title for s in paper.sections] == ["Abstract", "Introduction"]
    assert paper.sections[0].text.startswith("Developing an understanding")
    assert "Ann Lee" not in paper.sections[0].text and any("초록으로" in n for n in paper.notes)


def test_short_front_text_is_not_abstract():
    lines = ["Space I", "", "S. Elden, Durham University", "", "Introduction", "",
             "Space is, in many ways, the key topic of geography and it is hard to define."]
    assert [s.title for s in split_paper(lines, "Space I").sections] == ["Introduction"]


def test_head_with_timing_note():
    from src.pdf_text import HEAD_MARK as H
    from src.pdf_text import SUB_MARK as S
    say = "Mix the sample with the simulated fluid and incubate it at body temperature."
    lines = [H + "Introduction", "", say, "", H + "Procedure", "", say, "",
             S + "Digestion procedure ● Timing 5–8 h", "", say]
    paper = split_paper(lines, "T")
    proc = next(s for s in paper.sections if s.title == "Procedure")
    assert proc.subheads == ["Digestion procedure · Timing 5–8 h"]


def test_numbered_steps_kept_but_footnotes_dropped():
    step = ["1 Perform all enzyme and bile assays before the digestion experiments start in the lab.",
            "Prepare the stock solutions of simulated fluids and store them at minus twenty degrees.",
            "Check the pH of each solution again on the day of use and adjust it with acid or base."]
    lines = ["Abstract", "", "We describe a digestion protocol for many foods in this short paper.", "",
             "1 Introduction", "", "The method is simple and uses standard equipment in most labs.", "",
             *step, "", "4 We note that the enzyme was bought from one company."]
    text = split_paper(lines, "T").sections[1].text
    assert "Perform all enzyme" in text and "We note that" not in text


def test_run_in_bold_paragraph_heading_becomes_subhead():
    """ACM 양식 문단 첫머리 굵은 제목('Separated. We considered …')은 소제목으로 떼고 본문 문장에서 뺀다.
    문장 중간 줄 첫머리에 굵게 쓴 낱말(앞 줄이 쉼표로 끝남)은 제목이 아니다."""
    say = ["We study how people move between two workspaces during data analysis.",
           "The first condition places the notebook and the data in separate rooms."]
    items = [(60, 740, "1 Introduction", "B", 12)]
    items += [(60, 720 - 13 * k, t, "R", 10) for k, t in enumerate(say)]
    items += [(60, 680, "Separated.", "B", 10), (125, 680, "We considered several organizational strategies here.", "R", 10)]
    items += [(60, 667, "Clustering groups related items based on proximity or other shared attributes,", "R", 10)]
    items += [(60, 654, "Unified", "B", 10), (110, 654, "was faster than the other condition in every task we gave.", "R", 10)]
    items += [(60, 641 - 13 * k, t, "R", 10) for k, t in enumerate(say)]
    pdf = extract(make_font_pdf([items]))
    sec = split_paper(pdf.lines, pdf.title).sections[0]
    assert sec.subheads == ["Separated"]
    assert "Separated." not in sec.text and "We considered several organizational strategies" in sec.text
    assert "Unified was faster" in sec.text                                  # 문장 중간 굵은 낱말은 그대로 본문


@pytest.mark.parametrize("line,last,want", [
    ("6.2 Qualitative Feedback.", 6, ("sub", "6.2 Qualitative Feedback")),
    ("3 Results are shown here.", 2, None)])
def test_numbered_subhead_may_end_with_period(line, last, want):
    assert heading(line, last) == want
