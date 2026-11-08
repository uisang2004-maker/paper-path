"""PaperPath의 모든 정규표현식 규칙.

규칙의 실체(패턴)와 설명(잡는 것, 예시, 일부러 놓치는 경우, 화면에 보일 한국어 이유)을
이 파일 한 곳에 모은다. 탐색 알고리즘(약어 글자 맞추기, 뼈대 선택)은 extract.py가 맡는다.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    """화면과 규칙 탐색 탭에 노출되는 규칙 하나."""

    name: str
    category: str          # abbr / hedge / confident / reference / problem / method / result / limit / clean / split
    label: str             # 화면 표시 이름
    pattern: re.Pattern
    desc: str              # 무엇을 잡는가
    reason: str = ""       # 뼈대 카드·툴팁에 붙는 한국어 이유 한 줄
    examples: tuple = field(default_factory=tuple)   # 잡는 예
    misses: tuple = field(default_factory=tuple)     # 일부러 놓치는 경우
    toggleable: bool = False


# ====================================================================== 전처리 (P0-1 최소 방어선)

SOFT_NEWLINE = re.compile(r"(?<!\n)[ \t]*\n[ \t]*(?!\n)")
"""문단 안의 한 줄 개행 → 공백. 빈 줄(문단 경계)은 남긴다."""

MULTI_SPACE = re.compile(r"[ \t]{2,}")
"""연속 공백 → 공백 하나."""

DEHYPHEN = re.compile(r"(?P<a>[A-Za-z]*[a-z])-[ \t]*\n[ \t]*(?P<b>[a-z]+)")
"""줄 끝 하이픈으로 끊긴 단어 복원: ``termi-\\nnology`` → ``terminology``.

붙인 단어가 영어 단어 빈도표에 없는데 뒤 조각이 단어이면 원래 하이픈 단어로 보고 하이픈을 남긴다
(``fine-\\ntuned`` → ``fine-tuned``, ``state-of-\\nthe-art`` → ``state-of-the-art``). 판단은 src/clean.py.
"""

SMALL_CAPS_SPLIT = re.compile(r"\b(?P<c>[B-HJ-Z]) (?P<rest>[a-z]{3,})\b")
"""PDF의 작은 대문자 글꼴이 첫 글자만 떨어뜨린 것: ``B idirectional`` → ``Bidirectional``.
A·I는 실제 한 글자 단어라 뺐고, 뒤 조각이 단어(``H fit``)면 붙이지 않는다(src/clean.py)."""

PAGE_NUMBER_LINE = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.M)
"""숫자만 있는 줄(쪽번호) 제거."""

HEADER_MAX_LEN = 60
HEADER_MIN_REPEAT = 3
"""같은 짧은 줄(60자 이하)이 3번 이상 나오면 머리말로 보고 제거한다(줄 세기는 clean.py)."""

# ====================================================================== 문장 분리 (P0-1)

SENT_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[\"'])|\n{2,}")
"""문장 경계: 마침표·물음표·느낌표 뒤 공백 + 대문자/여는 괄호, 또는 빈 줄."""

NON_FINAL = re.compile(
    r"(?:\b(?:e\.g|i\.e|et al|vs|Figs?|Eqs?|Secs?|cf|approx|ca|Refs?|resp|No|Dr|Mr|Mrs|Ms|Prof|St)"
    r"|\bU\.S|\bU\.K)\.$",
    re.I,
)
"""마침표가 있어도 문장 끝이 아닌 약어. 경계 직전 텍스트가 이 패턴으로 끝나면 자르지 않는다.

일부러 놓치는 경우: 목록에 없는 약어 뒤에 대문자가 오면 잘린다.
"""

MATH = re.compile(r"\$[^$\n]{1,120}\$|\\\(.+?\\\)")
"""인라인 수식 ``$...$``, ``\\( ... \\)``. 문장 분리 전에 가려서 수식 안 마침표에서 자르지 않는다."""

URL = re.compile(r"https?://\S+")

# ====================================================================== 약어 (P0-3)

ACRONYM_CANDIDATE = re.compile(
    r"\(\s*(?P<abbr>(?=(?:[^A-Z)]*[A-Z]){2})[A-Za-z][A-Za-z0-9-]{1,11})\s*\)"
)
"""괄호 안 약어 후보: 대문자가 2개 이상인 2~12자 토큰. ``(NLP)``, ``(LLMs)``, ``(LoRA)``, ``(CoT)``.

후보만 찾는다. 앞 단어와 글자를 맞추는 일은 extract.match_long_form()이 한다.
"""

LINKER_WORDS = frozenset({"of", "from", "and", "the", "for", "in", "on", "with", "to", "a", "an", "by", "via"})
"""풀네임 안에 끼어도 되는 연결어(약어 글자를 맡지 않음)."""

WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
"""풀네임 후보를 이루는 단어(하이픈 단어는 한 덩어리)."""

BARE_ACRONYM = re.compile(r"\b[A-Z][A-Za-z]*[A-Z][A-Za-z0-9]*s?\b")
"""정의 없이 쓰인 대문자 약어 후보: ``GPU``, ``GPT``.

대문자 비율이 BARE_MIN_UPPER_RATIO 미만이면 약어로 보지 않는다(extract.py). ``REasoning``처럼
강조용으로 앞 두 글자만 대문자인 단어를 거르기 위해서다(``XGBoost`` 0.43은 통과).
"""

BARE_MIN_UPPER_RATIO = 0.4

ABBR_RULE = Rule(
    "ABBR", "abbr", "약어",
    ACRONYM_CANDIDATE,
    "괄호 안 약어 후보를 찾고, 앞 단어 첫 글자와 순서대로 맞춰 풀네임을 붙인다. 대문자로 맞추기에 실패하면 약어의 모든 글자로 한 번 더 맞춘다.",
    reason="괄호 안 약어를 앞 단어 첫 글자와 맞춰 풀네임을 찾았어요",
    examples=("Gated Recurrent Unit (GRU)", "Low-Rank Adaptation (LoRA)", "Chain-of-Thought (CoT)"),
    misses=("Extreme Gradient Boosting (XGBoost)", "Bidirectional LSTM (BiLSTM)", "k-nearest neighbors (k-NN)"),
    toggleable=True,
)

# ====================================================================== 추측·확신·참조 (P0-4)

HEDGE = re.compile(
    r"\b(?:may|might|could)\b"
    r"|\b(?:suggests?|suggesting|indicates?|likely|unlikely|possibly|perhaps|potentially|presumably)\b"
    r"|\b(?:appears?|seems?|tends?)\s+to\b",
    re.I,
)
HEDGE_EXCLUDE = re.compile(
    r"\b(?:may|can|could|might)\s+(?:(?:also|not|\w+ly)\s+){0,2}be\s+(?:\w+ly\s+)?"
    r"(?:applied|used|extended|adapted|employed|deployed|integrated|combined)\b",
    re.I,
)

HEDGE_RULE = Rule(
    "HEDGE", "hedge", "추측·가능성",
    HEDGE,
    "주장의 강도를 낮추는 조동사·인식 표현. 3등급으로 나누지 않고 한 등급으로 표시한다.",
    reason="저자가 단정하지 않는 표현이에요. 인용할 때 '~일 수 있다'로 옮기세요",
    examples=("may not necessarily reflect", "These findings suggest", "seems to"),
    misses=("문장을 넘는 문맥", "may 뒤 동사의 뜻(조동사 자체를 표시)"),
    toggleable=True,
)
HEDGE_EXCLUDE_RULE = Rule(
    "HEDGE_EXCLUDE", "hedge", "추측 제외(가능·허용)",
    HEDGE_EXCLUDE,
    "'~에 적용될 수 있다'처럼 가능·허용 뜻의 조동사는 추측으로 칠하지 않는다. 사이에 부사·부정어가 2개까지 끼어도 잡는다.",
    reason="가능·허용 뜻이라 추측 표시에서 뺐어요",
    examples=("may be applied", "could also be easily extended"),
    misses=("목록에 없는 동사(be evaluated 등)",),
    toggleable=True,
)

CONFIDENT = re.compile(
    r"\b(?:demonstrat(?:e|es|ed|ing)|clearly|significantly|substantially|dramatically|consistently"
    r"|prove[sdn]?|confirm(?:s|ed)?|undoubtedly|state[- ]of[- ]the[- ]art|outperform(?:s|ed|ing)?"
    r"|remarkabl[ey]|superior)\b",
    re.I,
)
CONFIDENT_RULE = Rule(
    "CONFIDENT", "confident", "확신",
    CONFIDENT,
    "결과를 강하게 주장하는 표현.",
    reason="저자가 강하게 주장하는 표현이에요. 근거(수치·표)를 함께 확인하세요",
    examples=("significantly improves", "We demonstrate", "state-of-the-art"),
    misses=("통계적 유의성과 단순 강조의 구분",),
    toggleable=True,
)

CITE_NUM = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]")
CITE_AUTHOR = re.compile(
    r"\((?:e\.g\.,?\s*)?[A-Z][A-Za-z'-]+(?:\s+et al\.|\s+(?:and|&)\s+[A-Z][A-Za-z'-]+)?,?\s+\d{4}[a-z]?"
    r"(?:;\s*[A-Z][A-Za-z'-]+(?:\s+et al\.|\s+(?:and|&)\s+[A-Z][A-Za-z'-]+)?,?\s+\d{4}[a-z]?)*\)"
)
XREF = re.compile(
    r"\b(?:Fig(?:ure)?s?|Tables?|Eqs?|Equations?|Sec(?:tion)?s?|Appendix|Alg(?:orithm)?)\.?\s*(?:\(\d+\)|[A-Z]?\d+(?:\.\d+)*)"
)
REFERENCE = re.compile(f"{CITE_NUM.pattern}|{CITE_AUTHOR.pattern}|{XREF.pattern}")
REFERENCE_RULE = Rule(
    "REFERENCE", "reference", "참조",
    REFERENCE,
    "번호식 인용 [12], 저자-연도 인용 (Smith et al., 2020), 그림·표·수식·절 참조 Fig. 3 / Table 2 / Eq. (4).",
    reason="다른 논문이나 그림·표를 가리키는 표시예요",
    examples=("[4]", "(Smith et al., 2021)", "Table 2", "Eq. (4)"),
    misses=("각주형 인용",),
    toggleable=True,
)

DEFINITION_CUE = re.compile(
    r"\b(?:refers? to|is defined as|are defined as|we define|(?:is|are) (?:called|termed|known as|denoted (?:as|by))"
    r"|(?:known|referred to) as)\b"
    r"|(?<=,\s)which\s+(?:is|are|aims? to|refers? to|consists? of|describes?|means|denotes|enables?|allows?"
    r"|uses?|captures?|measures?)\b"
    r"|(?<=\()(?:i\.e\.|e\.g\.),?"
    r"|\b(?:such as|including|for example|for instance|namely|that is,)(?=\s)"
    r"|\bi\.e\.,?(?=\s)",
    re.I,
)
"""용어 설명·예시 신호: 'refers to', 'is defined as', ', which is/aims to …', '(i.e., …)', 'such as', 'including'.

신호어에 형광펜을, 그 뒤의 설명 구간(쉼표·마침표·괄호 앞, 12단어 이내)에 점선 밑줄을 긋는다(extract.py).
샘플 300편 중 131편(44%)에서 잡힌다(2026-11-04).
"""

APPOSITIVE = re.compile(r"\b(?P<term>[A-Z][A-Za-z0-9]*(?:[A-Z0-9-][A-Za-z0-9]*)+)\s*,\s+(?P<art>an?|the)\b(?=[^,.;]{3,80},)")
"""동격 설명: 'GMM-LDA, a topic modeling based Bayesian model, …'. 대문자·숫자가 섞인 이름만 대상으로 해서
'Finally, a …' 같은 부사 오탐을 막는다. 관사부터 다음 쉼표까지가 설명이다."""

DEFINITION_RULE = Rule(
    "DEFINITION", "definition", "용어 설명·예시",
    DEFINITION_CUE,
    "앞에 나온 용어의 뜻을 풀거나 예를 드는 신호어. 신호어 뒤 설명 구간에는 점선 밑줄을 긋는다. 대문자 이름 뒤 동격('X, a …,')도 설명으로 본다.",
    reason="앞 용어의 뜻이나 예시를 알려 주는 부분이에요. 처음 보는 용어라면 여기부터 읽으세요",
    examples=("refers to", ", which aims to", "such as", "(i.e., …)", "GMM-LDA, a topic model,"),
    misses=("신호어 없는 설명", "소문자 용어의 동격"),
    toggleable=True,
)

EVIDENCE_NUMBER = re.compile(
    r"\b(?:accuracy|f1(?:-score)?|bleu|rouge(?:-\w+)?|precision|recall|auc|error rate|perplexity)\b"
    r"(?:\s+(?:of|=|is|was|score of))?\s*\d+(?:\.\d+)?\s?%?"
    r"|(?<![\w.])\d+(?:\.\d+)?\s?(?:%|percent\b|x\b|times\b|points?\b|-fold\b)",
    re.I,
)
NUMBER_RULE = Rule(
    "NUMBER", "number", "수치 근거",
    EVIDENCE_NUMBER,
    "결과를 뒷받침하는 수치: 백분율, 배수, 점수, '지표 + 숫자'(accuracy of 74%).",
    reason="주장을 뒷받침하는 수치예요. 무엇과 비교한 값인지 함께 확인하세요",
    examples=("by 8.4%", "accuracy of 74%", "2x faster", "F1-score of 77.63%"),
    misses=("단위 없는 숫자(데이터 개수 등)",),
    toggleable=True,
)

TERM_WORD = re.compile(r"[A-Za-z][A-Za-z-]{2,}")
"""반복 용어 후보 단어(3글자 이상)."""

TERM_MIN_COUNT = 3
"""이 글 안에서 3번 이상 나오면 반복 용어로 본다."""

TERM_STOPWORDS = frozenset("""
a an the and or but nor so yet for of in on at to from by with without within into onto over under about across after before
between through during against among is are was were be been being am do does did done have has had having can could may might
must shall should will would this that these those it its they them their there here which who whom whose what when where why how
than then also as such we our us you your he she his her not no only more most less least very much many some any each every both
either neither other another own same
""".split())
"""관사·전치사·접속사·대명사·조동사 등 기능어. 반복 용어에서 뺀다."""

TERM_GENERIC = frozenset("""
paper work study result approach method model task data performance system problem use used using propose proposed show shown
based new existing different two three four first one second various several large small high low however further well recent
recently moreover thus therefore while whether number amount main level set way time case part type kind order term able given
provide achieve improve effective effect significant important specific general previous current compute computed
dataset datasets
""".split())
"""어느 논문에나 자주 나오는 일반어. 한 단어로는 반복 용어로 보지 않는다(두 단어 용어의 뒷부분으로는 허용: 'language model')."""

TERM_RULE = Rule(
    "TERM", "term", "반복 용어",
    TERM_WORD,
    "이 글 안에서 3번 이상 나오는 용어. 두 단어 용어('language model')를 먼저 찾고, 남는 한 단어를 찾는다. "
    "기능어·일반어·-ed로 끝나는 한 단어(형용사·과거분사)는 뺀다. 복수형은 단수와 같은 용어로 센다.",
    reason="이 글에서 계속 나오는 핵심 용어예요. 뜻을 먼저 잡아 두면 나머지가 쉬워져요",
    examples=("language model", "sentence", "lexicon"),
    misses=("2번 이하로 나오는 용어", "철자가 바뀐 변형(-ing 형 등)"),
    toggleable=True,
)

TOKEN_RULES = (ABBR_RULE, HEDGE_RULE, HEDGE_EXCLUDE_RULE, CONFIDENT_RULE, REFERENCE_RULE, DEFINITION_RULE, NUMBER_RULE,
               TERM_RULE)

# ====================================================================== 3줄 뼈대 (P0-2)

SK_PROBLEM = re.compile(
    r"\A\s*(?:but|yet)\b"
    r"|\b(?:however|despite|although|unfortunately|challeng(?:e|es|ing)"
    r"|remains? (?:an? )?(?:open|unclear|difficult|unsolved)"
    r"|(?:key|major|main|central) (?:problem|issue|limitation|bottleneck)s?"
    r"|little (?:is known|attention|work)|lack(?:s|ing)? (?:of )?|struggles? to|suffer(?:s)? from"
    r"|is (?:costly|expensive|time-consuming)|existing (?:methods|approaches|work))\b",
    re.I,
)
SK_METHOD = re.compile(
    r"\b(?:in this (?:paper|work|study|article),? we"
    r"|we (?:propose|present|introduce|develop|design|describe|investigate|study)"
    r"|our (?:approach|method|model|framework|system|technique)"
    r"|this (?:paper|work) (?:proposes|presents|introduces)"
    r"|(?:is|are|was|were) (?:\w+ly )?(?:proposed|introduced|presented|developed))\b",
    re.I,
)
SK_RESULT = re.compile(
    r"\b(?:results? (?:show|indicate|demonstrate|suggest|reveal)|we (?:find|found|show|observe|demonstrate)"
    r"|experiments?\b[^.]{0,60}?\b(?:show|demonstrate|reveal)s?|outperform\w*"
    r"|(?:is|are|was|were) (?:\w+ly )?(?:observed|achieved|obtained)"
    r"|achiev\w* (?:an? )?(?:new )?(?:state[- ]of[- ]the[- ]art|improvement|accuracy|f1|bleu)"
    r"|improv\w* (?:by|over|upon))\b"
    r"|\d+(?:\.\d+)?\s?%",
    re.I,
)
SK_LIMIT = re.compile(
    r"\b(?:limitations?|future (?:work|research|directions?)|(?:does|do) not (?:generalize|address|consider)"
    r"|remains? to be|we leave|is limited to|only (?:applies|works)|broader impacts?|ethic(?:s|al|ally)"
    r"|open (?:question|problem)s?)\b",
    re.I,
)

SKELETON_RULES = (
    Rule("SK_PROBLEM", "problem", "문제 · Problem", SK_PROBLEM,
         "연구가 다루는 어려움·빈틈을 밝히는 신호 문구. 대조어(but, despite, although 등)도 포함한다.",
         reason="어려움을 직접 밝혀 연구 문제를 나타내는 표현",
         examples=("remains challenging", "However, ...", "existing methods struggle to")),
    Rule("SK_METHOD", "method", "방법 · Method", SK_METHOD,
         "저자가 한 일(제안 방법)을 알리는 신호 문구.",
         reason="새로운 접근법을 제안하는 방법 신호",
         examples=("we introduce", "In this paper, we", "our framework")),
    Rule("SK_RESULT", "result", "결과 · Result", SK_RESULT,
         "실험이 보여 준 것을 알리는 신호 문구와 수치.",
         reason="실험 결과를 알리는 표현이 포함된 문장",
         examples=("Experiments ... show", "outperforms", "by 8.4%")),
    Rule("SK_LIMIT", "limit", "한계", SK_LIMIT,
         "한계·향후 과제·윤리 문장. 3줄 뼈대의 결과 대체 후보에서 빼고 한계 후보로 보낸다.",
         reason="한계나 향후 과제를 밝히는 문장",
         examples=("future work", "we leave ... for", "broader impact")),
)

PUBLIC_RELEASE = re.compile(
    r"https?://|github"
    r"|\b(?:code|data(?:set)?s?|models?|resources?)\b[^.]{0,40}\b(?:is|are|will be)\s+"
    r"(?:publicly\s+|made\s+|freely\s+)?(?:available|released)"
    r"|\bwe (?:release|make)\b",
    re.I,
)
"""코드·데이터 공개 문장. 3줄 뼈대의 대체 문장으로 고르지 않는다."""

AUTHOR_VOICE = re.compile(r"\b(?:we|our)\b", re.I)
"""방법 대체: 'we/our'가 들어간 첫 문장."""

RESULT_HINT = re.compile(r"\d|\b(?:improv|achiev|outperform|reduc|increas|show|reveal|find|found|yield)\w*", re.I)
"""결과 대체: 수치나 결과 동사가 있는 문장을 우선한다."""

POSITION_FALLBACK_RULE = Rule(
    "SK_FALLBACK", "skeleton", "뼈대 위치 대체",
    AUTHOR_VOICE,
    "신호 문구가 없을 때만 위치로 대체한다. 문제 = 첫 문장('분야 배경 (첫 문장)'), "
    "방법 = we/our가 든 첫 문장, 결과 = 공개·한계 문장을 뺀 뒤쪽 문장 중 수치·결과 동사가 있는 문장.",
    reason="신호 문구가 없어 위치로 골랐어요(추정)",
    toggleable=True,
)

# ====================================================================== 핵심 내용 밑줄 (형광펜 = 신호, 밑줄 = 내용)

UNDERLINE_MAX_WORDS = 12
"""밑줄 상한. 샘플에서 '신호 뒤~문장 끝'은 평균 18.8단어였다(2026-11-04)."""

UL_REPORTING_END = re.compile(
    r"(?:show|shows|showed|find|finds|found|observe|observed|demonstrate|demonstrates|demonstrated"
    r"|reveal|reveals|indicate|indicates|suggest|suggests)\s*$", re.I)
"""신호 문구가 보고 동사로 끝나면 그 뒤(that절)가 내용이다."""

UL_CONTRAST_START = re.compile(r"^\s*(?:however|but|yet|unfortunately)\b", re.I)
"""대조어 신호면 그 뒤가 내용이다."""

UL_CONCESSION_START = re.compile(r"^\s*(?:although|despite|while|though|even though)\b", re.I)
"""양보절로 시작하면 주절(쉼표 뒤)이 내용이다."""

UL_PASSIVE = re.compile(r"^(?:is|are|was|were)\b", re.I)
"""수동태 신호('is introduced', 'were observed')면 그 앞의 주어가 내용이다."""

UL_SUBJECT_COMMA = re.compile(r",\s+(?=(?:a|an|the|our|this|these|those|its|their|they|we|it|such|existing|current|most|many)\b)", re.I)
"""쉼표가 여럿일 때 주어가 시작되는 쉼표를 고른다(삽입절·목록 쉼표 대신)."""

UL_TRAILING_FUNCTION = re.compile(r"(?:\s+(?:the|a|an|of|with|to|and|or|for|in|on|by|at|from|than|compared|as))+\s*$", re.I)
"""단어 상한에서 잘린 밑줄 끝의 기능어를 떼어 낸다."""

UL_LEADING_SKIP = re.compile(r"^[\s,]*(?:that\b)?[\s,]*", re.I)
"""밑줄 시작에서 'that'과 쉼표를 건너뛴다."""

UL_STOP = re.compile(r";|\s\(|,?\s(?:while|whereas)\b")
"""밑줄을 멈추는 경계. 'to'와 'that'(방법의 목적·기능)과 일반 쉼표에서는 멈추지 않는다."""

UL_STOP_WHICH = re.compile(r",\s(?:which|where)\b")
"""문제·결과 문장에서만 쓰는 추가 경계(덧붙인 설명절)."""

UL_CLAUSE_START = re.compile(r"(?:^|[,;:]\s+(?:but\s+|and\s+)?|\s(?:but|and)\s)")
"""신호가 내용 안에 있는 경우('... remains challenging') 그 절이 시작되는 위치를 찾는다."""

KO_TRAIL_PARTICLE = re.compile(r"(?<=[가-힣])(?:을|를|에서|으로)$")
"""한국어 밑줄 끝의 조사를 밑줄 밖으로 뺀다. 명사 끝 글자와 헷갈리는 과·가·이·의(결과·평가·차이·정의)와
동사·형용사 어미와 헷갈리는 은·는(높은·향상시키는)은 일부러 뺐다."""

KO_WORD_REST = re.compile(r"[가-힣A-Za-z0-9%]*")
"""DeepL이 밑줄 태그를 단어 중간('향상시키|는')에서 닫으면 단어 끝까지 늘린다."""

KO_NOUN_PARTICLES = frozenset({"가", "이", "을", "를", "의", "에", "와", "과", "도", "로", "만", "에서", "으로", "에게", "인", "이며", "이자"})
"""남은 조각이 이 조사들이면 늘리지 않는다('평가|가'는 '평가'에서 멈춤)."""

TOGGLEABLE_RULES = tuple(r for r in (*TOKEN_RULES, POSITION_FALLBACK_RULE) if r.toggleable)
DEFAULT_ACTIVE_RULES = frozenset(r.name for r in TOGGLEABLE_RULES)

# ====================================================================== 번역 보호·후처리 (P0-5)

NUMBER = re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?:\s?%|x\b)?")
"""수치: ``8.4%``, ``1,000``, ``3x``. 번역 보호에는 쓰지 않는다(아래 참고)."""

PROTECT_PATTERNS = (URL, MATH, CITE_NUM, CITE_AUTHOR, XREF)
"""번역에서 보호할 토큰(앞에 있을수록 우선). 약어는 문서마다 달라서 translate.py가 앞에 붙인다.

수치(NUMBER)는 2026-11-04 마스킹 30편 실측 뒤 뺐다. 수치를 <x>로 가리자 DeepL이 한 문장 안의
'우리 방법 72.3%'와 '기존 방법 67.9%'를 구분하지 못하고 앞 수치를 두 번 써서 틀린 숫자를 만들었다
(1812.10896). 마스킹을 끄면 수치는 '2,000', '1,000만'처럼 표기만 바뀌고 값은 맞았다.
"""

X_TAG = re.compile(r"</?(?:x|m|u)\b[^>]*>")
"""보호 태그 <x>, 형광펜 <m>, 밑줄 <u>를 모두 지운다. 짝이 맞지 않는 태그도 지워져 번역문에 글자로 남지 않는다."""

UL_TAG = re.compile(r'<u c="(?P<c>[\w-]+)" i="(?P<i>\d+)">(?P<body>.*?)</u>', re.S)
"""번역 결과에서 밑줄 위치를 복원하는 태그. 안에 <x>·<m>이 들어 있어도 된다."""

MARK_TAG = re.compile(r'<(?P<t>x|m) c="(?P<c>[\w-]+)" i="(?P<i>\d+)">(?P<body>.*?)</(?P=t)>', re.S)
"""번역 결과에서 형광펜 위치를 복원하는 태그(범주 c, 영어 표시 번호 i)."""
SENT_TAG = re.compile(r"<s>(.*?)</s>", re.S)
"""문단 번역에서 문장 짝을 복원하는 태그."""

KO_JE_BEFORE_REF = re.compile(r"제\s?(?=(?:Sec(?:tion)?|Table|Fig(?:ure)?|Eq|Appendix|Alg)\b)")
"""DeepL이 보호된 참조 앞에 붙이는 '제'(예: '제Section 5.1') 제거."""

KO_PARTICLE_SPACE = re.compile(r"(?<=[A-Za-z0-9%)\]])[ \t]+(?=(?:은|는|가|을|를|의|에|와|과|도|로)(?=[\s.,)]|$))")
"""영문·숫자·괄호 뒤의 공백 + 조사 → 공백 제거. '이'는 지시어('이 방법')와 겹쳐 목록에서 뺐다."""

KO_SPACE_BEFORE_CITE = re.compile(r"(?<=[가-힣])[ \t]+(?=\[\d)")
"""한국어 뒤 번호식 인용 앞 공백 제거: '수 있다 [4]' → '수 있다[4]'."""

# ====================================================================== 입력 (collect)

ARXIV_ID = re.compile(
    r"^\s*(?:arxiv:\s*)?(?P<id>(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v\d+)?)\s*$",
    re.I,
)
"""arXiv ID: 새 형식 ``2402.08910``(``v2``, ``arXiv:`` 접두 허용), 옛 형식 ``cs/0112017``."""

# ====================================================================== 논문 PDF 절 나누기 (sections)

SEC_NUM_HEAD = re.compile(
    r"^(?P<num>(?P<top>\d{1,2}|[IVX]{1,5})(?P<sub>(?:\.\d{1,2}){0,2})\.?)\s+"
    r"(?P<title>[A-Z][A-Za-z0-9\-–’' ,:&()/]{1,80}?)(?P<dot>\.)?$"
)
"""번호가 붙은 절 제목 줄: ``1 Introduction``, ``3.2 Pre-training BERT``, ``1. Introduction``, ``II. RELATED WORK``.
제목에는 마침표가 없어야 한다(문장·참고문헌 줄과 구별). 다만 ``6.2 Qualitative Feedback.``처럼 소절 번호(점이 있는
번호)가 붙은 제목은 끝 마침표 하나를 허용한다(ACM 양식). 표의 숫자 줄과 헷갈리지 않도록 절 번호가 1, 2, 3…
차례로 이어질 때만 받아들인다(src/sections.py)."""

SEC_NAMED_HEAD = re.compile(
    r"^(?P<title>Abstract|Introduction|Related\s+Work|Background|Conclusions?|Discussion|Limitations|"
    r"Acknowledg(?:e)?ments?|References|Bibliography)[.:]?$",
    re.I,
)
"""번호 없이 이름만 있는 절 제목 줄(대문자로만 쓴 ``ABSTRACT``도 허용)."""

SEC_END = re.compile(r"^(?:References|Bibliography|Further\s+Reading|Works\s+Cited)$", re.I)
"""여기부터는 참고문헌이라 분석하지 않는다(부록도 함께 뺀다). 백과사전·인문학 글은 Further Reading·Works Cited."""

SEC_FONT_HEAD = re.compile(r"^(?P<title>[A-Z][A-Za-z'’-]*(?:(?:,?\s+|:\s+)(?:[A-Z]\.|[A-Za-z][A-Za-z'’-]*))*)$")
"""번호 없는 절 제목(제목 글꼴 줄일 때만 대 본다): 대문자로 시작하고, 글자 낱말로만 되어 있고(숫자 없음) 문장부호로
끝나지 않는다. ``Space in the Philosophical Tradition``, ``Development of the protocol``(첫 낱말만 대문자),
``Analysis: A Contemporary Focus on Ibram X. Kendi``(콜론·머리글자). 워터마크처럼 여러 쪽에 반복되는 줄은
pdf_text가 제목 후보에서 미리 뺀다."""

PROTO_SECTION = re.compile(r"^(?:\d+\s+)?(?:Procedure|Materials|Reagents|Equipment|Reagent\s+setup|Equipment\s+setup|"
                           r"Timing|Troubleshooting|Protocol)\b", re.I)
"""실험 절차 절 제목(Nature Protocols 등). 이 절에서는 3줄 뼈대 대신 절차 요점 카드를 보여 준다."""

PROTO_CAUTION = re.compile(r"\bCRITICAL(?:\s+STEP)?\b|!\s*CAUTION\b|\bCAUTION\b")
PROTO_PAUSE = re.compile(r"\bPAUSE\s+POINT\b")
PROTO_TIMING = re.compile(r"\bTiming\b|~\s?\d+(?:\.\d+)?\s?(?:min|h|d)\b")
PROTO_STEP = re.compile(r"(?:^|(?<=[.!?)]\s))(?P<n>\d{1,2})\s+(?=[A-Z][a-z]|\()", re.M)
"""프로토콜 원문에 정해진 모양으로 들어 있는 표시: 주의(``CRITICAL STEP``·``! CAUTION``), 멈춤 지점(``PAUSE POINT``),
소요 시간(``Timing 5–8 h``·``~4 d``), 번호 단계(``7 Oral phase …``: 줄 첫머리나 문장 끝 바로 뒤의 번호).
번호 단계는 문장 분리가 단계 사이를 나누지 않는 경우가 있어 문장이 아니라 글 전체에서 센다."""

PROTO_MARK_WORDS = frozenset({"CAUTION", "CRITICAL", "STEP", "PAUSE", "POINT", "TROUBLESHOOTING", "TIMING", "PROCEDURE"})
"""프로토콜 원문의 대문자 표시 낱말: 약어 후보에서 뺀다('CAUTION: 이 글에서 정의를 찾지 못함'이 되지 않게)."""

CATALOG_PAREN = re.compile(r"\((?:[^()]*?,\s*)?cat\.\s*no\.[^()]*\)")
"""시약·제품 품번 괄호: ``(Sigma-Aldrich, cat. no. B3883)``·``(Merck, cat. no. 9141)``. 실험 절차 절의 '시약·제품' 카드와
본문에서 품번을 흐리게 보여 줄 때 쓴다(원문은 지우지 않는다)."""

FALLBACK_TITLE = re.compile(r"^(?:\d+\s+)?(?:Abstract|Introduction|Background)\b", re.I)
"""위치로 대신 고르는 뼈대 카드(첫 문장=배경 등)는 초록·서론·결론에서만 의미가 있다. 본문 절에서는 이 제목일 때만 쓴다."""

HEAD_NOTE = re.compile(r"\s*[●•]?\s*Timing\s+\S.*$")
"""제목 줄 뒤에 붙은 소요 시간 안내(Nature Protocols): ``Digestion procedure ● Timing 5–8 h``. 제목은 앞부분만 보고,
안내는 그 절 본문으로 남긴다."""

SEC_STD_TOP = re.compile(
    r"^(?:Abstract|Introduction|Background|Literature\s+Review|Related\s+Work|Methods?|Methodology|"
    r"Materials\s+and\s+Methods|Data(?:\s+and\s+Methods)?|Results|Findings|Analysis|Discussion|"
    r"Conclusions?|Limitations)(?=:|$)",
    re.I,
)
"""논문에서 흔한 절 이름(뒤에 콜론으로 부제가 붙어도 됨). 굵은 제목이 모두 같은 크기라 단계를 알 수 없을 때
이 이름으로 시작하는 제목은 절, 나머지(``Setting for Child One``)는 그 절의 소제목으로 본다."""

PDF_NOISE_PARA = re.compile(
    r"^(?:[∗*†‡§¶]|\(cid:\d+\)|\d{1,2}\s+(?:https?://|www\.|[A-Z])|"    # 각주(기호, 번호+주소, 번호+문장)
    r"(?:Figure|Fig\.|Table)\s*\d+\s*[:.|]|"                      # 그림·표 설명
    r"\S+@\S+)"                                                    # 이메일 줄
)
"""본문이 아닌 문단의 첫머리: 각주, 그림·표 설명(캡션), 이메일 줄."""

PDF_CID = re.compile(r"\(cid:\d+\)")
"""PDF 글꼴에서 글자로 바꾸지 못한 기호 자리표시(예: ``(cid:63)``)."""

PDF_WORDLIKE = re.compile(r"[A-Za-z]{3,}")
"""표·수식 조각을 거르는 데 쓰는 '세 글자 이상 영어 낱말'."""

SEC_TITLE_FUNCTION = re.compile(r"\b(?:the|a|an|that|this|these|we|our|is|are|was|were|in|of|to|for|with|and|by|on)\b", re.I)
"""절 제목 후보 안의 기능어. 4개 이상이면 제목이 아니라 문장(예: 번호 각주 ``4 We note that in the literature the ...``)."""

SEC_INLINE_ABSTRACT = re.compile(r"^(?:Abstract|ABSTRACT)\s*[—–:.\-]\s*(?P<rest>\S.*)$")
"""IEEE 형식처럼 같은 줄에서 바로 시작하는 초록: ``Abstract—State-of-the-art object detection ...``."""

SMALL_CAPS_HEAD = re.compile(r"\b([A-Z]) (?=[A-Z]{2,}\b)")
"""작은 대문자 글꼴 제목에서 첫 글자만 떨어진 것: ``R EFERENCES`` → ``REFERENCES``, ``1 I NTRODUCTION``."""

EMAIL = re.compile(r"(?:\{[\w.,\s-]+\}|[\w.+-]+)@[\w-]+(?:\.[\w-]+)+")
"""이메일 주소(``{kahe,jiansun}@microsoft.com`` 묶음 형식 포함). 본문에서 지운다."""

PDF_FRONT_MATTER = re.compile(
    r"(?:\bis\s+(?:now\s+)?with\s+(?:the\s+)?[A-Z]|\bE-?mail\s*:|Corresponding\s+author|Manuscript\s+received|"
    r"Index\s+Terms\s*[—–:-]|^Keywords?\s*[—–:.-]|Published\s+as\s+a\s+conference\s+paper|"
    r"©|\bCopyright\b|\bPreprint\b|\bProceedings\s+of\b|\barXiv:\d{4}\.\d{4,5}|\bdoi\.org/|\bDOI\s*:|ORCID|"
    r"Equal\s+contribution|Work\s+(?:was\s+)?(?:performed|done)\s+(?:while|when|during)|"
    r"\b(?:Conference|Workshop|Symposium)\s+on\b[^.]{0,80}\b(?:19|20)\d\d\b)",
    re.I | re.M,
)
"""본문이 아닌 앞머리·각주 표시: 소속(``S. Ren is with University ...``), 이메일 표시, 교신저자, 투고일,
Index Terms·Keywords, 학회·저작권·DOI·arXiv 번호. 이것이 든 짧은 문단(800자 미만)은 통째로 뺀다."""

SEC_CONCLUSION_TITLE = re.compile(r"\b(?:Conclusions?|Concluding|Discussion|Limitations?|Future\s+Work|Summary)\b", re.I)
"""'결론' 쪽에 모을 절 제목(Conclusion, Discussion, Limitations, Future Work). 나머지 번호 절은 '본문'."""

SEC_ACK_TITLE = re.compile(r"^Acknowledg", re.I)
"""감사의 글: 독해에 필요 없어 쪽 나누기에서 뺀다."""

# ====================================================================== 논문 PDF 표 (tables)

TABLE_CAPTION = re.compile(r"^(?:Table|TABLE)\s+(?P<num>\d{1,2})\s*[:.|]\s*\S")
"""표 설명(캡션) 첫 줄: ``Table 3: SQuAD 2.0 results.``, ``Table 1. Architectures ...``, ``TABLE 2: ...``."""

FIGURE_CAPTION = re.compile(r"^(?:Figure|Fig\.|FIGURE)\s*\d{1,2}\s*[:.|]")
"""그림 설명 첫 줄. 표 영역을 넓혀 가다가 이것을 만나면 멈춘다."""

FIGURE_CAPTION_NUM = re.compile(r"^(?:Figure|Fig\.|FIGURE)\s*(?P<num>\d{1,2})\s*[:.|]\s*\S")
"""그림 설명(캡션) 첫 줄과 번호: ``Figure 1: The Transformer - model architecture.``, ``Fig. 2. ...``."""

FIGURE_MENTION = re.compile(r"\b(?:Figures?|Figs?\.)\s*\d{1,2}(?:\s*(?:,|and|&|–|-)\s*\d{1,2})*\b")
"""본문에서 그림을 가리키는 말: ``Figure 3``, ``Fig. 2``, ``Figures 1 and 4``. 그림을 그 문장 아래에 끼우는 데 쓴다."""

TABLE_MENTION = re.compile(r"\b(?:Tables?|Tab\.)\s+\d{1,2}(?:\s*(?:,|and|&|–|-)\s*\d{1,2})*\b")
"""본문에서 표를 가리키는 말: ``Table 3``, ``Tables 2 and 4``, ``Tab. 5``. 화면에서 그 표를 바로 보여 주는 데 쓴다."""

DEHYPHEN_SPACE = re.compile(r"(?<=[a-z])- (?=[a-z])")
"""캡션처럼 줄을 공백으로 이은 글에서 줄 끝 하이픈 지우기: ``Human per- formance`` → ``Human performance``."""

# ====================================================================== 단어표 전문용어 기준 (glossary)

GLOSS_INFLECTION = re.compile(r"(?:ions?|ings?|ed|es|(?<![siu])s|e)$")
"""단어표에서 같은 낱말의 어형을 묶을 때 떼는 꼬리: annotate·annotating·annotated·annotation → annotat.
analysis·virus·class처럼 s로 끝나는 원형은 s를 떼지 않는다."""

JARGON_PREFIX = re.compile(r"^(?:multi|inter|under|semi|over|uni|pre|non|sub|dis|mis|out|un|re|bi|de|co)(?=[a-z]{4,})")
"""흔한 말에 붙는 접두사(긴 것부터: uni를 un보다 먼저). 떼고 남은 말이 흔하면(unlabeled → labeled) 전문용어로 보지 않는다."""

JARGON_SUFFIX = re.compile(r"(?:ally|ness|ities|ity|izations?|ized?|izes|ations?|able|wards?|al|ed|ing|ers?|s)$")
"""흔한 말에 붙는 접미사(bidirectional → bidirection → direction 처럼 두 번까지 뗀다)."""

CITE_NARRATIVE = re.compile(
    r"\b[A-Z][A-Za-z'-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'-]+)?,?\s*\(?\d{4}[a-z]?\)?"
    r"|\b[A-Z][A-Za-z'-]+\s+et\s+al\.?"
)
"""문장 속 인용: ``Vaswani et al. (2017)``, ``Dolan and Brockett, 2005``, ``Radford et al.``. 단어표 후보에서 가린다."""

CITE_CUE = re.compile(r"\s*(?:et\s+al\b|(?:and|&)\s+[A-Z][A-Za-z'-]+\s*(?:\[|\(|,?\s*\d{4})|\[\d|\(\d{4}|,\s*\d{4}[a-z]?\b|'s\s+(?:work|method|model))")
"""낱말 바로 뒤의 인용 표시: ``McCann et al.``, ``LeCun and Bengio (1995)``, ``Smith [12]``, ``Dolan, 2005``.
단어표 후보가 이렇게 쓰이면 사람(저자) 이름으로 보고 뺀다."""

ORG_SUFFIX = re.compile(r"^(?:GmbH|Inc|Ltd|Corp|LLC|AG|SA|Co)\.?$")
"""회사 이름 꼬리말. 단어표에 넣지 않는다."""

MODEL_CODE = re.compile(r"^[A-Z]\d+[a-z]?\d*(?:x\d+)?$|^[A-Z]\d*x$")
"""한 글자 + 숫자로 된 모델 변형 기호(R50x1, R152x2, I21k, W1x). 뜻을 알 수 없는 기호라 단어표에서 뺀다."""
