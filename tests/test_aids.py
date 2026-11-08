"""이해를 돕는 자료: 흐름도(문장 역할)와 단어표."""

from src import glossary as G
from src.extract import analyze
from src.translate import TranslationResult

TEXT = ("Speech systems are common in phones. However, accents remain challenging. "
        "We propose AccentNet, a small adapter, to handle accents. "
        "Experiments show that AccentNet reduces errors by 12% on accented speech. "
        "Future work will cover more languages. Retrieval-augmented generation (RAG) is related.")


def test_roles_follow_paper_flow():
    assert analyze(TEXT).roles == ["background", "problem", "method", "result", "limit", "detail"]


def test_first_sentence_fallback_is_background():
    a = analyze("Speech systems are common. Many users rely on them. Our model reached 92 points.")
    assert a.roles[0] == "background"


def test_glossary_collects_acronyms_terms_and_explained():
    rows = {e.term: e for e in G.build(analyze(TEXT))}
    assert rows["RAG"].explanation == "Retrieval-augmented generation" and rows["RAG"].kinds == ["abbr"]
    assert "AccentNet" not in rows and "AccentNet" in G.proper_names(analyze(TEXT))   # 모델 이름은 이름 목록으로
    assert "accent" not in rows      # accents 2번 + accented(-ed 한 단어 제외) → 3번이 안 됨


def test_glossary_korean_from_translation_tags():
    a = analyze(TEXT)
    ko = ["가"] * len(a.sentences)
    ko[2] = "우리는 악센트넷을 제안한다."
    ko[5] = "검색 증강 생성(RAG)은 관련이 있다."
    marks = [[] for _ in ko]
    marks[2] = [(4, 8, "term", "AccentNet")]
    at = ko[5].find("RAG")
    marks[5] = [(at, at + 3, "abbr", "RAG")]
    tr = TranslationResult(ko=ko, mode="aligned", ko_marks=marks)
    rows = {e.term: e for e in G.build(a, G.korean_marks(tr))}
    assert rows["RAG"].korean == ""        # 번역되지 않고 그대로 남은 약어는 한국어 칸에 넣지 않음


def test_csv_has_bom_and_header():
    data = G.to_csv(G.build(analyze(TEXT)))
    assert data.startswith("﻿".encode("utf-8")) and "용어,종류".encode("utf-8") in data


# ---------------------------------------------------------------- 어려운 단어(전문용어) 위주

def test_easy_words_are_excluded_and_rare_words_have_levels():
    t = ("A label is a sentence. The label marks each sentence. Every label and sentence matters. "
         "We use a stochastic reranker with heuristic annotation.")
    rows = {e.term.lower(): e for e in G.build(analyze(t))}
    assert "label" not in rows and "sentence" not in rows          # 일상어(Zipf 4.0 이상)
    assert "stochastic" not in rows                                 # Zipf 3.11: 전문용어 기준(3.0 미만)보다 흔함
    assert "reranker" not in rows                                   # rank(흔한 말)에 re-·-er가 붙은 말
    assert rows["heuristic"].level == "상"


def test_generic_academic_words_and_long_form_words_are_excluded():
    t = "We propose a method. Retrieval-augmented generation (RAG) helps retrieval."
    terms = {e.term.lower() for e in G.build(analyze(t))}
    assert "propose" not in terms and "retrieval-augmented" not in terms and "rag" in terms


def test_word_zipf_uses_base_forms():
    assert G.word_zipf("baselines") == G.word_zipf("baseline") > 0
    assert G.word_zipf("propagated") >= G.word_zipf("propagate") > 0
    assert G.word_zipf("qzxwv") == 0.0


def test_jargon_marks_are_for_translation_only():
    a = analyze("A heuristic softmax helps.")
    assert [a.text[x:y] for x, y, _ in G.jargon_marks(a)] == ["heuristic", "softmax"]
    assert not any(m.category == "jargon" for m in a.marks)



def test_only_jargon_that_korean_translation_does_not_explain():
    t = ("Following Vaswani et al. (2017), the unlabeled data are processed holistically. "
         "Our bidirectional model is outperforming baselines. We apply softmax and a heuristic. "
         "The dmodel and headi values come from the AI system.")
    terms = {e.term.lower() for e in G.build(analyze(t))}
    assert "vaswani" not in terms                       # 인용 속 저자 이름
    assert "unlabeled" not in terms and "bidirectional" not in terms and "outperforming" not in terms   # 흔한 말 + 접사
    assert "holistically" not in terms                  # 부사
    assert "dmodel" not in terms and "headi" not in terms   # 수식 기호가 붙은 말
    assert "ai" not in terms                            # 누구나 아는 약어
    assert {"softmax", "heuristic"} <= terms


def test_person_and_model_names_are_kept_out_of_the_glossary():
    t = ("McCann et al. (2017) used CoVe. LeCun et al. [12] trained ResNet on ImageNet. "
         "We compare ResNet with a heuristic softmax. Retrieval-augmented generation (RAG) uses ImageNet features.")
    a = analyze(t)
    terms = {e.term for e in G.build(a)}
    assert not ({"McCann", "LeCun", "ResNet", "ImageNet"} & terms)
    assert "RAG" in terms and {"softmax", "heuristic"} <= terms     # 풀네임 있는 약어, 전문용어는 그대로
    names = G.proper_names(a)
    assert "ImageNet" in names and "ResNet" in names and "McCann" not in names and "LeCun" not in names


def test_word_forms_merge_but_acronyms_stay_apart():
    a = analyze("We annotate the corpus. Later we kept annotating more data. Workers annotated it. "
                "The annotation is noisy.")
    rows = G.build(a)
    assert [(e.term, e.count, e.first_sentence) for e in rows] == [("annotate", 4, 1)]
    a = analyze("Named entity recognition (NER) finds named entities (NE). NER is hard.")
    assert {e.term for e in G.build(a)} == {"NER", "NE"}           # 서로 다른 약어는 합치지 않는다


def test_example_span_stops_before_which_clause():
    """예시 표현('such as …')의 밑줄은 ', which …' 앞에서 멈춘다(정규식 \b가 백스페이스로 잘못 들어가 있던 회귀)."""
    from src.extract import analyze
    a = analyze("We evaluate low-resource languages such as Sinhala, which have their own writing script.")
    spans = [a.text[x:y] for x, y, *_ in a.explanations]
    assert "Sinhala" in spans and not any("which" in s for s in spans)
