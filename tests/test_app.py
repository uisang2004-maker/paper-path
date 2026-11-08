"""Streamlit 화면 상태 테스트(AppTest). 네트워크를 쓰지 않는다."""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def blank():
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


@pytest.fixture
def at(blank):
    """예시 문단을 불러온 화면."""
    blank.button(key="demo_btn").click().run()
    assert not blank.exception, blank.exception
    return blank


def md_text(at):
    return "\n".join(m.value for m in at.markdown)


def test_start_screen_hides_results_until_input(blank):
    text = "\n".join(m.value for m in blank.markdown if not m.value.lstrip().startswith("<style"))
    assert "끌어다 놓으세요" in text and "논문 넣기" in text
    assert 'class="pp-flow"' not in text and "pp-reader-title" not in text and "먼저 읽을 3줄 뼈대" not in text
    assert "AI-Powered Translation" in text and 'class="pp-hero"' in text        # 첫 화면 히어로
    assert blank.session_state["input_mode"] == "논문 PDF"
    assert not any(b.label == "번역하기" for b in blank.button)


def test_first_screen_shows_demo_skeleton_and_flag(at):
    text = md_text(at)
    assert "실제 논문 아님" in text
    assert "we introduce" in text and "신호 문구로 찾음" in text
    chips = mark_chips(at).options
    assert any("약어 1" in o for o in chips) and any("참조 1" in o for o in chips)


def mark_chips(at):
    return next(g for g in at.get("button_group") if g.key == "mark_pills")


def test_chip_turns_highlight_off_and_on(at):
    """02 칩을 누르면 그 형광펜만 꺼지고, 칩은 흐리게 남아 다시 켤 수 있다."""
    assert 'class="pp-m hedge"' in md_text(at)
    mark_chips(at).unselect("hedge").run()
    assert not at.exception
    assert "HEDGE" not in at.session_state["active_rules"] and 'class="pp-m hedge"' not in md_text(at)
    assert any("추측·가능성 2" in o for o in mark_chips(at).options)    # 끈 칩도 개수와 함께 남는다
    mark_chips(at).select("hedge").run()
    assert "HEDGE" in at.session_state["active_rules"] and 'class="pp-m hedge"' in md_text(at)


def test_translation_not_called_on_load(at, monkeypatch):
    # 번역 패널에는 '번역하기' 버튼만 있고 결과는 없다
    assert any(b.label == "번역하기" for b in at.button)
    assert at.session_state["translations"] == {}


def test_rule_toggle_keeps_input_and_changes_marks(at):
    at.session_state["input_text"] = "The method may be applied to images. These results suggest gains."
    at.toggle(key="show_steps").set_value(True).run()
    assert not at.exception
    before = md_text(at)
    assert 'class="pp-m hedge"' in before
    at.checkbox(key="rule_HEDGE").uncheck().run()
    after = md_text(at)
    assert not at.exception
    assert 'class="pp-m hedge"' not in after
    assert at.session_state["input_text"].startswith("The method may be applied")
    assert "HEDGE" not in at.session_state["active_rules"]


def test_rules_survive_hiding_steps_panel(at):
    at.toggle(key="show_steps").set_value(True).run()
    at.checkbox(key="rule_REFERENCE").uncheck().run()
    at.toggle(key="show_steps").set_value(False).run()
    assert "REFERENCE" not in at.session_state["active_rules"]
    assert "reference" not in mark_chips(at).value              # 끈 표시는 칩이 꺼진 상태로 남는다


def test_translation_result_survives_rule_toggle(at):
    import hashlib

    from src.extract import analyze
    from src.translate import TranslationResult

    a = analyze(at.session_state["input_text"])
    key = (hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16], True)
    fake = TranslationResult(ko=[f"번역 {i}" for i in range(len(a.sentences))], mode="aligned", protected=[[]] * len(a.sentences))
    at.session_state["translations"] = {key: fake}
    at.run()
    assert "번역 0" in md_text(at)
    at.toggle(key="show_steps").set_value(True).run()
    at.checkbox(key="rule_ABBR").uncheck().run()
    assert not at.exception
    assert "번역 0" in md_text(at)
    assert at.session_state["translations"][key].ko[0] == "번역 0"


def test_paste_validation_error(blank):
    blank.session_state["input_mode"] = "텍스트 붙여 넣기"
    blank.run()
    blank.text_area(key="paste_text").set_value("한국어 문장만 있어요.").run()
    blank.button(key="load_btn").click().run()
    assert any("영어 텍스트가 아닌" in e.value for e in blank.error)
    assert blank.session_state["show_input"] is True        # 실패하면 입력 칸이 그대로 열려 있다


def test_input_panel_closes_after_loading_and_reopens(at):
    keys = lambda: {b.key for b in at.button}
    assert "reinput_btn" in keys() and "load_btn" not in keys() and "demo_btn" not in keys()
    assert "pp-reader-title" in md_text(at)
    at.button(key="reinput_btn").click().run()
    assert "demo_btn" in keys() and "back_btn" in keys()
    assert not any("pp-reader-title" in m.value for m in at.markdown if not m.value.lstrip().startswith("<style"))
    at.button(key="back_btn").click().run()
    assert "reinput_btn" in keys() and at.session_state["input_text"]


def test_pages_render(at):
    for page in ("규칙 탐색", "검증 결과", "논문 읽기"):
        at.radio(key="page").set_value(page).run()
        assert not at.exception, page
    at.radio(key="page").set_value("검증 결과").run()
    assert "측정" in md_text(at)


def test_korean_highlights_follow_rules(at):
    import hashlib

    from src.extract import analyze
    from src.translate import TranslationResult

    a = analyze(at.session_state["input_text"])
    key = (hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16], True)
    ko = ["가"] * (len(a.sentences) - 1) + ["도움이 될 수 있음을 시사한다[4]."]
    km = [[] for _ in ko]
    km[-1] = [(11, 15, "hedge", "suggest"), (15, 18, "reference", "[4]")]
    at.session_state["translations"] = {key: TranslationResult(ko=ko, mode="aligned", ko_marks=km,
                                                               protected=[[]] * len(ko))}
    at.run()
    text = md_text(at)
    assert "영어 &#x27;suggest&#x27;에 대응" in text or "영어 'suggest'에 대응" in text
    at.toggle(key="show_steps").set_value(True).run()
    at.checkbox(key="rule_HEDGE").uncheck().run()
    text = md_text(at)
    assert "suggest&#x27;에 대응" not in text and "'suggest'에 대응" not in text
    assert "[4]&#x27;에 대응" in text or "'[4]'에 대응" in text


def test_cards_show_korean_and_underline(at):
    import hashlib

    from src.extract import analyze
    from src.translate import TranslationResult

    a = analyze(at.session_state["input_text"])
    key = (hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16], True)
    ko = [f"번역문 {i}입니다." for i in range(len(a.sentences))]
    uls = [(0, 3, "problem"), (0, 3, "method"), (0, 3, "result"), None]
    at.session_state["translations"] = {key: TranslationResult(ko=ko, mode="aligned", ko_underlines=uls,
                                                               ko_marks=[[] for _ in ko], protected=[[]] * len(ko))}
    at.run()
    text = md_text(at)
    assert 'class="pp-sk-ko"' in text and " 1입니다." in text   # "번역문"은 밑줄 태그로 감싸져 있다
    assert text.count('class="pp-u method"') >= 2   # 원문 카드·원문 패널·번역 쪽


def test_translation_blocked_after_session_limit(at, monkeypatch, tmp_path):
    from src import translate as T
    from src import usage

    # 가짜 DeepL 키(진짜 호출 전에 제한에서 막히는지만 본다)
    monkeypatch.setattr(T, "load_api_key", lambda name="DEEPL_API_KEY": "fake:fx" if name == "DEEPL_API_KEY" else None)
    at.session_state["deepl_calls"] = usage.MAX_CALLS_PER_SESSION
    at.session_state["input_text"] = "A short new sentence for testing limits. Another sentence here."
    at.run()
    at.button(key="translate_btn").click().run()
    assert not at.exception
    assert any("번까지" in w.value for w in at.warning)


def test_flow_and_glossary_section(at):
    text = md_text(at)
    assert "이해를 돕는 자료" in text and 'class="pp-flow"' in text
    assert "단어표 · " in text and "RAG" in text
    assert any(b.label.startswith("단어표 내려받기") for b in at.get("download_button"))


def test_ai_glossary_column_shows_when_present(at):
    import hashlib

    key = hashlib.sha256(at.session_state["input_text"].encode("utf-8")).hexdigest()[:16]
    assert "AI 풀이" not in md_text(at).split("어려운 단어표")[-1].split("</table>")[0]
    at.session_state["ai_gloss"] = {key: {"RAG": "검색 증강 생성"}}
    at.run()
    assert "검색 증강 생성" in md_text(at) and "참고용</span>" in md_text(at)
    assert next(b for b in at.button if b.key == "ai_gloss_btn").disabled      # 키 없음 + 이미 받음


def test_reading_modes(at):
    import hashlib

    from src.extract import analyze
    from src.translate import TranslationResult

    a = analyze(at.session_state["input_text"])
    key = (hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16], True)
    ko = [f"번역 {i}" for i in range(len(a.sentences))]
    at.session_state["translations"] = {key: TranslationResult(ko=ko, mode="aligned", ko_marks=[[] for _ in ko],
                                                               protected=[[]] * len(ko))}

    def view(mode):
        at.session_state["read_mode"] = mode
        at.run()
        assert not at.exception, mode
        return "\n".join(m.value for m in at.markdown if not m.value.lstrip().startswith("<style"))

    side = view("side")
    assert "영어 원문" in side and side.count('class="pp-side-row"') == len(a.sentences)   # 문장 짝마다 한 행
    assert 'class="pp-side-ko"><div class="pp-text">번역 0' in side
    pair = view("pair")
    assert pair.count('class="pp-pair-ko"') == len(a.sentences) and 'class="pp-sent pp-ko"' not in pair
    en = view("en")
    assert "영어 원문" in en and 'class="pp-sent pp-ko"' not in en and "pp-pair-ko" not in en
    ko_only = view("ko")
    assert 'class="pp-sent pp-ko"' in ko_only and "영어 원문</span>" not in ko_only and "영어 원문 <span" not in ko_only


def test_frequent_term_tooltip_shows_meaning(at):
    import hashlib

    text = ("The reranker scores passages. A reranker is small. We train the reranker on data. "
            "Our reranker helps retrieval.")
    at.session_state["input_text"] = text
    at.run()
    before = md_text(at)
    assert "누르면 여기에 떠요" in before                                     # 뜻을 모르면 받는 방법 안내
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    at.session_state["ai_gloss"] = {key: {"reranker": "검색 결과 순위를 다시 매기는 모델"}}
    at.run()
    assert "reranker: 검색 결과 순위를 다시 매기는 모델" in md_text(at)


def test_korean_highlight_tooltip_matches_english(at):
    import hashlib
    import html as H

    from src.extract import analyze
    from src.translate import TranslationResult

    text = ("The reranker scores passages. A reranker is small. We train the reranker on data. "
            "Our reranker may help retrieval.")
    at.session_state["input_text"] = text
    a = analyze(text)
    tk = hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16]
    ko = ["재순위 모델이 문단에 점수를 매긴다.", "재순위 모델은 작다.", "우리는 재순위 모델을 학습한다.",
          "우리의 재순위 모델은 검색에 도움이 될 수 있다."]
    km = [[(0, 6, "term", "reranker")], [(0, 6, "term", "reranker")], [(4, 10, "term", "reranker")],
          [(4, 10, "term", "reranker"), (21, 25, "hedge", "may")]]
    at.session_state["translations"] = {(tk, True): TranslationResult(ko=ko, mode="aligned", ko_marks=km,
                                                                      protected=[[]] * 4)}
    at.session_state["ai_gloss"] = {tk: {"reranker": "검색 결과 순위를 다시 매기는 모델"}}
    at.run()
    out = H.unescape(md_text(at))
    assert "reranker: 검색 결과 순위를 다시 매기는 모델 · 이 글에 4번 · 영어 'reranker'에 대응" in out
    assert "추측·가능성" in out and "영어 'may'에 대응" in out


def test_tables_and_figures_inline_after_first_mention(at):
    from src.tables import TableInfo

    at.session_state["input_text"] = ("We describe the model. Results are in Table 1 and Figure 2. "
                                      "As Table 1 shows, the gains are large.")
    at.session_state["source"] = {"kind": "pdf", "title": "t", "section": "본문", "page": "본문"}
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    at.session_state["paper_tables"] = [TableInfo(1, "Table 1: Scores.", 3, png, width=400, height=100)]
    at.session_state["paper_figures"] = [TableInfo(2, "Figure 2: Curves.", 4, b"\xff\xd8xx", kind="figure",
                                                   width=300, height=200, mime="image/jpeg")]
    at.run()
    assert not at.exception
    text = "\n".join(m.value for m in at.markdown)
    assert ".pp-flt-table-1{background-image:url(data:image/png;base64," in text
    assert text.count('class="pp-float"') == 2                       # 표 1·그림 2, 처음 언급한 문장 아래 한 번씩
    assert 'class="pp-pop"' in text and "Table 1: 문장 2 아래에 있어요" in text   # 3번째 문장의 두 번째 언급


def test_tables_also_appear_in_korean_translation(at):
    import hashlib

    from src.extract import analyze
    from src.tables import TableInfo
    from src.translate import TranslationResult

    text = "We describe the model. Results are in Table 1. The gains are large."
    at.session_state["input_text"] = text
    at.session_state["source"] = {"kind": "pdf", "title": "t", "section": "본문", "page": "본문"}
    at.session_state["paper_tables"] = [TableInfo(1, "Table 1: Scores.", 3, b"\x89PNG", width=400, height=100)]
    a = analyze(text)
    tk = hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16]
    ko = ["모델을 설명한다.", "결과는 표 1에 있다.", "향상이 크다."]
    at.session_state["translations"] = {(tk, True): TranslationResult(ko=ko, mode="aligned", ko_marks=[[], [], []],
                                                                      protected=[[], [], []])}
    for mode, n in (("side", 1), ("ko", 1), ("en", 1), ("pair", 1)):
        at.session_state["read_mode"] = mode
        at.run()
        out = "\n".join(m.value for m in at.markdown)
        assert out.count('class="pp-float"') == n, mode          # 나란히 보기는 문장 짝 아래에 한 번
    at.session_state["read_mode"] = "ko"
    at.run()
    assert "<b>표 1</b>" in "\n".join(m.value for m in at.markdown)


def test_reopen_saved_pdf_document_without_file(blank):
    import hashlib

    from src import saved as SV
    from src.translate import TranslationResult

    secs = [{"title": "Abstract", "text": "We propose a small reranker. It helps retrieval."},
            {"title": "1 Introduction", "text": "Retrieval is hard. We study it."},
            {"title": "2 Conclusion", "text": "We showed gains."}]
    rec = SV.doc_record("pdf", "A Tiny Paper", secs)
    abstract_key = hashlib.sha256(secs[0]["text"].encode("utf-8")).hexdigest()[:16]
    blank.session_state["browser_docs"] = {rec["id"]: rec}
    blank.session_state["translations"] = {(abstract_key, True): TranslationResult(
        ko=["우리는 작은 재순위 모델을 제안한다.", "검색에 도움이 된다."], mode="aligned", ko_marks=[[], []], protected=[[], []])}
    blank.run()
    text = "\n".join(m.value for m in blank.markdown)
    assert "A Tiny Paper" in text and "번역 1/3쪽" in text          # 초록만 번역돼 있음(초록·본문 1·결론)
    blank.button(key=f"doc_open_{rec['id']}").click().run()
    assert not blank.exception
    ss = blank.session_state
    assert ss["source"]["kind"] == "pdf" and ss["input_text"] == secs[0]["text"] and not ss["show_input"]
    out = "\n".join(m.value for m in [*blank.markdown, *blank.caption])   # 논문 안내(notes)는 00 아래 caption
    assert "우리는 작은 재순위 모델을 제안한다." in out and "표·그림은 PDF를 다시 넣으면 보여요." in out
    assert any(e.label.startswith("논문 전체 보기") for e in blank.expander)   # 펼침 칸 셋을 하나(탭)로 묶음
    blank.button(key="reinput_btn").click().run()
    blank.button(key=f"doc_del_{rec['id']}").click().run()
    assert rec["id"] not in blank.session_state["browser_docs"]


def test_glossary_preview_and_cards_above(at):
    words = ["heuristic", "softmax", "concatenation", "sinusoid", "transduction", "monolingual", "entailment",
             "extrapolate", "cosine", "warmup", "optimizer", "superset"]
    at.session_state["input_text"] = " ".join(f"We use a {w} here." for w in words)
    at.run()
    out = "\n".join(m.value for m in at.markdown if not m.value.lstrip().startswith("<style"))
    assert "어려운 단어표 · 12개" in out
    first_table = out[out.index('class="pp-table pp-gloss"'):]
    first_table = first_table[:first_table.index("</table>")]
    assert first_table.count("<tr>") == 1 + 8                       # 머리글 + 처음 8개
    assert any(e.label.startswith("전체 단어표 보기 · 12개") for e in at.expander)
    assert [t.label for t in at.tabs][:3] == ["논문 흐름도", "어려운 단어 12", "약어·규칙 근거 0"]   # 03은 탭으로
    assert "<td>-</td>" not in first_table                          # 빈 칸은 '-' 대신 비움



def test_missing_problem_card_explains_and_zero_chips_hidden(at):
    at.session_state["input_text"] = ("We introduce a new model called Foo for parsing. It parses fast. "
                                      "Results show that Foo improves accuracy by 3%.")
    at.run()
    out = "\n".join(m.value for m in at.markdown if not m.value.lstrip().startswith("<style"))
    assert "첫 문장부터 바로 방법을 소개해요" in out and "지어낸 문장으로 채우지 않아요" in out
    assert not any("추측·가능성" in o for o in mark_chips(at).options)   # 0개인 표시는 칩이 없다


def test_reading_helper_in_sticky_bar(at):
    assert at.get("popover")                                          # 고정 막대의 '읽기 도우미' 팝오버
    out = "\n".join(m.value for m in at.markdown)
    assert "**RAG** → Retrieval-augmented generation" in out           # 팝오버 안 약어 목록
    assert "pp-intro" not in out or "ACADEMIC READING WORKSPACE" not in out.split("pp-loaded")[0]


def open_paper_doc(blank, sections):
    """절 목록으로 저장된 PDF 문서를 만들어 다시 연 화면."""
    from src import saved as SV
    rec = SV.doc_record("pdf", "A Protocol Paper", sections)
    blank.session_state["browser_docs"] = {rec["id"]: rec}
    blank.run()
    blank.button(key=f"doc_open_{rec['id']}").click().run()
    assert not blank.exception, blank.exception
    return blank


def protocol_sections():
    para = "\n\n"
    steps = para.join(f"{k} Add the simulated fluid to the sample and mix it well for one minute." for k in range(1, 46))
    proc = ("CRITICAL STEP Prepare the salivary fluid fresh on the day of the experiment." + para + steps + para +
            "PAUSE POINT Samples can be frozen at minus twenty degrees." + para + "CAUTION Bile is toxic if swallowed.")
    return [{"title": "Abstract", "text": "We present a static digestion method for foods. It uses standard tools."},
            {"title": "Introduction", "text": "Food digestion matters. We study it in the lab."},
            {"title": "Procedure", "text": proc, "subheads": ["Preparation", "Digestion procedure · Timing 5–8 h"],
             "sub_at": [0, proc.index("20 Add")]},
            {"title": "Conclusion", "text": "The method works well."}]


def test_protocol_section_shows_procedure_cards_and_parts(blank):
    at = open_paper_doc(blank, protocol_sections())
    next(g for g in at.get("button_group") if g.key == "paper_page").set_value("본문").run()
    next(g for g in at.get("button_group") if g.key == "section_pick").set_value(1).run()
    assert not at.exception, at.exception
    out = "\n".join(m.value for m in at.markdown)
    assert "먼저 볼 실험 절차 요점" in out and "먼저 읽을 3줄 뼈대" not in out
    assert 'class="pp-subhead">Preparation</div>' in out                      # 본문 문장 위에 소제목 줄
    assert "CRITICAL STEP" in out and "PAUSE POINT" in out and "번호 단계 <b>1–45</b>" in out
    parts = next(g for g in at.get("button_group") if g.key == "part_pick")
    assert [o.split(" · ")[0] for o in parts.options] == ["Preparation", "Digestion procedure"]
    assert 'class="pp-no">25<' not in out                               # 첫 부분(1~20번째 단계 문장)만 보인다
    assert not any(b.key == "part_prev" for b in at.button)           # 첫 부분에는 '이전'이 없다
    at.button(key="part_next").click().run()
    assert 'class="pp-no">25<' in "\n".join(m.value for m in at.markdown)
    assert not any(b.key == "part_next" for b in at.button)           # 마지막 부분에는 '다음'이 없다
    at.button(key="part_prev").click().run()                           # 이전 부분으로 돌아가기
    assert 'class="pp-no">25<' not in "\n".join(m.value for m in at.markdown)


def test_body_section_has_no_position_fallback(blank):
    secs = protocol_sections()
    secs[2] = {"title": "3 Model", "text": "The encoder has six layers. Each layer has two sublayers."}
    at = open_paper_doc(blank, secs)
    next(g for g in at.get("button_group") if g.key == "paper_page").set_value("본문").run()
    next(g for g in at.get("button_group") if g.key == "section_pick").set_value(1).run()
    out = "\n".join(m.value for m in at.markdown)
    assert "먼저 읽을 3줄 뼈대" in out and "분야 배경 (첫 문장)" not in out


def test_side_mode_before_translation_shows_english_only(at):
    out = md_text(at)
    assert at.session_state["read_mode"] == "side" and 'class="pp-side-row"' not in out
    assert 'class="pp-panel pp-narrow"' in out                         # 번역 전에는 영어 원문 한 칸


def test_flow_map_points_to_cards_instead_of_repeating(at):
    out = md_text(at)
    assert "위 01 카드 참고" in out


def test_long_skeleton_sentence_is_clamped():
    from app import SK_CLAMP_CHARS, skeleton_card
    from src.extract import analyze
    long = "We introduce a method that " + "keeps reading the long sentence and " * 12 + "ends here."
    a = analyze(long)
    card = next(c for c in a.skeleton if c.sentence_idx is not None)
    assert len(long) > SK_CLAMP_CHARS and 'class="pp-sk-more"' in skeleton_card(card, a)


def test_mark_tooltip_has_no_duplicate_browser_title(at):
    out = md_text(at)
    assert 'data-tip="' in out
    assert not re.search(r'title="[^"]*" data-tip=', out)              # 브라우저 기본 툴팁과 겹치지 않게


def test_many_parts_use_dropdown(blank):
    secs = protocol_sections()
    text = secs[2]["text"]
    cuts = [text.index(f"{k} Add") for k in (1, 6, 11, 16, 21, 26, 31, 36)]
    secs[2] = {**secs[2], "subheads": [f"Part {c}" for c in "ABCDEFGH"], "sub_at": cuts}
    at = open_paper_doc(blank, secs)
    next(g for g in at.get("button_group") if g.key == "paper_page").set_value("본문").run()
    next(g for g in at.get("button_group") if g.key == "section_pick").set_value(1).run()
    assert at.selectbox(key="part_pick").options[0].startswith("처음")   # 부분 9개: 버튼 대신 드롭다운
    assert not any(g.key == "part_pick" for g in at.get("button_group"))
    at.selectbox(key="part_pick").set_value(2).run()
    assert not at.exception and 'class="pp-no">7<' in md_text(at)


def test_feedback9_range_rules_and_labels(at):
    import hashlib

    from src.extract import analyze
    from src.translate import TranslationResult

    text = md_text(at)
    assert "· 문장 1–4" in text                                   # 원문 헤더에 지금 읽는 문장 범위
    assert "규칙 근거" in text and "HEDGE" in text                 # 위쪽 막대 팝오버의 규칙 근거
    a = analyze(at.session_state["input_text"])
    key = (hashlib.sha256(a.text.encode("utf-8")).hexdigest()[:16], True)
    ko = [f"번역 {i}" for i in range(len(a.sentences))]
    at.session_state["translations"] = {key: TranslationResult(ko=ko, mode="aligned", ko_marks=[[] for _ in ko],
                                                               protected=[[]] * len(ko))}
    at.run()
    assert any(b.key == "compare_btn" and b.label == "보호 없이 번역해 비교" for b in at.button)


def test_position_card_text_is_not_faded(at):
    at.session_state["input_text"] = ("Large models are popular in many fields today. We train a small network on a new corpus. "
                                      "The network reaches high accuracy on the test set.")
    at.run()
    assert 'pp-card' in md_text(at) and ' est"' in md_text(at)    # 위치 추정 카드는 진한 글자 클래스


@pytest.mark.parametrize("before,name", [
    ("Bovine bile ", "Bovine bile"), ("alternatively, porcine bile ", "porcine bile"),
    ("lipase can be used Porcine pancreatin ", "Porcine pancreatin"), ("CAUTION NaOH ", "NaOH"),
    ("Bile Acid Assay Kit ", "Bile Acid Assay Kit"), ("water. 3,5-Dinitrosalicylic acid ", "3,5-Dinitrosalicylic acid"),
    ("2 ", "")])
def test_reagent_name_before_catalog_number(before, name):
    from app import reagent_name
    assert reagent_name(before) == name


def test_materials_section_shows_found_cards_first(blank):
    secs = protocol_sections()
    mat = ("Bovine bile (Sigma-Aldrich, cat. no. B3883) is used for the intestinal phase of the method.\n\n"
           "CAUTION NaOH (Merck, cat. no. 9141) is corrosive and causes severe skin burns.\n\n"
           "Porcine pepsin (Sigma-Aldrich, cat. no. P7012) is stored at minus twenty degrees.")
    secs[2] = {"title": "Materials", "text": mat}
    at = open_paper_doc(blank, secs)
    next(g for g in at.get("button_group") if g.key == "paper_page").set_value("본문").run()
    next(g for g in at.get("button_group") if g.key == "section_pick").set_value(1).run()
    out = md_text(at)
    i_caution, i_reagent = out.index("주의 · CRITICAL·CAUTION"), out.index("시약·제품 · cat. no.")
    assert i_caution < i_reagent and "<b>Bovine bile</b>" in out and "<b>NaOH</b>" in out
    assert "멈춤 지점 · PAUSE POINT" in out and out.index("멈춤 지점 · PAUSE POINT") > i_reagent   # 빈 카드는 뒤로
    assert re.search(r'<span class="pp-cat">\(.*?cat\. no\. B3883\)</span>', out)            # 품번은 흐리게
