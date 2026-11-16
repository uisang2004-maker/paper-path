import pytest

from src import patterns as P
from src.collect import load_samples
from src.extract import analyze, find_acronyms, match_long_form

DEMO = ("Retrieval-augmented generation (RAG) can ground language models in external documents, but selecting "
        "reliable evidence remains challenging. To address this issue, we introduce a lightweight reranking module "
        "that scores retrieved passages using contextual relevance. Experiments on three public benchmarks show that "
        "the proposed module significantly improves retrieval precision by 8.4% compared with the baseline. These "
        "findings suggest that a compact reranker may help smaller models use evidence more consistently [4].")


# ---------------------------------------------------------------- 약어 (P0-3)

@pytest.mark.parametrize("text,abbr,expected", [
    ("We use a Gated Recurrent Unit (GRU) here.", "GRU", "Gated Recurrent Unit"),
    ("You Only Look Once (YOLO) detects objects.", "YOLO", "You Only Look Once"),
    ("We apply Low-Rank Adaptation (LoRA) to it.", "LoRA", "Low-Rank Adaptation"),
    ("Retrieval-Augmented Generation (RAG) helps.", "RAG", "Retrieval-Augmented Generation"),
    ("Bidirectional Encoder Representations from Transformers (BERT) is used.", "BERT",
     "Bidirectional Encoder Representations from Transformers"),
    ("Chain-of-Thought (CoT) prompting works.", "CoT", "Chain-of-Thought"),
    ("Large language models (LLMs) are big.", "LLM", "Large language models"),
])
def test_acronym_success(text, abbr, expected):
    assert find_acronyms(text)[abbr] == expected


@pytest.mark.parametrize("text,abbr", [
    ("Extreme Gradient Boosting (XGBoost) is strong.", "XGBoost"),
    ("A Bidirectional LSTM (BiLSTM) encoder.", "BiLSTM"),
    ("k-nearest neighbors (k-NN) is simple.", "NN"),
])
def test_acronym_failure_does_not_invent(text, abbr):
    assert find_acronyms(text).get(abbr) is None


def test_all_letters_retry_only_when_needed():
    assert match_long_form("CoT", "Chain-of-Thought ") is None
    assert match_long_form("CoT", "Chain-of-Thought ", all_letters=True) == "Chain-of-Thought"


def test_capitalized_emphasis_is_not_acronym():
    acr = find_acronyms("We propose CREST, a method for Cognitive REasoning Steering.")
    assert "REasoning" not in acr and "CREST" in acr


def test_bare_acronym_marked_as_missing():
    a = analyze("GPU memory is limited. We test it.")
    assert a.acronyms == {"GPU": None}
    assert any(m.category == "abbr-missing" for m in a.marks)


# ---------------------------------------------------------------- 추측·확신·참조 (P0-4)

def marked(text, active=None, category="hedge"):
    a = analyze(text, active)
    return [a.text[m.start:m.end] for m in a.marks if m.category == category]


@pytest.mark.parametrize("text,hit", [
    ("The gains may not necessarily reflect real use.", "may"),
    ("These findings suggest a link.", "suggest"),
    ("The model seems to overfit.", "seems to"),
    ("This is likely due to noise.", "likely"),
])
def test_hedge_positive(text, hit):
    assert hit in marked(text)


@pytest.mark.parametrize("text", [
    "The method may be applied to images.",
    "It could also be easily extended to speech.",
    "The tool can be used offline.",
])
def test_hedge_exclusion(text):
    assert marked(text) == []


def test_hedge_exclusion_toggle_off_marks_again():
    active = P.DEFAULT_ACTIVE_RULES - {"HEDGE_EXCLUDE"}
    assert marked("The method may be applied to images.", active) == ["may"]


def test_hedge_case_insensitive():
    assert marked("May this hold? Perhaps.") == ["May", "Perhaps"]


def test_confident_and_reference():
    assert marked("We demonstrate state-of-the-art results.", category="confident") == ["demonstrate", "state-of-the-art"]
    assert marked("As in [3, 5] and (Smith et al., 2021), see Table 2 and Eq. (4).", category="reference") == [
        "[3, 5]", "(Smith et al., 2021)", "Table 2", "Eq. (4)"]


def test_rule_off_removes_marks():
    a = analyze(DEMO, P.DEFAULT_ACTIVE_RULES - {"ABBR", "REFERENCE"})
    assert not any(m.category in ("abbr", "reference") for m in a.marks)
    assert a.counts["abbr"] == 0 and a.counts["reference"] == 0


def test_demo_counts():
    assert analyze(DEMO).counts == {"abbr": 1, "hedge": 2, "confident": 2, "reference": 1, "definition": 0, "number": 1, "term": 0}


# ---------------------------------------------------------------- 3줄 뼈대 (P0-2)

def card(a, kind):
    return next(c for c in a.skeleton if c.kind == kind)


def test_demo_skeleton_all_signals():
    a = analyze(DEMO)
    assert [(c.kind, c.sentence_idx, c.basis) for c in a.skeleton] == [
        ("problem", 0, "signal"), ("method", 1, "signal"), ("result", 2, "signal")]
    s = a.sentences[1].text
    c = card(a, "method")
    assert s[c.signal[0]:c.signal[1]] == "we introduce"
    assert c.reason


def test_contrast_second_sentence_is_problem():
    a = analyze("Recent advances in large models have shown success. However, they are costly to run. "
                "We propose a pruning method. It reduces memory by 40%.")
    assert card(a, "problem").sentence_idx == 1
    assert card(a, "problem").basis == "signal"


def test_new_contrast_words_are_problem_signals():
    for w in ("Despite progress, errors persist.", "Although useful, it is slow.", "Unfortunately, data is scarce."):
        assert P.SK_PROBLEM.search(w)


def test_fallback_labels_and_exclusions():
    a = analyze("Speech systems are common in phones. Many users rely on them daily. "
                "The new system reached 92 points on the test. Future work will cover other languages. "
                "Code is available at https://github.com/x/y.")
    p, m, r = (card(a, k) for k in ("problem", "method", "result"))
    assert (p.basis, p.label, p.sentence_idx) == ("position", "분야 배경 (첫 문장)", 0)
    assert m.basis == "none" and m.label == "방법 문장을 찾지 못함"
    assert r.basis == "position" and r.label == "마무리 문장 (결과 추정)"
    assert "Code is available" not in a.sentences[r.sentence_idx].text
    assert "Future work" not in a.sentences[r.sentence_idx].text
    assert 3 in a.limit_idx


def test_method_fallback_uses_author_voice():
    # "our system"은 방법 신호 문구라서, 신호에 없는 "our tokenizer"로 위치 대체를 확인한다.
    a = analyze("Translation quality varies widely. Our tokenizer uses a small vocabulary. It is fast.")
    m = card(a, "method")
    assert m.basis == "position" and m.sentence_idx == 1


def test_fallback_toggle_off():
    a = analyze("Speech systems are common. Many users rely on them.", P.DEFAULT_ACTIVE_RULES - {"SK_FALLBACK"})
    assert all(c.basis == "none" for c in a.skeleton)


def test_samples_never_produce_blank_cards():
    for r in load_samples():
        for c in analyze(r["abstract"]).skeleton:
            assert c.label and c.reason
            assert (c.sentence_idx is None) == (c.basis == "none")


# ---------------------------------------------------------------- 핵심 내용 밑줄

def ul(kind, sentence):
    from src.extract import underline_span
    m = {"method": P.SK_METHOD, "problem": P.SK_PROBLEM, "result": P.SK_RESULT}[kind].search(sentence)
    sp = underline_span(kind, sentence, (m.start(), m.end()))
    return sentence[sp[0]:sp[1]] if sp else None


@pytest.mark.parametrize("kind,sentence,expected", [
    ("method", "We propose a lightweight architecture that reduces memory overhead.",
     "a lightweight architecture that reduces memory overhead"),
    ("method", "We introduce a novel framework to estimate global interest rates.",
     "a novel framework to estimate global interest rates"),
    ("method", "In this paper, we propose a pruning method for transformers.", "a pruning method for transformers"),
    ("method", "In this paper, a lightweight reranking module is introduced.", "a lightweight reranking module"),
    ("method", "In this work, the proposed model, when evaluated on Benchmark X, is introduced.", "the proposed model"),
    ("problem", "However, selecting reliable evidence remains challenging.", "selecting reliable evidence remains challenging"),
    ("problem", "Although existing models, such as BERT and RoBERTa, achieve high accuracy, they suffer from memory overhead.",
     "they suffer from memory overhead"),
    ("problem", "Despite recent advances, reliability remains an unresolved issue.", "reliability remains an unresolved issue"),
    ("result", "Experiments on three public benchmarks show that the proposed module significantly improves retrieval "
               "precision by 8.4% compared with the baseline.",
     "the proposed module significantly improves retrieval precision by 8.4%"),
    ("result", "Significant performance improvements were observed.", "Significant performance improvements"),
])
def test_underline_span(kind, sentence, expected):
    assert ul(kind, sentence) == expected


def test_underline_cap_on_long_sentence():
    got = ul("method", "We propose a novel transformer-based framework that integrates multi-modal contextual "
                       "embeddings with reinforcement learning to optimize execution plans.")
    assert len(got.split()) <= P.UNDERLINE_MAX_WORDS and not got.endswith((" with", " the", " to"))


def test_fallback_cards_have_no_underline():
    a = analyze("Speech systems are common in phones. Many users rely on them daily. The system reached 92 points.")
    assert all(c.underline is None for c in a.skeleton if c.basis != "signal")


def test_samples_underline_length():
    lengths = []
    for r in load_samples():
        a = analyze(r["abstract"])
        for c in a.skeleton:
            if c.underline:
                s = a.sentences[c.sentence_idx].text
                lengths.append(len(s[c.underline[0]:c.underline[1]].split()))
    assert lengths and max(lengths) <= P.UNDERLINE_MAX_WORDS


# ---------------------------------------------------------------- 용어 설명·수치 근거

def expl(text):
    a = analyze(text)
    return [a.text[x:y] for x, y, _ in a.explanations]


@pytest.mark.parametrize("text,expected", [
    ("Hierarchical text classification, which aims to classify text documents into a given hierarchy, is important.",
     ["classify text documents into a given hierarchy"]),
    ("We evaluate methods such as deep neural networks for natural language inference (NLI). It works.",
     ["deep neural networks for natural language inference (NLI)"]),
    ("We propose GMM-LDA, a topic modeling based Bayesian unsupervised model, to analyze text.",
     ["a topic modeling based Bayesian unsupervised model"]),
    ("A pattern refers to a formatted combination of several words. Next.", ["a formatted combination of several words"]),
    ("The model is faster (i.e., it uses less memory) than before.", ["it uses less memory"]),
])
def test_explanation_spans(text, expected):
    assert expl(text) == expected


def test_adverb_is_not_appositive_term():
    assert expl("Finally, a visual-semantic encoder is exploited to refine the features, and it helps.") == []


def test_definition_and_number_marks():
    a = analyze("Tasks such as parsing reach an accuracy of 74% and run 2x faster.")
    got = [(a.text[m.start:m.end], m.category) for m in a.marks if m.category in ("definition", "number")]
    assert got == [("such as", "definition"), ("accuracy of 74%", "number"), ("2x", "number")]


def test_definition_toggle_removes_explanations():
    a = analyze("Tasks such as parsing are hard.", P.DEFAULT_ACTIVE_RULES - {"DEFINITION"})
    assert a.explanations == [] and a.counts["definition"] == 0


# ---------------------------------------------------------------- 반복 용어

def terms(text):
    from src.extract import frequent_terms
    return {w: n for w, n, _ in frequent_terms(analyze(text).text)}


def test_frequent_two_word_term_and_single_word():
    t = ("Text classification is hard. Existing text classification models ignore the label hierarchy. "
         "We add label information. Our text classification uses each label.")
    assert terms(t) == {"text classification": 3, "label": 3}


def test_function_words_and_generic_words_are_excluded():
    t = "The model and the data. The model and the data. The model and the data."
    assert terms(t) == {}


def test_plural_counts_as_same_term_and_ed_words_excluded():
    t = "One lexicon helps. Two lexicons help. Lexicons are weighted, weighted and weighted."
    assert terms(t) == {"lexicon": 3}


def test_term_toggle_and_marks():
    t = "A parser parses. The parser fails. Every parser learns."
    a = analyze(t)
    terms = [m for m in a.marks if m.category == "term"]
    assert [a.text[m.start:m.end] for m in terms] == ["parser"]          # 처음 나온 곳에만 표시
    assert terms[0].start == t.index("parser") and "3번" in terms[0].tip  # 설명창에는 등장 횟수 그대로
    assert a.counts["term"] == 1
    assert analyze(t, P.DEFAULT_ACTIVE_RULES - {"TERM"}).counts["term"] == 0


def test_term_marks_skip_common_words_names_and_figures():
    """흔한 영어(because·people)·이름(대문자로만 나오는 Kendi)·그림 표기(Fig)는 반복돼도 표시하지 않는다."""
    t = ("Kendi says people argue because tokenizers fail. Kendi notes people disagree because tokenizers drift. "
         "Kendi adds that people worry because tokenizers break, as Fig 1 and Fig 2 and Fig 3 show.")
    a = analyze(t)
    assert [a.text[m.start:m.end] for m in a.marks if m.category == "term"] == ["tokenizers"]


def test_term_marks_keep_top_five():
    """많이 나온 순서로 5개까지만 표시한다(형광펜이 배경처럼 보이지 않게)."""
    words = ["tokenizer", "embedding", "lexicon", "morpheme", "phoneme", "corpus", "treebank"]
    t = " ".join(f"The {w} helps." for w in words for _ in range(3)) + " The tokenizer helps. The embedding helps."
    shown = [t[m.start:m.end] for m in analyze(t).marks if m.category == "term"]
    assert len(shown) == P.TERM_TOP == 5 and shown[:2] == ["tokenizer", "embedding"]
