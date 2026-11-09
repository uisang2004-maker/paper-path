"""PaperPath — 처음 읽는 영어 논문 도우미 (Streamlit).

실행: .venv/Scripts/python.exe -m streamlit run app.py
외형은 docs/design/paperpath-v03, 기능은 docs/PLAN.md를 따른다.
"""

import base64
import hashlib
import html
import io
import json
import os
import re
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from src import patterns as P
from src import translate as T
from src import usage
from src.analyze import load_benchmark, skeleton_stats
from src.collect import ArxivError, fetch_abstract, load_samples
from src import glossary as G
from src.explain import MAX_TERMS, explain_terms
from src.extract import ROLE_ORDER, analyze
from src.pdf_text import PdfError, extract as extract_pdf
from src.sections import PAGES as PAPER_PAGES, Paper, Section, paper_pages, split_paper
from src.tables import find_figures, find_tables, first_mentions
from src import saved as SV
from src import store as S
from src import quota as Q

# 브라우저 저장(localStorage) 컴포넌트: components/browser_store/index.html. 화면에는 보이지 않는다
_browser_store = components.declare_component(
    "browser_store", path=str(Path(__file__).resolve().parent / "components" / "browser_store"))

PAGES = ("논문 읽기", "규칙 탐색", "검증 결과")
MODES = ("논문 PDF", "arXiv ID", "텍스트 붙여 넣기")
M_PDF, M_ARXIV, M_PASTE = MODES
MAX_PASTE = 20000
MAX_PDF_MB = 20
CHIPS = (("abbr", "약어", "blue"), ("hedge", "추측·가능성", "amber"), ("confident", "확신", "teal"),
         ("reference", "참조", "violet"), ("definition", "용어 설명", "blue"), ("term", "반복 용어", "blue"),
         ("number", "수치", "teal"), ("jargon", "어려운 단어", "gray"))
CHIP_RULE = {"abbr": "ABBR", "hedge": "HEDGE", "confident": "CONFIDENT", "reference": "REFERENCE",
             "definition": "DEFINITION", "term": "TERM", "number": "NUMBER", "jargon": "JARGON"}     # 칩 → 켜고 끌 규칙
CHIP_COLOR = {"blue": "blue", "amber": "orange", "teal": "green", "violet": "violet", "gray": "gray"}
JARGON_RULE = "JARGON"                # 단어표의 어려운 단어(wordfreq 기준)를 본문에 점선으로. 정규식 규칙이 아니라 화면 표시 켜고 끄기
SK_CLAMP_CHARS = 200                 # 뼈대 카드 문장이 이보다 길면 4줄까지만 보이고 '전체 보기'
GLOSS_PREVIEW = 8                    # 단어표는 처음 8개만 보이고 나머지는 '전체 단어표 보기'로
TIMES = re.compile(r"(\d+)번")      # 반복 용어 표시 설명에서 등장 횟수
READ_MODES = ("side", "pair", "en", "ko")      # 읽기 모드: 한영 나란히 / 문장마다 한영 / 원문만 / 한국어만
READ_MODE_LABEL = {"side": "한영 나란히", "pair": "문장마다 한영", "en": "영어 원문만", "ko": "한국어만"}

# 화면 예시용으로 직접 작성한 문단(실제 논문 아님). v03 시안과 같은 문장이다.
DEMO_TEXT = (
    "Retrieval-augmented generation (RAG) can ground language models in external documents, but selecting "
    "reliable evidence remains challenging. To address this issue, we introduce a lightweight reranking module "
    "that scores retrieved passages using contextual relevance. Experiments on three public benchmarks show that "
    "the proposed module significantly improves retrieval precision by 8.4% compared with the baseline. These "
    "findings suggest that a compact reranker may help smaller models use evidence more consistently [4]."
)
RULE_DEMO = "The method may be applied to images, and the results suggest a gain over BERT [4]."

KIND_META = {
    "problem": ("문제 · Problem", "blue"),
    "method": ("방법 · Method", "teal"),
    "result": ("결과 · Result", "violet"),
}
BASIS_TEXT = {"signal": "신호 문구로 찾음", "position": "위치로 추정", "none": "찾지 못함"}
MARK_LABEL = {"abbr": "약어", "abbr-missing": "약어(정의 없음)", "hedge": "추측·가능성", "confident": "확신", "reference": "참조",
              "definition": "용어 설명·예시", "number": "수치 근거", "term": "반복 용어", "jargon": "어려운 단어"}
CAT_RULE = {"abbr": "ABBR", "abbr-missing": "ABBR", "hedge": "HEDGE", "confident": "CONFIDENT", "reference": "REFERENCE",
            "definition": "DEFINITION", "number": "NUMBER", "term": "TERM", "jargon": "JARGON"}
UL_TITLE = {"problem": "핵심 내용(밑줄) · 무엇이 어려운가", "method": "핵심 내용(밑줄) · 무엇을 만들었나",
            "result": "핵심 내용(밑줄) · 무엇을 발견했나", "def": "용어 설명(점선 밑줄)"}

ICON = {
    "doc": '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/></svg>',
    "search": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="6"/><path d="m20 20-4-4M11 8v6M8 11h6"/></svg>',
    "shield": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    "info": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>',
    "list": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 7h14M5 12h9M5 17h11"/><circle cx="18" cy="17" r="2"/></svg>',
    "rule": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m4 18 5-12 4 9 3-5 4 8"/></svg>',
}

CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
:root {
  --ink:#14263e; --muted:#52677f; --subtle:#66798b; --line:#e2e9f2; --surface:#ffffff; --canvas:#f6f8fc;
  --navy:#152c49; --blue:#3979b5; --blue-soft:#eaf3fc; --teal:#21877f; --teal-soft:#eaf7f4;
  --amber:#9a681d; --amber-soft:#fff7e8; --violet:#7156a2; --violet-soft:#f3effa;
  --radius:14px; --shadow:0 4px 18px rgba(26,54,83,.045), 0 1px 2px rgba(25,49,77,.03);
}
html, body, [data-testid="stAppViewContainer"], .stApp {
  background: radial-gradient(780px 300px at 88% -8%, rgba(216,232,247,.46), transparent 70%),
              linear-gradient(180deg,#f8fafd 0%,#f4f7fb 100%) !important;
  color: var(--ink);
  font-family: Pretendard, "Malgun Gothic", "Segoe UI", sans-serif;
}
.stApp * { font-family: Pretendard, "Malgun Gothic", "Segoe UI", sans-serif; }
.stApp [data-testid="stIconMaterial"], .stApp [data-testid="stIconMaterial"] * { font-family: "Material Symbols Rounded" !important; }
code, pre, .pp-code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace !important; }
header[data-testid="stHeader"], #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
.block-container { max-width: 1320px !important; padding: 0 16px 40px !important; }
[data-testid="stVerticalBlock"] { gap: .75rem; }

/* 상단 바 */
.stApp, [data-testid="stMain"], [data-testid="stAppViewContainer"] { overflow-x: hidden; }
.st-key-topbar { position: relative; padding: 10px 0; margin-bottom: 6px; }
.st-key-topbar::before { content:""; position:absolute; top:0; bottom:0; left:-100vw; right:-100vw; z-index:0;
  background: rgba(255,255,255,.94); border-bottom: 1px solid #e4eaf2; box-shadow: 0 2px 12px rgba(22,46,74,.025); }
.st-key-topbar > div { position: relative; z-index: 1; }
.pp-brand { display:flex; align-items:center; gap:12px; min-height:46px; }
.pp-mark { width:38px; height:38px; border-radius:12px; display:grid; place-items:center; color:#fff;
  background: linear-gradient(145deg,#214d75 0%,#142f4f 100%); box-shadow:0 5px 12px rgba(24,59,96,.18); flex:none; }
.pp-name { font-size:16px; font-weight:750; letter-spacing:-.45px; color:#172c46; line-height:1.1; }
.pp-sub { color:#5d7286; font-size:11.5px; margin-top:3px; }
.st-key-nav [role="radiogroup"] { gap: 6px; flex-wrap: wrap; }
.st-key-nav [role="radiogroup"] label { padding: 8px 14px; border-radius: 10px; margin: 0; cursor: pointer; }
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child:not([data-testid]) { display:none !important; }
.st-key-nav [data-testid="stRadioOption"] p { font-size:14px; font-weight:650; color:#5d7286; }
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] { background:#eef4fa; box-shadow: inset 0 -3px 0 #2d78a8; }
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p { color:#1b456d; font-weight:750; }
.st-key-nav [data-testid="stRadioOption"]:focus-within { outline: 2px solid #5c9ac0; outline-offset: 2px; }
.pp-status { display:flex; justify-content:flex-end; align-items:center; gap:12px; min-height:46px; color:#5d7286; font-size:12.5px; }
.pp-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:6px; background:#36a28d; }
.pp-dot.err { background:#c2410c; }
.pp-avatar { width:32px; height:32px; border-radius:50%; background:#edf2f7; display:grid; place-items:center; color:#60758b; font-size:11px; font-weight:750; }

/* 첫 화면 히어로: 왼쪽 소개, 오른쪽은 배경 이미지(지구본·A↔文·논문) */
.pp-hero { position:relative; overflow:hidden; border-radius:18px; margin:6px 0 4px; min-height:300px;
  background:#eaf3fc url('app/static/hero.jpg') right center / cover no-repeat;
  border:1px solid #dbe7f4; box-shadow:0 10px 30px rgba(21,44,73,.08); }
.pp-hero::before { content:""; position:absolute; inset:0;
  background:linear-gradient(90deg, rgba(255,255,255,.96) 0%, rgba(255,255,255,.88) 34%, rgba(234,243,252,.25) 62%, rgba(234,243,252,0) 100%); }
.pp-hero-text { position:relative; z-index:1; max-width:560px; padding:44px 40px 40px; }
.pp-hero .pp-eyebrow { color:#3979b5; }
.pp-hero-h1 { margin:0; font-size:38px; line-height:1.22; letter-spacing:-1.4px; font-weight:800; color:#152c49;
  word-break:keep-all; }
.pp-hero-desc { font-size:16px; color:#3e5672; margin-top:14px; line-height:1.6; }
.pp-hero-badges { display:flex; gap:8px; flex-wrap:wrap; margin-top:20px; }
.pp-hero-badge { display:inline-flex; align-items:center; gap:6px; padding:7px 13px; border-radius:999px; font-size:12.5px;
  font-weight:750; color:#fff; background:linear-gradient(135deg,#3979b5,#152c49); box-shadow:0 4px 12px rgba(57,121,181,.28); }
.pp-hero-badge.soft { color:#152c49; background:rgba(255,255,255,.85); border:1px solid #cfe0f1; box-shadow:none; }
@media (max-width: 900px) {
  .pp-hero { min-height:0; background-position:78% center; }
  .pp-hero::before { background:linear-gradient(180deg, rgba(255,255,255,.95) 0%, rgba(255,255,255,.86) 100%); }
  .pp-hero-text { padding:26px 20px 24px; }
  .pp-hero-h1 { font-size:25px; letter-spacing:-.8px; }
  .pp-hero-desc { font-size:14.5px; word-break:keep-all; }
}

/* 소개 */
.pp-intro { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-top:6px; }
.pp-eyebrow { color:#68839a; font-size:11px; font-weight:800; letter-spacing:1.6px; margin-bottom:6px; }
.pp-h1 { margin:0; font-size:26px; line-height:1.25; letter-spacing:-.9px; font-weight:780; color:var(--ink); }
.pp-desc { font-size:14px; color:var(--muted); margin-top:6px; }
.pp-flag { display:flex; align-items:center; gap:6px; padding:8px 12px; border:1px solid #dfe8ef; border-radius:9px;
  background:#fbfdff; color:#596f84; font-size:12.5px; }

/* 패널 공통 */
.st-key-input_panel, .st-key-ko_panel, .st-key-ev_abbr, .st-key-ev_rule, .st-key-steps_panel, .st-key-rule_panel {
  background: var(--surface); border:1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px 16px; }
.st-key-input_panel { border-left: 3px solid #4f86ad; }
.pp-panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); }
.pp-input-copy { display:flex; gap:12px; align-items:center; }
.pp-lead { width:32px; height:32px; border-radius:9px; background:#eef4f8; color:#53738d; display:grid; place-items:center; flex:none; }
.pp-field-label { color:#354a61; font-weight:750; font-size:14px; }
.pp-field-hint { color:#607489; font-size:12px; margin-top:2px; }
.pp-privacy { display:flex; gap:8px; align-items:flex-start; color:#5d7285; font-size:12px; line-height:1.55; border-left:1px solid var(--line); padding-left:14px; }
[data-testid="stBaseButton-primary"] { background: var(--navy) !important; border-color: var(--navy) !important; border-radius:10px !important; font-weight:700 !important; min-height:44px; }
[data-testid="stBaseButton-secondary"] { border-radius:9px !important; border-color:#dce5ed !important; color:#536b82 !important; font-weight:650 !important; }
.stTextInput input, .stTextArea textarea { border-radius:10px !important; background:#fcfdff !important; }

/* 섹션 머리 */
.pp-sec { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-top:10px; }
.pp-sec-title { display:flex; align-items:center; gap:9px; font-size:17px; font-weight:780; letter-spacing:-.3px; color:var(--ink); }
.pp-sec-num { width:24px; height:24px; display:grid; place-items:center; border-radius:7px; background:#edf3f8; color:#54738d; font-size:11px; font-weight:800; }
.pp-sec-note { color:#60758a; font-size:12px; }

/* 3줄 뼈대 */
.pp-card { position:relative; overflow:hidden; padding:15px 17px 14px 19px; min-height:150px; height:100%; }
.pp-card:before { content:""; position:absolute; top:0; left:0; bottom:0; width:3px; background:#6c9dbb; }
.pp-card.teal:before { background:#3a9c92; } .pp-card.violet:before { background:#8b79b5; }
.pp-card-head { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:9px; }
.pp-kind { display:flex; align-items:center; gap:7px; font-size:13px; font-weight:780; color:#36536d; }
.pp-kdot { width:8px; height:8px; border-radius:50%; background:#6c9dbb; }
.teal .pp-kdot { background:#3a9c92; } .violet .pp-kdot { background:#8b79b5; }
.pp-basis { color:#60758a; font-size:11.5px; white-space:nowrap; }
.pp-basis.position { color:#9a681d; } .pp-basis.none { color:#8b98a9; }
.pp-card.est .pp-sk-text { color:#22364d; font-weight:560; }   /* 위치 추정 카드: 형광펜·밑줄이 없어 흐려 보이지 않게 */
.pp-sk-text { font-size:14px; color:#40536a; line-height:1.65; letter-spacing:-.1px; }
.pp-sk-no { color:#8090a1; font-size:11.5px; font-weight:750; margin-right:6px; }
.pp-sk-missing { font-size:14px; color:#8b98a9; }
.pp-sig-blue { background:#e7f1fb; color:#205d8b; font-weight:750; border-radius:4px; padding:1px 3px; }
.pp-sig-teal { background:#e5f5f1; color:#18796f; font-weight:750; border-radius:4px; padding:1px 3px; }
.pp-sig-violet { background:#f0ebf8; color:#7456a2; font-weight:750; border-radius:4px; padding:1px 3px; }
.pp-sk-ko { margin-top:8px; padding-top:8px; border-top:1px dashed #e3e9f0; font-size:13.5px; color:#4a5e73; line-height:1.65; }
.pp-u { text-decoration-line: underline; text-decoration-thickness: 2px; text-underline-offset: 4px; text-decoration-color:#6c9dbb; }
.pp-u.method { text-decoration-color:#3a9c92; } .pp-u.result { text-decoration-color:#8b79b5; }
.pp-u.def { text-decoration-style: dotted; text-decoration-thickness: 2px; text-decoration-color:#286b9c; }
.pp-u .pp-u.def { text-decoration: none; color:#205f90; }   /* 핵심 내용 밑줄 안의 용어 설명은 글자색으로만 */
.pp-u .pp-m, .pp-u [class^="pp-sig-"] { background: transparent !important; border-bottom: 0 !important; padding: 0; }
.pp-reason { display:flex; align-items:flex-start; gap:6px; margin-top:10px; color:#5c7084; font-size:12px; line-height:1.5; }
.pp-limit { color:#60758a; font-size:12px; margin-top:2px; }

/* 칩 */
.pp-chips { display:flex; gap:7px; flex-wrap:wrap; justify-content:flex-end; align-items:center; min-height:38px; }
.pp-chip { height:28px; padding:0 10px; border-radius:7px; display:inline-flex; align-items:center; font-size:12px; font-weight:750; }
.pp-chip.blue { color:#25618b; background:var(--blue-soft); } .pp-chip.amber { color:#986314; background:var(--amber-soft); }
.pp-chip.teal { color:#1f7b73; background:var(--teal-soft); } .pp-chip.violet { color:#73539d; background:var(--violet-soft); }

/* 원문·번역 */
.pp-reader-head { padding:12px 16px; border-bottom:1px solid #edf0f4; display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; }
.pp-reader-title { display:flex; align-items:center; gap:8px; font-size:14px; font-weight:780; color:var(--ink); }
.pp-lang { background:#eff4f8; border-radius:5px; color:#60758b; padding:4px 7px; font-size:10.5px; font-weight:800; letter-spacing:.35px; }
.pp-meta { color:#4f6478; font-size:12px; }
.pp-names-label { font-size:12.5px; font-weight:750; color:#2c4560; }
.pp-names .pp-chips { justify-content:flex-start; }
.pp-info { cursor:help; color:#3979b5; font-weight:700; }
[data-testid="stCaptionContainer"] { color:#4f6478 !important; }
.pp-sents { padding:8px 16px 10px; }
.pp-sent { display:grid; grid-template-columns:24px 1fr; gap:10px; padding:9px 0; border-bottom:1px solid #f0f3f6; }
.pp-sent:last-child { border-bottom:0; }
.pp-no { width:22px; height:22px; border-radius:6px; background:#f1f5f8; color:#8090a1; display:grid; place-items:center; font-size:11px; font-weight:750; margin-top:1px; }
.pp-text { color:#34485f; font-size:14.5px; line-height:1.7; letter-spacing:-.1px; }
.pp-ko .pp-text { color:#4a5e73; font-size:14px; }
.pp-off { color:#9a681d; font-size:12.5px; margin-top:4px; }
.pp-m { position:relative; padding:0 2px; border-radius:3px; font-weight:700; cursor:help; }
.pp-m:focus { outline:2px solid #5c9ac0; outline-offset:1px; }
.pp-m.abbr { color:#205f90; background:#eaf3fb; border-bottom:1px solid #7ca9ca; }
.pp-m.abbr-missing { color:#52677f; background:#f1f4f7; border-bottom:1px dashed #9aa8b6; }
.pp-m.hedge { color:#956314; background:#fff4dd; border-bottom:1px solid #d7ac57; }
.pp-m.confident { color:#1c756d; background:#e8f5f1; }
.pp-m.reference { color:#73539d; background:#f0ebf8; }
/* 색은 늘리지 않는다: 용어 설명은 약어와 같은 파랑, 수치 근거는 확신과 같은 청록, 반복 용어는 파랑 얇은 밑줄 */
.pp-m.definition { color:#205f90; background:#eaf3fb; border-bottom:1px solid #7ca9ca; }
.pp-m.number { color:#1c756d; background:#e8f5f1; }
.pp-m.term { color:inherit; font-weight:650; background:transparent; padding:0; border-bottom:1.5px solid #7ca9ca; border-radius:0; }
/* 설명창은 단어 위쪽에 띄운다: 아래에 띄우면 곧 읽을 다음 줄을 가린다(카드 안은 위가 잘리므로 아래) */
.pp-m:hover::after, .pp-m:focus::after { content: attr(data-tip); position:absolute; left:0; bottom:calc(100% + 6px); z-index:20;
  background:#17293f; color:#fff; font-size:12px; font-weight:500; line-height:1.45; padding:7px 9px; border-radius:7px;
  width:max-content; max-width:300px; white-space:normal; box-shadow:0 6px 16px rgba(0,0,0,.18); }
.pp-card .pp-m:hover::after, .pp-card .pp-m:focus::after { top:calc(100% + 6px); bottom:auto; }
.pp-banner { margin:-14px -16px 10px; padding:9px 16px; background:#f3f8fb; color:#536f86; font-size:12px; border-bottom:1px solid #edf0f4; }
.pp-notice { color:#52677f; font-size:13px; line-height:1.6; background:#fbfdff; border:1px dashed #d6e1ea; border-radius:10px; padding:12px 14px; margin-bottom:10px; }

/* 하단 근거 */
.pp-mini { display:flex; gap:12px; align-items:flex-start; }
.pp-mini-icon { flex:none; width:32px; height:32px; border-radius:9px; background:#f0f4f8; color:#59758d; display:grid; place-items:center; }
.pp-mini-title { display:flex; flex-wrap:wrap; align-items:center; gap:7px; font-size:13.5px; color:#3b5168; font-weight:780; margin-bottom:4px; }
.pp-mini-desc { color:#5c7084; font-size:12.5px; line-height:1.5; }
.pp-snip { color:#678096; font-family:ui-monospace, Consolas, monospace; font-size:11.5px; background:#f5f8fa; padding:3px 6px; border-radius:5px; }
.pp-foot { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:6px 2px 0; color:#60758a; font-size:12px; }
.pp-foot strong { color:#52697f; }

/* 이해를 돕는 자료: 흐름도 */
.pp-sub-title { font-size:14px; font-weight:780; color:#2c4560; margin:6px 0 6px; }
.pp-flow { display:flex; gap:6px; align-items:stretch; flex-wrap:wrap; }
.pp-flow-step { flex:1 1 170px; background:var(--surface); border:1px solid var(--line); border-top:3px solid #9aa8b6;
  border-radius:12px; padding:10px 12px; box-shadow:var(--shadow); }
.pp-flow-step.blue { border-top-color:#6c9dbb; } .pp-flow-step.teal { border-top-color:#3a9c92; }
.pp-flow-step.violet { border-top-color:#8b79b5; }
.pp-flow-head { display:flex; align-items:center; gap:5px; font-size:13px; color:#2c4560; margin-bottom:6px; }
.pp-flow-no { font-size:10.5px; font-weight:750; color:#60758a; background:#f1f5f8; border-radius:5px; padding:1px 5px; }
.pp-flow-text { font-size:13px; color:#34485f; line-height:1.55; }
.pp-flow-ko { font-size:12px; color:#5c7084; margin-top:5px; line-height:1.5; }
.pp-flow-arrow { align-self:center; color:#9aa8b6; font-size:18px; font-weight:700; }
.pp-flow-note { color:#60758a; font-size:12px; margin-top:6px; }
@media (max-width: 900px) { .pp-flow-arrow { display:none; } }

.pp-gloss th, .pp-gloss td:nth-child(-n+5) { white-space: nowrap; }
.pp-gloss td:nth-child(6) { min-width: 180px; }

/* 저장된 문서 목록 */
.pp-doc { font-size:13.5px; color:#152c49; line-height:1.4; }

/* 브라우저 저장 컴포넌트(보이지 않음) */
.st-key-browser_store_box { position:absolute; height:0; overflow:hidden; margin:0; padding:0; }

/* 단어표 아래 이름 목록 */
.pp-names { margin:8px 0 4px; }
.pp-chip.gray { color:#4c5d70; background:#eef2f6; }

/* 논문 표 */
.pp-table-cap { font-size:13px; color:#2c4560; line-height:1.55; margin:10px 0 6px; }

/* 본문 속 표·그림 카드(처음 언급한 문장 아래)와 마우스 미리보기 */
.pp-flt { display:block; background-size:contain; background-repeat:no-repeat; background-position:left top;
  background-color:#fff; }
.pp-float { margin:4px 0 10px 34px; border:1px solid var(--line); border-left:3px solid #8b79b5; border-radius:10px;
  background:#fbfaff; padding:8px 12px; }
.pp-float summary { list-style:none; cursor:zoom-in; }
.pp-float summary::-webkit-details-marker { display:none; }
.pp-float details[open] summary { cursor:zoom-out; }
.pp-float details[open] .pp-thumb { display:none; }
.pp-float-head { font-size:13px; color:#2c4560; }
.pp-float-cap { font-size:12.5px; color:#4a5d72; line-height:1.5; margin:3px 0 6px; }
.pp-float { padding:5px 12px; }
.pp-float-short { font-size:12px; color:#5b6d80; margin-left:4px; }
.pp-float details[open] .pp-float-short { display:none; }
/* 한 언어만 볼 때(영어 원문만·한국어만): 한 줄이 너무 길지 않게 가운데 좁은 폭 */
.pp-narrow, .st-key-ko_only { max-width:960px; margin-left:auto !important; margin-right:auto !important; }
.st-key-part_next_box [data-testid="stButton"] { display:flex; justify-content:flex-end; }
/* 이전·다음 막대: 본문 칸 바로 아래 같은 폭으로 붙인다 */
.st-key-part_nav, .st-key-part_nav_narrow { border:1px solid var(--line); border-radius:12px; background:#f7fafd;
  padding:6px 12px; margin-top:8px; }   /* 본문 글 상자의 음수 아래 여백(-16px)을 메워 겹치지 않게 */
.st-key-part_next_box { align-items:flex-end !important; }   /* 세로로 쌓는 칸이라 오른쪽 끝은 align-items로 */
.st-key-part_nav_narrow { max-width:960px; margin-left:auto !important; margin-right:auto !important; }
.st-key-part_nav button, .st-key-part_nav_narrow button { min-height:36px !important; }
.st-key-part_bar, .st-key-paper_nav { scroll-margin-top:72px; }   /* 위에 붙은 막대에 가리지 않게 */
.pp-nav-label { font-size:12px; font-weight:750; color:#2c4560; margin:6px 0 3px; }
.pp-sk-more summary { list-style:none; cursor:pointer; }
.pp-sk-more summary::-webkit-details-marker { display:none; }
.pp-sk-more .pp-sk-text { display:-webkit-box; -webkit-line-clamp:4; -webkit-box-orient:vertical; overflow:hidden; }
.pp-sk-more[open] .pp-sk-text { display:block; -webkit-line-clamp:unset; }
.pp-more-btn { display:inline-block; margin-top:4px; font-size:12px; font-weight:700; color:#3979b5; }
.pp-sk-more[open] .pp-more-btn:after { content:" 접기"; } .pp-sk-more[open] .pp-more-btn { font-size:0; }
.pp-sk-more[open] .pp-more-btn:after { font-size:12px; }
/* 02 표시 칩: 누르기 쉽게 간격·여백을 넓힌다 */
.st-key-mark_pills [data-testid="stButtonGroup"] > div { gap:8px !important; flex-wrap:wrap !important; }
.st-key-chip_row .pp-chips { justify-content:flex-start; }
.st-key-chip_row .pp-nav-label { margin-top:0; }
.st-key-mark_pills button { padding:5px 13px !important; min-height:32px; }
/* 01 카드: 같은 줄의 카드 높이를 가장 높은 카드에 맞춘다 */
/* height:100%로 하면 가장 긴 카드가 줄 높이에 잘리므로, 칸을 세로 flex로 두고 남는 높이만 채운다 */
.st-key-card_row [data-testid="stColumn"]:has(.pp-card), .st-key-card_row [data-testid="stColumn"] div:has(.pp-card) {
  display:flex; flex-direction:column; flex:1 1 auto; }
.st-key-card_row .pp-card { height:auto; flex:1 1 auto; }
.pp-end { border:0; border-top:2px solid #d9e3ee; margin:26px 0 8px; }
.pp-cat { font-size:.82em; color:#8796a6; }
.pp-subhead { margin:14px 0 2px; padding:4px 10px; font-size:13.5px; font-weight:750; color:#152c49;
  border-left:3px solid #3979b5; background:#f3f8fd; border-radius:0 6px 6px 0; }
.pp-subhead:first-child { margin-top:2px; }
/* 저장된 문서 카드 */
.pp-doc-card { border:1px solid var(--line); border-radius:12px; overflow:hidden; background:var(--surface); }
/* 카드 하나 = 그림·제목·진행·버튼(테두리는 카드 칸 전체에) */
[class*="st-key-doc_card_"] { border:1px solid var(--line); border-radius:12px; background:var(--surface); overflow:hidden;
  gap:0 !important; padding-bottom:10px; box-shadow:var(--shadow); }
[class*="st-key-doc_card_"] .pp-doc-card { border:0; border-radius:0; box-shadow:none; }
[class*="st-key-doc_card_"] [data-testid="stMarkdownContainer"], [class*="st-key-doc_card_"] [data-testid="stMarkdown"] {
  margin-bottom:0 !important; }   /* Streamlit 기본 아래 여백(음수)이 버튼을 글자 위로 끌어올리지 않게 */
[class*="st-key-doc_card_"] [data-testid="stHorizontalBlock"] { padding:4px 11px 0; gap:6px; }
[class*="st-key-doc_card_"] button { min-height:34px !important; padding-top:4px !important; padding-bottom:4px !important; }
.pp-doc-thumb { height:118px; background-color:#f4f7fb; background-size:contain; background-repeat:no-repeat;
  background-position:center; border-bottom:1px solid #edf1f6; }
.pp-doc-blank { display:flex; align-items:center; justify-content:center; font-size:38px; font-weight:800; color:#9fb6cf;
  background:linear-gradient(135deg, #eaf3fc, #f7fafd); }
.pp-doc-body { padding:9px 11px 6px; }
.pp-doc-title { font-size:13.5px; font-weight:750; color:#152c49; line-height:1.35; display:-webkit-box;
  -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.7em; }
.pp-doc-last { font-size:12px; color:#3979b5; font-weight:650; margin-top:3px; }
.pp-resume { font-size:14px; color:#152c49; }
.st-key-resume_box { background:#eef5fd; border:1px solid #cfe0f2; border-radius:10px; padding:6px 12px; }
/* 어려운 단어: 단어표의 드문 말을 보라 점선으로(형광펜보다 약하게) */
.pp-m.jargon { color:inherit; font-weight:inherit; background:transparent; padding:0; border-radius:0;
  border-bottom:1.5px dotted #8b79b5; }
/* 화면 안내: '안내 끝'을 알리는 숨은 버튼(브라우저의 안내 스크립트가 누름) */
.st-key-tour_done_box { position:absolute; height:0; overflow:hidden; margin:0; padding:0; }
/* 로고·이름 전체를 누를 수 있게: 보이지 않는 버튼을 위에 덮는다 */
.st-key-brand_home { position:relative; cursor:pointer; min-height:46px; }
.st-key-go_home { position:absolute !important; inset:0; width:100% !important; height:100% !important; opacity:0; z-index:2; }
.st-key-go_home > div, .st-key-go_home [data-testid="stButton"], .st-key-go_home button {
  width:100% !important; height:100% !important; min-height:46px; cursor:pointer; }
.st-key-brand_home:hover .pp-name { color:#3979b5; }
.pp-jump { margin-left:10px; font-size:12px; font-weight:700; color:#3979b5; text-decoration:none; white-space:nowrap; }
#pp-03 { scroll-margin-top:72px; }
.pp-proto-list { margin:6px 0 0; padding-left:0; list-style:none; }
.pp-proto-list li { font-size:13px; line-height:1.55; padding:5px 0; border-top:1px dashed #e3e9f0; }
.pp-proto-list li:first-child { border-top:0; }
.pp-proto-tag { font-weight:800; color:#a8641a; }
.pp-float .pp-thumb { border:1px solid #e6e9f0; border-radius:4px; }
.pp-float .pp-full { margin-top:6px; border:1px solid #e6e9f0; border-radius:4px; }
.pp-m:has(.pp-pop) { position:relative; }
.pp-pop { display:none; position:absolute; left:0; top:1.7em; z-index:60; width:max-content; max-width:min(540px, 86vw);
  background:#fff; border:1px solid #cfd8e3; border-radius:8px; box-shadow:0 8px 24px rgba(20,38,62,.18); padding:6px; }
.pp-pop .pp-flt { max-width:84vw; }   /* 너비는 float_box가 원래 크기(최대 520px)로 정한다 */
.pp-m:hover > .pp-pop, .pp-m:focus > .pp-pop { display:block; }
@media (hover: none) { .pp-pop { display:none !important; } }

/* 읽기 모드: 한영 나란히(문장 짝마다 한 행) */
.pp-side-head, .pp-side-row { display:grid; grid-template-columns:30px minmax(0,1.1fr) minmax(0,.95fr); gap:0 18px; }
.pp-side-head { padding:10px 14px; border-bottom:1px solid var(--line); align-items:center; }
.pp-side-row { padding:10px 14px; border-bottom:1px solid #eef2f6; align-items:start; }
.pp-side-ko { color:#34485f; border-left:2px solid #e3ebf4; padding-left:14px; }
@media (max-width: 760px) {
  .pp-side-head { display:none; }
  .pp-side-row { grid-template-columns:26px minmax(0,1fr); }
  .pp-side-ko { grid-column:2; border-left:0; padding-left:0; margin-top:6px; padding-top:6px; border-top:1px dashed #dbe5ef; }
}

/* 읽기 모드: 문장마다 한영 */
.pp-pair-ko { display:flex; gap:8px; align-items:baseline; margin-top:6px; padding:6px 10px; background:#f6f9fc;
  border-left:3px solid #9fbbd2; border-radius:6px; color:#34485f; }
.pp-pair-ko .pp-lang { flex:none; font-size:10px; }
.st-key-read_mode_bar { margin:2px 0 8px; }

/* 단어표 AI 풀이 칸 */
.pp-ai { color:#4b3f72; }
.pp-ai-badge { font-size:10.5px; font-weight:700; color:#6b5a9e; background:#f1edf9; border-radius:5px; padding:1px 5px; }

/* 자료를 넣은 뒤 위쪽 막대 */
[data-testid="stLayoutWrapper"]:has(> .st-key-loaded_bar) { position:sticky; top:0; z-index:40; }  /* 막대를 감싼 칸이 막대와 높이가 같아 그 바깥에 붙인다 */
.st-key-loaded_bar { background:var(--surface); border:1px solid var(--line); border-left:3px solid #4f86ad;
  border-radius:12px; padding:8px 14px; box-shadow:var(--shadow); }
.pp-loaded { font-size:13.5px; color:#2c4560; }
.pp-loaded-label { font-size:11.5px; font-weight:750; color:#60758a; background:#eef3f8; border-radius:6px;
  padding:2px 7px; margin-right:8px; }

/* PDF 끌어다 놓기 칸 */
.st-key-pdf_drop { border:2px dashed #9cc0e3; border-radius:14px; background:#eaf3fc; padding:16px 18px 8px; }
.pp-drop-kicker { font-size:12px; font-weight:800; color:#3979b5; text-align:center; letter-spacing:.4px; }
.pp-drop-title { font-size:18px; font-weight:800; color:#152c49; text-align:center; margin-top:2px; }
.pp-drop-hint { font-size:12.5px; color:#5c7084; text-align:center; margin:2px 0 8px; }
.st-key-pdf_drop [data-testid="stFileUploaderDropzone"] { min-height:120px; background:#fff;
  border:1px solid #d6e2ec; justify-content:center; }

/* 자료를 넣기 전 안내 */
.pp-guide { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
.pp-guide-step { flex:1 1 220px; display:flex; gap:10px; background:var(--surface); border:1px solid var(--line);
  border-radius:12px; padding:12px 14px; box-shadow:var(--shadow); color:#2c4560; font-size:14px; }
.pp-guide-no { flex:none; width:26px; height:26px; border-radius:50%; background:#183b60; color:#fff;
  display:flex; align-items:center; justify-content:center; font-weight:750; font-size:13px; }
.pp-guide-desc { color:#5c7084; font-size:12.5px; margin-top:3px; line-height:1.5; }

/* 단계 보기·규칙 */
.pp-steps { display:grid; grid-template-columns:repeat(6,1fr); gap:8px; }
.pp-step { border:1px solid #e8eef4; border-radius:10px; padding:10px; background:#fbfdff; }
.pp-step b { display:block; font-size:12.5px; color:#2c4560; margin-bottom:4px; }
.pp-step span { font-size:12px; color:#5c7084; line-height:1.45; }
.pp-table { width:100%; border-collapse:collapse; font-size:13.5px; }
.pp-table th { text-align:left; color:#52677f; font-weight:700; padding:9px 10px; border-bottom:1px solid var(--line); background:#f8fafc; }
.pp-table td { padding:10px; border-bottom:1px solid #eef2f6; vertical-align:top; color:#34485f; line-height:1.55; }
.pp-tag { display:inline-block; padding:2px 8px; border-radius:6px; font-size:11.5px; font-weight:750; }
.pp-tag.measured { background:var(--teal-soft); color:#1f7b73; } .pp-tag.target { background:var(--amber-soft); color:#986314; }
.pp-tag.planned { background:#eef2f6; color:#52677f; }

@media (max-width: 900px) {
  .pp-steps { grid-template-columns:repeat(2,1fr); }
  .pp-privacy { border-left:0; padding-left:0; }
  .pp-h1 { font-size:23px; }
}
</style>
"""


# ================================================================ 상태

def init_state() -> None:
    ss = st.session_state
    ss.setdefault("page", PAGES[0])
    ss.setdefault("input_mode", M_PDF)
    ss.setdefault("arxiv_id", "")
    ss.setdefault("paste_text", "")
    ss.setdefault("input_text", "")         # 자료를 넣기 전에는 비어 있고, 아래 결과 창들을 그리지 않는다
    ss.setdefault("source", {"kind": "none"})
    ss.setdefault("active_rules", set(P.DEFAULT_ACTIVE_RULES) | {JARGON_RULE})
    ss.setdefault("translations", {})
    ss.setdefault("show_steps", False)
    ss.setdefault("show_translation", True)
    ss.setdefault("input_error", None)
    ss.setdefault("deepl_calls", 0)
    ss.setdefault("paper", None)              # PDF에서 나눈 절 목록(src.sections.Paper). 파일 자체는 저장하지 않는다
    ss.setdefault("paper_cur", "초록")        # PDF 논문에서 지금 보는 쪽(초록·본문·결론)
    ss.setdefault("body_cur", 0)              # 본문 쪽에서 지금 보는 절 번호
    ss.setdefault("show_input", True)         # 자료를 넣으면 입력 칸을 닫고, '다시 넣기'로 다시 연다
    ss.setdefault("ai_gloss", {})             # {글 해시: {단어: AI 풀이}}
    ss.setdefault("saved_sig", {})            # 이 브라우저에 저장했거나 불러온 글: 해시 → 저장 표시(src.saved.signature)
    ss.setdefault("browser_ver", 0)           # 브라우저 저장 요청 번호(바뀔 때만 컴포넌트가 저장한다)
    ss.setdefault("browser_loaded", False)    # 이 화면을 연 뒤 브라우저에 저장된 결과를 불러왔는지
    ss.setdefault("browser_keys", set())      # 지금 이 브라우저에 저장돼 있는 글 해시(화면 표시용)
    ss.setdefault("browser_docs", {})         # 이 브라우저에 저장된 '다시 열 문서' {id: 기록}(src.saved.doc_record)
    ss.setdefault("docs_pending", {})         # 다음 실행에 브라우저에 저장할 문서
    ss.setdefault("docs_delete", [])          # 다음 실행에 브라우저에서 지울 문서 id
    ss.setdefault("account_loaded", False)    # 로그인 뒤 내 계정 저장소에서 불러왔는지
    ss.setdefault("account_sig", {})          # 내 계정에 올린 결과 항목: 해시 → 저장 표시
    ss.setdefault("account_docs", {})         # 내 계정에 올린 문서: id → (저장 시각, 지난 위치)
    ss.setdefault("prefs", {})                # 사용 안내를 봤는지 등(이 브라우저·내 계정)
    ss.setdefault("prefs_pending", {})
    ss.setdefault("account_msg", None)        # 내 계정 저장소 오류 안내
    ss.setdefault("current_doc", None)        # 지금 읽는 문서 id(번역 한도를 세는 단위, src/quota.py)
    ss.setdefault("guest_chars", 0)           # 로그인하지 않고 이 화면에서 번역한 글자 수(맛보기 한도)


@st.cache_resource
def engine_ok() -> bool:
    try:
        return analyze("We introduce a method (ABC) that may work [1].").sentences != []
    except Exception:
        return False


def text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def go_page(name: str) -> None:
    st.session_state.page = name


def load_demo() -> None:
    st.session_state.input_text = DEMO_TEXT
    st.session_state.source = {"kind": "demo"}
    st.session_state.input_error = None
    st.session_state.paper = None
    st.session_state.show_input = False


def set_view() -> None:
    """지금 고른 쪽(초록·본문·결론)과 본문 절에 맞춰 읽을 글을 바꾼다. 번역은 글마다 따로 남는다."""
    ss = st.session_state
    pages = paper_pages(ss.paper)
    page = ss.paper_cur if ss.paper_cur in pages else next(iter(pages))
    ids = pages[page]
    if page == "본문":
        ids = [ids[min(ss.body_cur, len(ids) - 1)]]
    secs = [ss.paper.sections[i] for i in ids]
    ss.input_text = "\n\n".join(s.text for s in secs)        # 결론 쪽은 Discussion·Conclusion을 이어 붙인다
    label = page if page != "본문" else f"본문 › {secs[0].title}"
    if page != "본문":
        label += " (" + ", ".join(s.title for s in secs) + ")"
    ss.source = {**ss.source, "section": label, "section_ids": ids, "page": page}


def on_page_change() -> None:
    ss = st.session_state
    if ss.paper_page is not None:          # 같은 버튼을 다시 눌러 선택이 풀리면 지금 쪽을 유지
        ss.paper_cur = ss.paper_page
    set_view()
    track_position(new_section=True)


def pick_section() -> None:
    ss = st.session_state
    if ss.section_pick is not None:
        ss.body_cur = ss.section_pick
    set_view()
    track_position(new_section=True)


def set_show_input(on: bool) -> None:
    st.session_state.show_input = on
    st.session_state.input_error = None


def loaded_bar(a) -> None:
    """자료를 넣은 뒤 맨 위에 붙어 있는 막대: 지금 읽는 자료, 사용법, 약어·어려운 단어 팝오버, 결과 파일, 다시 넣기.
    스크롤해도 따라오므로 읽는 도중에 약어·어려운 단어를 바로 펼쳐 볼 수 있다."""
    with st.container(key="loaded_bar"):
        c1, c5, c4, c3, c2 = st.columns([2.9, 0.95, 1.15, 1.3, 1], vertical_alignment="center")
        with c4.popover("약어·단어·규칙", icon=":material/menu_book:", use_container_width=True):
            reading_helper(a)
        c5.button("사용법", key="tut_open", icon=":material/help:", on_click=open_tutorial, use_container_width=True,
                  help="화면을 하나씩 짚어 가며 기능을 알려 줘요")   # 스크롤해도 늘 보이게 위 막대에
        c1.markdown(f'<div class="pp-loaded"><span class="pp-loaded-label">지금 읽는 자료</span>'
                    f'{esc(source_flag())}<a class="pp-jump" href="#pp-03">03 자료로 ↓</a></div>', unsafe_allow_html=True)
        ss = st.session_state
        items = SV.pack_items(ss.translations, ss.ai_gloss)
        c3.download_button("결과 파일 내려받기", SV.to_file(items, ss.source.get("title", ""), ss.browser_docs),
                           file_name="paperpath_results.json", mime="application/json", key="saved_dl",
                           icon=":material/download:", use_container_width=True, disabled=not items,
                           help="번역·뜻 풀이 결과를 파일로 받아요. 다른 PC에서 '결과 파일 불러오기'로 넣으면 다시 번역하지 않아요.")
        c2.button("다시 넣기", key="reinput_btn", icon=":material/upload_file:", use_container_width=True,
                  on_click=set_show_input, args=(True,), help="다른 PDF·arXiv ID·텍스트를 넣어요")


def reading_helper(a) -> None:
    """'약어·단어·규칙' 팝오버(고정 막대): 이 글의 약어, 어려운 단어 상위 8개(뜻과 함께), 걸린 규칙 근거."""
    ss = st.session_state
    tr = ss.translations.get((text_key(a.text), True))
    rows = G.build(a, G.korean_marks(tr))
    meanings = ss.ai_gloss.get(text_key(a.text))
    if meanings:
        G.apply_ai(rows, meanings)
    st.markdown("**약어**")
    if a.acronyms:
        st.markdown("\n".join(f"- **{k}** → {v or '이 글에서 정의를 찾지 못함'}" for k, v in a.acronyms.items()))
    else:
        st.caption("이 글에는 약어가 없어요.")
    st.markdown(f"**어려운 단어** · {len(rows)}개")
    if rows:
        lines = []
        for e in rows[:GLOSS_PREVIEW]:
            mean = e.ai_meaning or (e.explanation if e.explanation and "정의를 찾지" not in e.explanation else "") or e.korean
            lines.append(f"- **{e.term}**" + (f" — {mean}" if mean else ""))
        st.markdown("\n".join(lines))
        st.caption("전체 목록은 아래 '03 이해를 돕는 자료 → 어려운 단어' 탭에 있어요. 뜻은 참고용이에요.")
    else:
        st.caption("이 글에는 한국어로 옮겨도 안 풀릴 전문용어가 없어요.")
    rule_summary(a)


def rule_summary(a) -> None:
    """팝오버의 '규칙 근거': 이 글에서 걸린 표시 종류마다 개수·규칙 이름·예시 표현 두 개(정규식이 찾은 그대로)."""
    groups: dict[str, list[str]] = {}
    for m in a.marks:
        phrase = a.text[m.start:m.end]
        lst = groups.setdefault(m.category, [])
        if phrase not in lst:
            lst.append(phrase)
    st.markdown(f"**규칙 근거** · {sum(len(v) for v in groups.values())}개 표현")
    if not groups:
        st.caption("이 글에서 걸린 규칙이 없어요.")
        return
    order = [c for c in MARK_LABEL if c in groups]
    st.markdown("\n".join(f"- **{MARK_LABEL[c]}** `{CAT_RULE[c]}` · {len(groups[c])}개 — "
                          + ", ".join(f"_{x[:30]}_" for x in groups[c][:2]) for c in order))
    st.caption("각 규칙의 정규식·예시·일부러 놓치는 경우는 위 '규칙 탐색' 탭에 있어요.")


DOC_CARDS_PER_ROW = 3


def doc_card(d: dict) -> str:
    """저장 문서 카드 HTML: 대표 그림(없으면 제목 첫 글자 판), 제목 두 줄, 종류·날짜·번역 진행, 지난 위치."""
    done, total = doc_progress(d)
    when = time.strftime("%m/%d %H:%M", time.localtime(d.get("ts", 0)))
    kind = {"pdf": "PDF", "arxiv": "arXiv", "paste": "붙여 넣은 글"}[d["kind"]]
    title = d.get("title") or "제목 없음"
    thumb = d.get("thumb") or ""
    if thumb.startswith("data:image/jpeg;base64,"):
        pic = f'<div class="pp-doc-thumb" style="background-image:url({thumb})"></div>'
    else:
        initial = next((ch for ch in title if ch.isalnum()), "P").upper()
        pic = f'<div class="pp-doc-thumb pp-doc-blank">{esc(initial)}</div>'
    last = (d.get("last") or {}).get("label")
    # 지난 위치가 없어도 한 줄을 비워 두어 같은 줄 카드의 버튼 높이를 맞춘다
    where = f'<div class="pp-doc-last">지난번: {esc(last)}</div>' if last else '<div class="pp-doc-last">&nbsp;</div>'
    return (f'<div class="pp-doc-card">{pic}<div class="pp-doc-body"><div class="pp-doc-title" title="{esc(title)}">'
            f'{esc(title)}</div><div class="pp-meta">{kind} · {when} · 번역 {done}/{total}쪽</div>{where}</div></div>')


def saved_panel() -> None:
    """입력 칸 아래: 이 브라우저에 저장된 결과 수, 지우기(A), 결과 파일 불러오기(B)."""
    ss = st.session_state
    n = len(ss.browser_keys)
    docs = sorted(ss.browser_docs.values(), key=lambda d: -d.get("ts", 0))
    label = f"저장된 문서 {len(docs)}개 · 번역된 글 {n}개" if docs or n else "저장된 문서 · 결과 파일 불러오기"
    with st.expander(label, expanded=bool(docs)):
        if current_user():
            st.caption("로그인 중: 읽은 문서(절 글자)와 번역·뜻 풀이 결과가 이 브라우저와 **내 계정**에 저장돼요. 다른 PC에서도 "
                       "로그인하면 '다시 열기'로 이어 볼 수 있어요. 이메일·이름·PDF 파일·표 이미지는 저장하지 않아요.")
        else:
            st.caption("읽은 문서와 번역·뜻 풀이 결과는 이 브라우저에만 저장돼요(서버에는 저장하지 않음). '다시 열기'를 누르면 "
                       "PDF를 다시 넣지 않아도 바로 열리고, 번역한 부분은 API 없이 보여요. 다른 PC에서는 결과 파일을 불러오세요"
                       + (" (로그인하면 다른 PC에서도 이어 볼 수 있어요)." if auth_ready() else "."))
        if ss.get("account_msg"):
            st.warning(ss.account_msg)
        elif current_user():
            st.caption(f"내 계정 동기화: 문서 {len(ss.account_docs)}개 · 번역·뜻 풀이 글 {len(ss.account_sig)}개가 계정에 있어요.")
        shown_docs = docs[:SV.MAX_DOCS]
        for row in range(0, len(shown_docs), DOC_CARDS_PER_ROW):   # 한 줄에 카드 3장: 대표 그림 · 제목 · 진행 · 지난 위치
            cols = st.columns(DOC_CARDS_PER_ROW)
            for col, d in zip(cols, shown_docs[row:row + DOC_CARDS_PER_ROW]):
                with col.container(key=f"doc_card_{d['id']}"):
                    st.markdown(doc_card(d), unsafe_allow_html=True)
                    b1, b2 = st.columns([2.2, 1], vertical_alignment="center")   # 버튼은 카드 안 아래쪽에
                    b1.button("이어서 보기" if d.get("last") else "열기", key=f"doc_open_{d['id']}", on_click=open_doc,
                              args=(d["id"],), type="primary", use_container_width=True,
                              icon=":material/play_arrow:" if d.get("last") else ":material/menu_book:")
                    b2.button("삭제", key=f"doc_del_{d['id']}", on_click=delete_doc, args=(d["id"],), type="tertiary",
                              icon=":material/delete:", use_container_width=True)
        st.file_uploader("결과 파일 불러오기(.json)", type=["json"], key="saved_file", on_change=on_saved_upload)
        if ss.get("saved_msg"):
            st.success(ss.saved_msg)
        if n:
            st.button("이 브라우저에 저장된 번역 모두 지우기", key="saved_clear", type="tertiary", on_click=clear_browser)
        if current_user():
            st.button("내 계정에 저장된 기록 모두 지우기", key="account_clear", type="tertiary", on_click=account_delete_all)


def remember_doc(rec: dict, current: bool = False) -> None:
    """읽은 문서를 '저장된 문서'에 넣는다(다음 실행에서 브라우저에 저장). current: 지금 읽기 시작한 문서(번역 한도를 셀 문서)."""
    ss = st.session_state
    ss.browser_docs[rec["id"]] = rec
    ss.docs_pending[rec["id"]] = rec
    if current:
        ss.current_doc = rec["id"]


def doc_texts(rec: dict) -> list[str]:
    """문서를 열었을 때 읽게 되는 글들(PDF면 초록·본문 절마다·결론 묶음). 번역이 저장돼 있는지 세는 데 쓴다."""
    if rec["kind"] != "pdf":
        return [s["text"] for s in rec["sections"]]
    paper = paper_from_doc(rec)
    out = []
    for page, ids in paper_pages(paper).items():
        if page == "본문":
            out += [paper.sections[i].text for i in ids]
        else:
            out.append("\n\n".join(paper.sections[i].text for i in ids))
    return out


def doc_progress(rec: dict) -> tuple[int, int]:
    texts = doc_texts(rec)
    ss = st.session_state
    return sum((SV.text_hash(t), True) in ss.translations for t in texts), len(texts)


def paper_from_doc(rec: dict) -> Paper:
    return Paper(title=rec.get("title", ""), notes=list(rec.get("notes") or []),
                 sections=[Section(s.get("title", ""), text=s["text"], subheads=list(s.get("subheads") or []),
                                   sub_at=[int(x) for x in (s.get("sub_at") or [])])
                           for s in rec["sections"]])


def open_doc(doc_id: str) -> None:
    """저장된 문서 다시 열기: PDF 없이 절 글자로 다시 만든다. 표·그림 이미지는 PDF를 다시 넣어야 보인다."""
    ss = st.session_state
    rec = ss.browser_docs.get(doc_id)
    if not rec:
        return
    rec = {**rec, "ts": time.time()}
    remember_doc(rec, current=True)        # 최근에 연 문서가 위로 오게
    ss.input_error = None
    ss.paper_tables, ss.paper_figures = [], []
    if rec["kind"] == "pdf":
        paper = paper_from_doc(rec)
        if "표·그림은 PDF를 다시 넣으면 보여요." not in paper.notes:
            paper.notes.append("표·그림은 PDF를 다시 넣으면 보여요.")
        ss.paper = paper
        ss.paper_cur, ss.body_cur = PAPER_PAGES[0], 0
        for k in ("paper_page", "section_pick"):
            ss.pop(k, None)
        ss.source = {"kind": "pdf", "title": paper.title}
        set_view()
        last = rec.get("last")
        ss.resume_offer = ({"doc": doc_id, **last}
                           if last and (last.get("page") != PAPER_PAGES[0] or last.get("body") or last.get("part")) else None)
    else:
        ss.resume_offer = None
        ss.paper = None
        ss.input_text = rec["sections"][0]["text"]
        ss.source = ({"kind": "arxiv", "id": rec.get("arxiv_id", ""), "title": rec.get("title", "")}
                     if rec["kind"] == "arxiv" else {"kind": "paste"})
    ss.show_input = False


TOUR_STEPS = (
    # (가리킬 화면 요소 CSS 선택자, 제목, 설명) — 실제 화면 위에서 하나씩 강조하며 보여 준다
    (".st-key-card_row", "먼저 읽을 3줄 뼈대",
     "이 글의 <b>문제 · 방법 · 결과</b> 문장이에요. 정규식이 신호 문구(<i>we propose</i>, <i>results show</i>…)로 찾았고, "
     "진한 밑줄은 그 문장의 핵심 내용이에요. 오른쪽 위 '신호 문구로 찾음 / 위치로 추정'은 근거가 얼마나 확실한지예요."),
    (".st-key-chip_row", "형광펜 켜고 끄기",
     "색마다 뜻이 있어요: <b>파랑</b> 약어·용어 설명, <b>노랑</b> 추측·가능성, <b>초록</b> 확신·수치, <b>보라</b> 참조, "
     "보라 <b>점선</b> 어려운 단어. 칩을 누르면 그 표시만 꺼지고, 다시 누르면 켜져요."),
    (".pp-sents .pp-m", "마우스를 올려 보세요",
     "표시된 말에 마우스를 올리면(휴대폰은 누르면) <b>뜻 · 규칙 · 이 글에 나온 횟수</b>가 떠요. 'Table 2'·'Fig. 3'에 올리면 "
     "표·그림 미리보기가 뜨고, 한국어 번역 쪽에도 같은 설명이 떠요."),
    (".st-key-read_mode_bar", "읽기 모드",
     "<b>한영 나란히 · 문장마다 한영 · 영어만 · 한국어만</b> 중에서 골라요. 번역하기를 누르면 문장마다 한국어가 붙어요 "
     "(AI 번역, 참고용)."),
    (".st-key-part_bar", "긴 절은 나눠 읽기",
     "40문장이 넘는 절은 <b>소제목 단위</b>로 나뉘어요. 여기서 고르거나 글 아래 <b>◀ 이전 · 다음 ▶</b>으로 넘겨요."),
    (".st-key-loaded_bar [data-testid='stPopover']", "약어·단어·규칙",
     "스크롤 위치와 상관없이 이 글의 <b>약어 · 어려운 단어 · 걸린 규칙</b>을 바로 펼쳐 볼 수 있어요. "
     "전체 단어표와 흐름도는 맨 아래 <b>03</b>에 있어요."),
    (".st-key-tut_open", "언제든 다시 보기",
     "읽던 곳은 자동으로 저장돼서 다음에 '저장된 문서'에서 <b>이어서 보기</b>를 고를 수 있어요. "
     "이 안내는 위 막대의 <b>'사용법'</b>으로 언제든 다시 볼 수 있어요."),
)

TOUR_CORE = r"""
(function () {
  const W = window, D = document, STEPS = __STEPS__, NONCE = "__NONCE__";
  const old = D.getElementById("pp-tour");
  if (old && old.dataset.nonce === NONCE) return;
  if (old) old.remove();
  D.querySelectorAll(".pp-tour-on").forEach(e => e.classList.remove("pp-tour-on"));
  const css = D.getElementById("pp-tour-css") || D.head.appendChild(Object.assign(D.createElement("style"), {id: "pp-tour-css"}));
  css.textContent = `
    #pp-tour-hole { position:fixed; z-index:100000; border-radius:12px; pointer-events:none; transition:all .25s;
      box-shadow:0 0 0 9999px rgba(15,30,50,.55), 0 0 0 3px #ffd84d, 0 0 26px 8px rgba(255,216,77,.75); }
    #pp-tour-card { position:fixed; z-index:100001; width:min(380px, calc(100vw - 32px)); background:#fff; border-radius:14px;
      box-shadow:0 12px 36px rgba(0,0,0,.28); padding:16px 18px 14px; font-family:Pretendard, 'Malgun Gothic', sans-serif; }
    #pp-tour-card .n { font-size:12px; font-weight:750; color:#3979b5; }
    #pp-tour-card .t { font-size:17px; font-weight:800; color:#152c49; margin:2px 0 6px; }
    #pp-tour-card .b { font-size:13.5px; line-height:1.7; color:#34485f; }
    #pp-tour-card .row { display:flex; justify-content:space-between; align-items:center; margin-top:12px; gap:8px; }
    #pp-tour-card button { border:0; border-radius:9px; padding:7px 14px; font-weight:700; font-size:13px; cursor:pointer; }
    #pp-tour-card .skip { background:transparent; color:#60758a; padding-left:0; }
    #pp-tour-card .prev { background:#eef3f8; color:#2c4560; }
    #pp-tour-card .next { background:#152c49; color:#fff; }
    .pp-tour-on { background:#fff3b8 !important; border-radius:4px; animation:pp-tour-pulse 1.1s ease-in-out infinite; }
    @keyframes pp-tour-pulse { 0%,100% { box-shadow:0 0 0 2px #ffd84d, 0 0 10px 3px rgba(255,216,77,.8); }
                               50% { box-shadow:0 0 0 4px #ffd84d, 0 0 22px 8px rgba(255,216,77,.95); } }
    .pp-m.pp-tour-on::after { content: attr(data-tip); position:absolute; left:0; bottom:calc(100% + 8px); z-index:20;
      background:#17293f; color:#fff; font-size:12px; font-weight:500; line-height:1.45; padding:7px 9px; border-radius:7px;
      width:max-content; max-width:300px; white-space:normal; box-shadow:0 6px 16px rgba(0,0,0,.18); }
    .st-key-card_row, .st-key-chip_row, .st-key-read_mode_bar, .st-key-part_bar, .st-key-tut_open, .pp-sents .pp-m {
      scroll-margin-top:110px; scroll-margin-bottom:260px; }`;
  const root = Object.assign(D.createElement("div"), {id: "pp-tour"}); root.dataset.nonce = NONCE;
  root.innerHTML = '<div id="pp-tour-hole"></div><div id="pp-tour-card"><div class="n"></div><div class="t"></div>' +
    '<div class="b"></div><div class="row"><button class="skip">건너뛰기</button><span>' +
    '<button class="prev">◀ 이전</button> <button class="next">다음 ▶</button></span></div></div>';
  D.body.appendChild(root);
  const hole = root.querySelector("#pp-tour-hole"), card = root.querySelector("#pp-tour-card");
  const steps = STEPS.filter(s => D.querySelector(s[0]));          // 이 화면에 없는 요소(짧은 글의 나눠 읽기 등)는 건너뛴다
  let i = 0;
  function target() { return D.querySelector(steps[i][0]); }
  function place() {
    const el = target(); if (!el) return;
    const r = el.getBoundingClientRect(), pad = 6;
    const up = el.classList.contains("pp-m") ? 64 : 0;            // 표시 단어는 위에 뜨는 설명창까지 밝게
    Object.assign(hole.style, {left: (r.left - pad) + "px", top: (r.top - pad - up) + "px",
                               width: (r.width + pad * 2) + "px", height: (r.height + pad * 2 + up) + "px"});
    if (up) Object.assign(hole.style, {width: Math.min(320, W.innerWidth - r.left) + "px"});
    const ch = card.offsetHeight, cw = card.offsetWidth, vh = W.innerHeight, vw = W.innerWidth;
    if (vw < 700) {                                                // 좁은 화면: 설명 카드는 화면 아래에 붙인다
      Object.assign(card.style, {top: "auto", bottom: "12px", left: Math.max(10, (vw - cw) / 2) + "px"});
      return;
    }
    let top = r.bottom + 14; if (top + ch > vh - 10) top = Math.max(10, r.top - up - ch - 14);
    let left = Math.min(Math.max(10, r.left), vw - cw - 10);
    Object.assign(card.style, {top: top + "px", bottom: "auto", left: left + "px"});
  }
  function show() {
    const s = steps[i], el = target();
    D.querySelectorAll(".pp-tour-on").forEach(e => e.classList.remove("pp-tour-on"));
    if (el.classList.contains("pp-m")) el.classList.add("pp-tour-on");   // 단어는 빛나게, 설명창도 미리 띄운다
    card.querySelector(".n").textContent = (i + 1) + " / " + steps.length;
    card.querySelector(".t").textContent = s[1];
    card.querySelector(".b").innerHTML = s[2];
    card.querySelector(".prev").style.visibility = i ? "visible" : "hidden";
    card.querySelector(".next").textContent = i + 1 < steps.length ? "다음 ▶" : "시작하기";
    el.scrollIntoView({block: W.innerWidth < 700 ? "start" : "center"});   // 바로 옮겨야 위치 계산이 맞다
    place(); setTimeout(place, 150);
  }
  function finish() {                                              // 끝내기·건너뛰기
    root.remove();
    D.querySelectorAll(".pp-tour-on").forEach(e => e.classList.remove("pp-tour-on"));
    W.removeEventListener("resize", place, true); D.removeEventListener("scroll", place, true);
    try {                                                          // '봤음'을 이 브라우저에 바로 저장(앱이 바빠도 남게)
      const k = "paperpath-prefs-v1", v = JSON.parse(localStorage.getItem(k) || "{}") || {};
      v.tutorial_done = true; localStorage.setItem(k, JSON.stringify(v));
    } catch (e) {}
    const b = D.querySelector(".st-key-tour_done_box button"); if (b) b.click();   // 앱(내 계정 저장)에도 알린다
  }
  card.querySelector(".skip").onclick = finish;
  card.querySelector(".prev").onclick = () => { if (i) { i--; show(); } };
  card.querySelector(".next").onclick = () => { if (i + 1 < steps.length) { i++; show(); } else finish(); };
  W.addEventListener("resize", place, true); D.addEventListener("scroll", place, true);
  if (steps.length) show(); else finish();
})();
"""

# 안내 코드는 앱 화면(부모 문서)에 직접 심는다. Streamlit이 끼워 넣는 작은 창(iframe) 안에서 돌리면, 앱이 다시 그려질 때
# 그 창이 지워지면서 버튼 동작도 함께 사라져 '시작하기'를 눌러도 반응이 없었다(팀원 피드백).
TOUR_JS = """<script>
(function () {
  const D = window.parent.document, s = D.createElement("script");
  s.textContent = __CODE__;
  D.body.appendChild(s);
  s.remove();
})();
</script>"""


def open_tutorial() -> None:
    st.session_state.tour_pending = True


def finish_tour() -> None:
    """화면 안내를 끝냈거나 건너뛰었다: '봤음'을 이 브라우저·내 계정에 저장한다(다시 보려면 '사용법 보기')."""
    set_pref("tutorial_done", True)


def maybe_tutorial() -> None:
    """화면 맨 끝에서 부른다(가리킬 요소가 다 그려진 뒤). 처음 자료를 열었을 때 한 번, 또는 '사용법 보기'를 눌렀을 때
    실제 화면 위에서 요소를 하나씩 강조하며 안내한다. 안내는 브라우저에서만 움직이고, 끝나면 숨은 버튼으로 알려 온다."""
    ss = st.session_state
    with st.container(key="tour_done_box"):
        st.button("안내 끝", key="tour_done", on_click=finish_tour)
    first = not ss.prefs.get("tutorial_done") and ss.get("browser_loaded") and not ss.get("tour_shown")
    if not (first or ss.pop("tour_pending", False)):
        return
    ss.tour_shown = True                   # 건너뛰지 않고 새로 고쳐도 이번 화면에서는 다시 띄우지 않는다
    ss.tour_nonce = ss.get("tour_nonce", 0) + 1
    steps = json.dumps([list(t) for t in TOUR_STEPS], ensure_ascii=False)
    code = TOUR_CORE.replace("__STEPS__", steps).replace("__NONCE__", str(ss.tour_nonce))
    components.html(TOUR_JS.replace("__CODE__", json.dumps(code, ensure_ascii=False).replace("</", "<\\/")), height=0)


def resume_reading() -> None:
    """'이어서 보기': 지난번에 보던 쪽·절·부분·읽기 모드로 옮긴다."""
    ss = st.session_state
    last = ss.pop("resume_offer", None)
    if not last or ss.get("paper") is None:
        return
    pages = paper_pages(ss.paper)
    ss.paper_cur = last.get("page") if last.get("page") in pages else next(iter(pages))
    ss.body_cur = int(last.get("body") or 0)
    for k in ("paper_page", "section_pick"):
        ss.pop(k, None)
    set_view()
    ss.part_resume = int(last.get("part") or 0)
    if last.get("mode") in READ_MODES:
        ss.read_mode = last["mode"]
    rec = ss.browser_docs.get(last.get("doc") or "")
    if rec:                                # 이어 본 위치를 그대로 지난 위치로 둔다(부분 수는 다음 화면에서 채워짐)
        ss.browser_docs[rec["id"]] = {**rec, "last": {k: v for k, v in last.items() if k != "doc"}}


def dismiss_resume() -> None:
    st.session_state.resume_offer = None


def resume_prompt() -> None:
    """다시 연 문서에 지난 위치가 있으면 맨 위에 '이어서 보시겠어요?'(스크롤 위치는 기억하지 못해 그 부분의 처음으로)."""
    last = st.session_state.get("resume_offer")
    if not last:
        return
    with st.container(key="resume_box"):
        c1, c2, c3 = st.columns([4, 1.1, 0.9], vertical_alignment="center")
        c1.markdown(f'<div class="pp-resume">지난번에 <b>{esc(last.get("label") or "본문")}</b>까지 읽었어요. '
                    f'이어서 보시겠어요?</div>', unsafe_allow_html=True)
        c2.button("이어서 보기", key="resume_yes", type="primary", on_click=resume_reading, use_container_width=True)
        c3.button("처음부터", key="resume_no", on_click=dismiss_resume, use_container_width=True)


def track_position(new_section: bool = False, user: bool = True) -> None:
    """지금 읽는 위치(쪽·절·부분·읽기 모드)를 문서 기록에 남긴다. 바뀔 때만 저장(브라우저·내 계정).
    이동 버튼의 콜백에서 부른다: 콜백은 화면을 그리기 전에 돌아서, 같은 실행의 브라우저 저장에 바로 실린다
    (화면을 다 그린 뒤 부르면 브라우저 저장이 이미 끝나 한 번 늦게 저장됐다). new_section이면 새 절의 첫 부분.
    user: 사용자가 직접 옮긴 것(이어 보기 물음은 닫는다)."""
    ss = st.session_state
    if user:
        ss.resume_offer = None
    rec = ss.browser_docs.get(ss.get("current_doc") or "")
    if not rec or rec.get("kind") != "pdf" or ss.get("paper") is None or ss.get("resume_offer"):
        return
    part = 0 if new_section else (ss.get("part_pick") or 0)
    parts_n = 0 if new_section else ss.get("parts_n", 0)
    label = ss.source.get("section", "") + (f" ({part + 1}/{parts_n} 부분)" if parts_n else "")
    pos = {"page": ss.paper_cur, "body": ss.body_cur, "part": part, "mode": ss.get("read_mode"), "label": label}
    if rec.get("last") != pos:
        rec = {**rec, "last": pos}
        ss.browser_docs[rec["id"]] = rec
        ss.docs_pending[rec["id"]] = rec


def delete_doc(doc_id: str) -> None:
    ss = st.session_state
    ss.browser_docs.pop(doc_id, None)
    ss.docs_pending.pop(doc_id, None)
    ss.docs_delete.append(doc_id)
    user = current_user()
    if user:                               # 로그인했으면 내 계정에서도 지운다
        try:
            get_store().delete_doc(user, doc_id)
            ss.account_docs.pop(doc_id, None)
        except S.StoreError as e:
            ss.account_msg = str(e)


def on_saved_upload() -> None:
    """결과 파일(B) → 이 화면에 합치고, 브라우저 저장에도 넣는다(다음 실행에서 자동 저장)."""
    ss = st.session_state
    f = ss.get("saved_file")
    if f is None:
        return
    try:
        tr, gl = SV.unpack_items(SV.from_file(f.getvalue()))
    except ValueError as e:
        ss.saved_msg = None
        ss.input_error = str(e)
        return
    ss.translations.update(tr)
    ss.ai_gloss.update(gl)
    docs = SV.docs_from_file(f.getvalue())
    for rec in docs.values():
        remember_doc(rec)
    ss.input_error = None
    n = len({k for k, _ in tr} | set(gl))
    ss.saved_msg = (f"결과 {n}개 글과 문서 {len(docs)}개를 불러왔어요. 아래 '다시 열기'를 누르거나 같은 PDF를 넣으면 "
                    "저장된 번역으로 바로 열려요.")


def clear_browser() -> None:
    ss = st.session_state
    ss.browser_clear = True
    ss.saved_msg = "이 브라우저에 저장된 번역을 지웠어요(지금 화면의 결과는 새로고침하면 사라져요)."


def browser_sync() -> None:
    """A: 이 브라우저(localStorage)와 번역·뜻 풀이 결과를 맞춘다. 처음 열 때 저장된 결과를 불러오고,
    새로 생긴 결과만 저장한다. 컴포넌트는 화면에 보이지 않는다(높이 0)."""
    ss = st.session_state
    items = SV.pack_items(ss.translations, ss.ai_gloss)
    clear = ss.pop("browser_clear", False)
    if clear:                              # 지우기: 지금 화면에 남은 결과는 다시 저장하지 않는다(새 결과만 저장)
        ss.saved_sig = {k: SV.signature(v) for k, v in items.items()}
        ss.browser_keys = set()
    pending = {k: v for k, v in items.items() if ss.saved_sig.get(k) != SV.signature(v)}
    if clear:
        ss.browser_docs, ss.docs_pending = {}, {}
    docs, deletes, prefs = ss.docs_pending, ss.docs_delete, ss.prefs_pending
    if pending or clear or docs or deletes or prefs:
        ss.browser_ver += 1
    with st.container(key="browser_store_box"):
        value = _browser_store(save=pending or None, save_docs=docs or None, delete_docs=deletes or None, clear=clear,
                               save_prefs=prefs or None, ver=str(ss.browser_ver), max=SV.MAX_BYTES,
                               max_docs=SV.MAX_DOCS, key="browser_store", default=None)
    ss.docs_pending, ss.docs_delete, ss.prefs_pending = {}, [], {}
    for k, v in pending.items():
        ss.saved_sig[k] = SV.signature(v)
        ss.browser_keys.add(k)
    if value and not ss.browser_loaded:
        ss.browser_loaded = True
        for k, v in (value.get("prefs") or {}).items():
            ss.prefs.setdefault(k, v)
        stored = value.get("items") or {}
        ss.browser_keys |= set(stored)
        for k, rec in (value.get("docs") or {}).items():
            if SV.valid_doc(rec):
                ss.browser_docs.setdefault(k, rec)
        tr, gl = SV.unpack_items(stored)
        for k, r in tr.items():
            ss.translations.setdefault(k, r)
        for k, g in gl.items():
            ss.ai_gloss.setdefault(k, g)
        for k, item in stored.items():
            ss.saved_sig.setdefault(k, SV.signature(item))


def on_pdf_drop() -> None:
    """PDF를 끌어다 놓거나 고르면 버튼 없이 바로 분석한다."""
    ss = st.session_state
    f = ss.get("pdf_file")
    if f is None:                          # 올린 파일을 지운 경우: 지금 보던 결과는 그대로 둔다
        return
    with st.spinner("PDF에서 글자를 읽고 절을 나누는 중…"):
        ss.input_error = load_pdf(f.getvalue(), f.name)


THUMB_SIZE = (360, 220)               # 저장 문서 카드의 대표 그림 크기(px)
THUMB_MAX_BYTES = 40_000              # 이보다 크면 저장하지 않는다(브라우저 저장 공간)


def doc_thumb(figures) -> str:
    """저장 문서 카드에 쓸 대표 그림: 4쪽 안의 Figure 1, 없으면 1~2쪽에서 가장 큰 그림을 작은 JPEG(data URI)로.
    대개 논문 맨 앞의 큰 그림이 그 논문을 대표한다. 찾지 못하면 빈 글자."""
    from PIL import Image

    usable = [t for t in figures if t.png and not t.whole_page]
    first = [t for t in usable if t.num == 1 and t.page <= 4]
    early = [t for t in usable if t.page <= 2]
    pick = first[0] if first else (max(early, key=lambda t: t.width * t.height) if early else None)
    if pick is None:
        return ""
    try:
        im = Image.open(io.BytesIO(pick.png)).convert("RGB")
        im.thumbnail(THUMB_SIZE)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70, optimize=True)
    except Exception:
        return ""
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri if len(uri) <= THUMB_MAX_BYTES else ""


def load_pdf(data: bytes, name: str) -> str | None:
    """PDF → 절 나누기. 실패하면 화면에 보여 줄 이유를 돌려준다."""
    ss = st.session_state
    if len(data) > MAX_PDF_MB * 1024 * 1024:
        return f"PDF가 너무 커요({len(data) / 1024 / 1024:.1f}MB). {MAX_PDF_MB}MB 이하만 올릴 수 있어요."
    try:
        pdf = extract_pdf(data)
    except PdfError as e:
        return str(e)
    paper = split_paper(pdf.lines, pdf.title or name)
    if not paper.sections:
        return "PDF에서 본문을 찾지 못했어요."
    try:
        ss.paper_tables = find_tables(data)       # 표 캡션(정규식) + 가로선으로 자른 표 이미지
    except Exception:                            # 표 찾기가 실패해도 본문 분석은 그대로 보여 준다
        ss.paper_tables = []
    try:
        ss.paper_figures = find_figures(data)     # 그림 캡션(정규식) + 캡션 위 그림 영역
    except Exception:
        ss.paper_figures = []
    paper.notes = [f"PDF {pdf.read_pages}쪽을 읽었어요" + (f"(2단 편집 {pdf.two_column_pages}쪽은 왼쪽 단 → 오른쪽 단 순서로)"
                                                       if pdf.two_column_pages else "") + "."] + pdf.notes + paper.notes
    ss.paper = paper
    ss.paper_cur, ss.body_cur = PAPER_PAGES[0], 0
    for k in ("paper_page", "section_pick"):     # 앞 논문의 선택 위젯 상태를 지운다
        ss.pop(k, None)
    ss.source = {"kind": "pdf", "title": paper.title}
    set_view()
    ss.show_input = False
    extra = {"notes": paper.notes}
    thumb = doc_thumb(ss.paper_figures)
    if thumb:
        extra["thumb"] = thumb
    remember_doc(SV.doc_record("pdf", paper.title or name,
                               [{"title": s.title, "text": s.text, "subheads": s.subheads, "sub_at": s.sub_at}
                                for s in paper.sections], extra), current=True)
    return None


def seed_rule_widgets() -> None:
    """규칙 체크박스는 패널이 닫히면 위젯 상태가 지워지므로, 매번 active_rules에서 다시 채운다."""
    for r in P.TOGGLEABLE_RULES:
        st.session_state.setdefault(f"rule_{r.name}", r.name in st.session_state.active_rules)


def on_rule_change(name: str) -> None:
    rules = set(st.session_state.active_rules)
    (rules.add if st.session_state[f"rule_{name}"] else rules.discard)(name)
    st.session_state.active_rules = rules


def mark_chips(a) -> None:
    """02 표시 칩: 누르면 그 종류의 형광펜만 켜고 끈다(색이 한꺼번에 많으면 읽기 피곤하므로).
    끈 표시도 칩은 흐리게 남아 다시 켤 수 있다. 0개는 처음부터 숨긴다. 칩 색은 원문 형광펜 색."""
    ss = st.session_state
    rules = a.active_rules - {JARGON_RULE}
    full = dict(a.counts if rules == P.DEFAULT_ACTIVE_RULES else analyze(a.text).counts)   # 끈 표시도 개수는 그대로
    full["jargon"] = len({m.tip.split(":")[0] for m in hard_marks(a)})                   # 어려운 단어 가짓수
    opts = [k for k, _, _ in CHIPS if full[k]]
    if not opts:
        st.markdown('<div class="pp-chips"><span class="pp-meta">표시할 표현이 없어요</span></div>', unsafe_allow_html=True)
        return
    label = {k: f":{CHIP_COLOR[color]}[●] {name} {full[k]}" for k, name, color in CHIPS}
    ss.mark_pills = [k for k in opts if CHIP_RULE[k] in ss.active_rules]   # 규칙 체크박스와 같은 상태를 쓴다
    st.pills("형광펜 켜고 끄기", opts, selection_mode="multi", format_func=label.get, key="mark_pills",
             on_change=on_chip_change, args=(tuple(opts),), label_visibility="collapsed",
             help="누르면 그 표시의 형광펜을 끄고, 다시 누르면 켜요")


def on_chip_change(opts: tuple) -> None:
    ss = st.session_state
    rules = set(ss.active_rules)
    on = set(ss.mark_pills or [])
    for k in opts:
        name = CHIP_RULE[k]
        (rules.add if k in on else rules.discard)(name)
        ss[f"rule_{name}"] = k in on          # '읽기 전용 단계 보기'의 체크박스도 같이 맞춘다
    ss.active_rules = rules


def rule_toggles(container_cols: int = 3) -> None:
    seed_rule_widgets()
    cols = st.columns(container_cols)
    for i, r in enumerate(P.TOGGLEABLE_RULES):
        with cols[i % container_cols]:
            st.checkbox(f"{r.label} ({r.name})", key=f"rule_{r.name}", on_change=on_rule_change, args=(r.name,),
                        help=r.desc)


# ================================================================ 렌더링 도우미

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def as_wrappers(u) -> list:
    """밑줄 값(하나, 목록, None)을 목록으로 맞춘다."""
    if not u:
        return []
    return [u] if isinstance(u[0], int) else list(u)


def render_layers(text: str, spans, underline=None) -> str:
    """형광펜(spans)과 밑줄(underline: 핵심 내용·용어 설명)을 함께 그린다.

    spans: [(시작, 끝, CSS 클래스, 툴팁)] — 서로 겹치지 않는다.
    underline: (시작, 끝, 종류[, 툴팁]) 하나 또는 목록. 앞의 것이 우선이고 겹치는 뒤의 것은 버린다.
    형광펜을 자르지 않도록 밑줄 경계를 넓힌다. 밑줄 안의 형광펜은 CSS에서 배경을 빼고 글자색만 남긴다.
    """
    spans = sorted(sp for sp in spans if 0 <= sp[0] < sp[1] <= len(text))
    wraps = []
    for w in as_wrappers(underline):
        ua, ub, kind = w[0], w[1], w[2]
        if not (0 <= ua < ub <= len(text)):
            continue
        for a, b, *_ in spans:
            if a < ua < b:
                ua = a
            if a < ub < b:
                ub = b
        # 겹치지 않으면 그대로, 용어 설명이 핵심 내용 밑줄 안에 통째로 들어가면 안쪽에 겹쳐 그린다(교차하면 버림)
        inside = [o for o in wraps if o[0] <= ua and ub <= o[1]]
        crossing = [o for o in wraps if not (ub <= o[0] or ua >= o[1]) and o not in inside]
        if crossing or (inside and kind != "def"):
            continue
        wraps.append((ua, ub, kind, w[3] if len(w) > 3 else UL_TITLE.get(kind, "")))
    cuts = sorted({0, len(text), *(e for sp in spans for e in sp[:2]), *(e for w in wraps for e in w[:2])})
    out = []
    for x, y in zip(cuts, cuts[1:]):
        for w in sorted((w for w in wraps if w[0] == x), key=lambda w: -(w[1] - w[0])):   # 바깥 밑줄부터 연다
            out.append(f'<span class="pp-u {w[2]}" title="{esc(w[3])}">')
        sp = next((sp for sp in spans if sp[0] == x and sp[1] == y), None)
        if sp:
            tip = esc(sp[3])
            extra = sp[4] if len(sp) > 4 else ""        # 표·그림 미리보기처럼 표시 안에 넣을 HTML
            out.append(f'<span class="{sp[2]}" tabindex="0" data-tip="{tip}" aria-label="{tip}">'
                       f'{esc(text[x:y])}{extra}</span>')
        else:
            out.append(esc(text[x:y]))
        out.extend("</span>" for w in wraps if w[1] == y)
    return "".join(out)


def render_ko(ko: str, ko_marks, active, underline=None, en_tips: dict | None = None, pop=None) -> str:
    """번역문 형광펜·밑줄. 툴팁은 영어 원문의 같은 표시와 같은 설명(뜻·규칙)을 보여 주고,
    위치는 번역기가 옮긴 태그에서 복원한 것이라 '영어 ○○에 대응'을 덧붙인다. pop=(표·그림 맥락, 문장 번호)."""
    spans, last = [], -1
    for a, b, cat, en in ko_marks:
        if CAT_RULE.get(cat) in active and a >= last:
            tip = (en_tips or {}).get((cat, (en or "").lower())) or MARK_LABEL[cat]
            extra = ""
            if cat == "reference" and pop and pop[0]:
                _, extra = float_pop(en or "", pop[1], pop[0])
            spans.append((a, b, f"pp-m {cat}", f"{tip} · 영어 '{en}'에 대응 (번역기가 옮긴 위치)", extra))
            last = b
    return render_layers(ko, spans, underline)


def mark_tip(m) -> str:
    """표시 툴팁: '범주 · 설명'. 설명이 이미 범주 이름으로 시작하면(확신 · …) 두 번 쓰지 않는다."""
    label = MARK_LABEL[m.category]
    return m.tip if m.tip.startswith(label) else f"{label} · {m.tip}"


def en_tips(a, i: int) -> dict[tuple[str, str], str]:
    """i번째 문장의 영어 표시 툴팁: (범주, 영어 표현 소문자) → 영어 쪽과 같은 설명. 반복 용어는 뜻 풀이."""
    s = a.sentences[i]
    tips = st.session_state.get("term_tips", {}).get(text_key(a.text), {})
    out = {}
    for m in all_marks(a):                 # 어려운 단어도 한국어 쪽에서 같은 설명이 뜨게
        if m.end > s.start and m.start < s.end:
            phrase = a.text[m.start:m.end]
            out.setdefault((m.category, phrase.lower()),
                           term_tip(phrase, m.tip, tips) if m.category == "term" else mark_tip(m))
    return out


def card_underlines(a) -> dict[int, list]:
    """문장 번호 → 그 문장의 밑줄 목록(문장 안 위치). 핵심 내용(신호 문구로 찾은 카드)이 먼저, 용어 설명이 뒤."""
    out: dict[int, list] = {}
    for c in a.skeleton:
        if c.sentence_idx is not None and c.underline:
            out.setdefault(c.sentence_idx, []).append((*c.underline, c.kind))
    for x, y, tip in a.explanations:
        s = next((s for s in a.sentences if s.start <= x < s.end), None)
        if s:
            out.setdefault(s.idx, []).append((x - s.start, min(y, s.end) - s.start, "def", tip))
    return out


def translation_layers(raw_text: str):
    """번역에 보낼 문장별 영어 형광펜·밑줄. 규칙 켜고 끄기와 상관없이 기본 규칙으로 만들어 캐시를 안정시킨다."""
    full = analyze(raw_text)
    uls = card_underlines(full)
    marks = [[(m.start - s.start, m.end - s.start, m.category) for m in full.marks if s.start <= m.start and m.end <= s.end]
             for s in full.sentences]
    jargon = G.jargon_marks(full)   # 단어표의 한국어 대응어를 얻으려는 태그(화면에는 그리지 않음)
    for s, row in zip(full.sentences, marks):
        row += [(x - s.start, y - s.start, c) for x, y, c in jargon if s.start <= x and y <= s.end
                and all(y - s.start <= a or x - s.start >= b for a, b, _ in row)]
    return marks, [[w[:3] for w in uls.get(s.idx, [])] for s in full.sentences]


def render_marked(text: str, start: int, end: int, marks, underline=None) -> str:
    """원문 문장(text[start:end]) 렌더링. underline은 문장 안 위치."""
    ss = st.session_state
    tips = ss.get("term_tips", {}).get(text_key(text), {})
    ctx = ss.get("float_ctx", {}).get(text_key(text))
    spans = []
    for m in marks:
        if not (m.end > start and m.start < end):
            continue
        phrase = text[m.start:m.end]
        tip = term_tip(phrase, m.tip, tips) if m.category == "term" else mark_tip(m)
        extra = ""
        if m.category == "reference" and ctx:          # 'Table 2'·'Fig. 3': 마우스를 올리면 그 표·그림 미리보기
            more, extra = float_pop(phrase, ctx[2].get(start), ctx[:2])
            tip += more
        spans.append((max(m.start, start) - start, min(m.end, end) - start, f"pp-m {m.category}", tip, extra))
    return render_layers(text[start:end], spans, underline)


def term_tip(term: str, tip: str, tips: dict[str, str]) -> str:
    """반복 용어 툴팁: 뜻을 알면 뜻을 먼저, 모르면 뜻을 받는 방법을 알려 준다."""
    n = TIMES.search(tip)
    times = f" · 이 글에 {n.group(1)}번" if n else ""
    mean = tips.get(G.term_key(term))
    if mean:
        return f"{term}: {mean}{times}"
    return f"{term}{times} · 뜻은 '번역하기'나 단어표의 'AI로 뜻 풀이 받기'를 누르면 여기에 떠요"


def skeleton_card(card, a, ko: str | None = None, ko_ul=None) -> str:
    title, color = KIND_META[card.kind]
    basis_cls = card.basis
    basis_tip = {"signal": "신호 문구로 찾은 문장이에요. 밑줄은 신호 문구가 가리키는 핵심 내용이에요.",
                 "position": "신호 문구가 없어 위치로 고른 추정 문장이에요. 근거가 없어 핵심 내용 밑줄은 긋지 않아요.",
                 "none": "규칙에 맞는 문장을 찾지 못했어요."}[card.basis]
    reason = card.reason
    if card.sentence_idx is None:
        body, reason = missing_card_text(card, a)
        basis = BASIS_TEXT["none"]
    else:
        s = a.sentences[card.sentence_idx].text
        spans = [(*card.signal, f"pp-sig-{color}", f"신호 문구 · {card.reason}")] if card.signal else []
        ul = card_underlines(a).get(card.sentence_idx, [])
        body = (f'<div class="pp-sk-text"><span class="pp-sk-no">문장 {card.sentence_idx + 1}</span>'
                f'{render_layers(s, spans, ul)}</div>')
        if len(s) > SK_CLAMP_CHARS:        # 긴 문장은 4줄까지만 보이고 눌러서 전체(세 카드 높이를 맞춤)
            body = f'<details class="pp-sk-more"><summary>{body}<span class="pp-more-btn">전체 보기</span></summary></details>'
        if ko:
            body += f'<div class="pp-sk-ko">{render_layers(ko, [], ko_ul)}</div>'
        basis = card.label
    est = " est" if card.basis == "position" else ""
    return (f'<div class="pp-panel pp-card {color}{est}"><div class="pp-card-head"><div class="pp-kind">'
            f'<span class="pp-kdot"></span>{title}</div><div class="pp-basis {basis_cls}" title="{esc(basis_tip)}">'
            f'{esc(basis)}</div></div>'
            f'{body}<div class="pp-reason">{ICON["info"]}<span>{esc(reason)}</span></div></div>')


def missing_card_text(card, a) -> tuple[str, str]:
    """찾지 못한 카드: 비어 보이지 않게 왜 없는지를 문장으로 알려 준다(지어낸 문장으로 채우지 않음)."""
    first_used = any(c.sentence_idx == 0 and c.kind != card.kind for c in a.skeleton)
    if card.kind == "problem" and first_used:
        msg = "이 글은 문제를 따로 밝히지 않고 첫 문장부터 바로 방법을 소개해요."
    else:
        msg = {"problem": "문제를 직접 밝힌 문장이 없어요.", "method": "방법을 소개하는 문장이 없어요.",
               "result": "결과를 알리는 문장이 없어요."}[card.kind]
    return (f'<div class="pp-sk-missing">{esc(msg)}</div>',
            "지어낸 문장으로 채우지 않아요. 흐름도의 배경·보충 문장에서 맥락을 확인해 보세요.")


ROLE_META = {"background": ("배경", "gray"), "problem": ("문제", "blue"), "method": ("방법", "teal"),
             "result": ("결과", "violet"), "limit": ("한계", "gray")}


def flow_map(a, tr=None) -> str:
    """논문 흐름도: 문장 역할(배경 → 문제 → 방법 → 결과 → 한계)을 순서대로 이어 보여 준다."""
    uls = {c.sentence_idx: c.underline for c in a.skeleton if c.sentence_idx is not None and c.underline}
    in_cards = {c.sentence_idx for c in a.skeleton if c.sentence_idx is not None}
    steps = []
    for role in ROLE_ORDER:
        idxs = [i for i, r in enumerate(a.roles) if r == role]
        if not idxs:
            continue
        name, color = ROLE_META[role]
        i = idxs[0]
        s = a.sentences[i].text
        if i in in_cards:                  # 01 카드에 이미 나온 문장은 다시 쓰지 않고 가리키기만 한다
            steps.append(f'<div class="pp-flow-step {color}"><div class="pp-flow-head"><b>{name}</b>'
                         + "".join(f'<span class="pp-flow-no">{j + 1}</span>' for j in idxs)
                         + f'</div><div class="pp-flow-text pp-meta">문장 {i + 1} · 위 01 카드 참고</div></div>')
            continue
        if i in uls:
            snippet = s[uls[i][0]:uls[i][1]]
        else:
            words = s.split()
            snippet = " ".join(words[:14]) + (" …" if len(words) > 14 else "")
        ko = ""
        if tr and tr.ko and i < len(tr.ko):
            k = tr.ko[i]
            ko = f'<div class="pp-flow-ko">{esc(k[:60] + ("…" if len(k) > 60 else ""))}</div>'
        nums = "".join(f'<span class="pp-flow-no">{j + 1}</span>' for j in idxs)
        steps.append(f'<div class="pp-flow-step {color}"><div class="pp-flow-head"><b>{name}</b>{nums}</div>'
                     f'<div class="pp-flow-text">{esc(snippet)}</div>{ko}</div>')
    if not steps:
        return '<div class="pp-meta">문장이 없어요.</div>'
    detail = [i + 1 for i, r in enumerate(a.roles) if r == "detail"]
    note = (f'<div class="pp-flow-note">보충 문장: {", ".join(f"문장 {j}" for j in detail)} '
            f'(신호 문구가 없어 흐름 단계로 분류하지 않았어요)</div>') if detail else ""
    arrow = '<div class="pp-flow-arrow" aria-hidden="true">→</div>'
    return f'<div class="pp-flow">{arrow.join(steps)}</div>{note}'


def names_line(a, rows) -> str:
    """단어표에 넣지 않은 모델·데이터셋·기관 이름을 한 줄로(뜻을 풀 용어가 아니라 이름이라서)."""
    in_table = {e.term for e in rows}
    names = [n for n in G.proper_names(a) if n not in in_table]
    if not names:
        return ""
    chips = "".join(f'<span class="pp-chip gray">{esc(n)}</span>' for n in names[:30])
    more = f' <span class="pp-meta">외 {len(names) - 30}개</span>' if len(names) > 30 else ""
    return (f'<div class="pp-names"><span class="pp-names-label">이 글에 나온 모델·데이터셋·기관 이름 {len(names)}개</span>'
            f'<span class="pp-meta"> · 뜻을 풀 용어가 아니라 이름이라 단어표에서 뺐어요</span>'
            f'<div class="pp-chips" style="margin-top:4px">{chips}{more}</div></div>')


def glossary_table(rows) -> str:
    ai = any(e.ai_meaning for e in rows)
    if not rows:
        return '<div class="pp-meta">이 글에는 한국어로 옮겨도 뜻이 안 풀릴 전문용어가 없어요.</div>'
    body = "".join(
        f"<tr><td><b>{esc(e.term)}</b></td><td>{esc(', '.join(G.KIND_LABEL[k] for k in e.kinds))}</td>"
        f"<td>{esc(e.level)}</td>"
        f"<td>{e.count}</td><td>{('문장 ' + str(e.first_sentence)) if e.first_sentence else ''}</td>"
        f"<td>{esc(e.explanation)}</td><td>{esc(e.korean)}</td>"
        + (f'<td class="pp-ai">{esc(e.ai_meaning)}</td>' if ai else "") + "</tr>" for e in rows)
    ai_head = '<th>AI 풀이 <span class="pp-ai-badge">참고용</span></th>' if ai else ""
    # 짧은 칸(종류·난이도·횟수·위치)은 좁게, 설명·뜻 칸은 넓게. 값이 없는 칸은 '-' 대신 비운다(열 이름과 헷갈리지 않게)
    cols = ('<colgroup><col style="width:16%"><col style="width:10%"><col style="width:6%"><col style="width:6%">'
            '<col style="width:9%"><col style="width:23%"><col style="width:12%">'
            + ('<col style="width:18%">' if ai else "") + '</colgroup>')
    return (f'<div class="pp-panel" style="padding:6px 8px; overflow-x:auto"><table class="pp-table pp-gloss">{cols}<thead><tr>'
            f'<th>용어</th><th>종류</th><th>난이도</th><th>횟수</th><th>처음 나온 곳</th><th>풀네임·설명(원문)</th><th>한국어</th>'
            f'{ai_head}</tr></thead><tbody>{body}</tbody></table></div>')


SIGNAL_ROLE = {"problem": "문제", "method": "방법", "result": "결과"}


def paper_overview(paper) -> None:
    """PDF 논문 전체 지도: 절 고르기, 절별 요약 표(정규식이 찾은 핵심 문장), 논문 전체 어려운 단어표."""
    ss = st.session_state
    titles = [s.title for s in paper.sections]
    pages = paper_pages(paper)
    st.markdown(sec_head("00", "논문 쪽 고르기", f"{paper.title[:70]} · {len(titles)}개 절 · 약 {paper.chars:,}자"),
                unsafe_allow_html=True)
    if paper.notes:
        st.caption(" ".join(paper.notes))
    with st.container(key="paper_nav"):
        ss.setdefault("paper_page", ss.source.get("page"))     # 입력 칸을 열었다 닫으면 위젯 상태가 지워져 다시 채운다
        labels = {p: (f"{p} · {len(ids)}개 절" if p == "본문" else p) for p, ids in pages.items()}
        st.markdown('<div class="pp-nav-label">쪽 고르기 · 초록 / 본문 / 결론</div>', unsafe_allow_html=True)
        st.segmented_control("쪽", list(pages), format_func=labels.get, key="paper_page", on_change=on_page_change,
                             label_visibility="collapsed")
        if "결론" not in pages:
            st.caption("결론·논의 절 제목을 찾지 못해 '결론' 쪽이 없어요. 마지막 본문 절을 확인해 보세요.")
        if ss.source.get("page") == "본문":
            body = pages["본문"]
            ss.setdefault("section_pick", ss.body_cur)
            st.markdown('<div class="pp-nav-label">본문 절 고르기</div>', unsafe_allow_html=True)
            st.pills("본문 절 고르기", list(range(len(body))), format_func=lambda j: titles[body[j]], key="section_pick",
                     on_change=pick_section, label_visibility="collapsed")

    analyses = [analyze(s.text) for s in paper.sections]
    rows = []
    for i, (sec, a) in enumerate(zip(paper.sections, analyses)):
        found = [(SIGNAL_ROLE[c.kind], a.sentences[c.sentence_idx].text) for c in a.skeleton
                 if c.sentence_idx is not None and c.basis == "signal"]
        key = "<br>".join(f'<span class="pp-chip blue">{r}</span> {esc(t[:110] + ("…" if len(t) > 110 else ""))}'
                          for r, t in found[:2]) or '<span class="pp-meta">신호 문구로 찾은 문장 없음</span>'
        subs = esc(", ".join(sec.subheads)) or "-"
        now = ' style="background:#eef4fb"' if i in ss.source.get("section_ids", []) else ""
        rows.append(f"<tr{now}><td><b>{esc(sec.title)}</b></td><td>{len(a.sentences)}</td><td>{key}</td>"
                    f"<td>{subs}</td></tr>")
    tables = ss.get("paper_tables") or []
    figures = ss.get("paper_figures") or []
    # 펼침 칸 셋을 하나로 묶어(안은 탭) 01 뼈대 카드가 첫 화면에 들어오게 한다
    with st.expander(f"논문 전체 보기 · 절별 지도 · 표 {len(tables)}개·그림 {len(figures)}개 · 전체 단어표"):
        st.caption("참고문헌·부록·각주·그림/표 안 글자·저자 정보는 분석에서 뺐어요.")
        tab_map, tab_fl, tab_gl = st.tabs(["절별 지도", f"표·그림 {len(tables) + len(figures)}", "전체 단어표"])
    with tab_map:
        st.markdown(f'<div class="pp-panel" style="padding:6px 8px; overflow-x:auto"><table class="pp-table pp-overview">'
                    f'<thead><tr><th>절</th><th>문장</th><th>신호 문구로 찾은 핵심 문장</th><th>소제목</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></div>', unsafe_allow_html=True)
    with tab_fl:
        st.caption("본문을 읽을 때는 표·그림을 처음 언급한 문장 바로 아래에 넣어 보여 줘요. 여기는 전체 목록이에요.")
        if not tables and not figures:
            st.caption("'Table 1:'·'Figure 1:'처럼 시작하는 설명(캡션)을 찾지 못했어요.")
        for t in tables + figures:
            table_card(t, "all")
    with tab_gl:
        whole = analyze("\n\n".join(s.text for s in paper.sections))
        entries = G.build(whole)
        st.download_button("전체 단어표 내려받기(CSV)", G.to_csv(entries), file_name="paperpath_paper_glossary.csv",
                           mime="text/csv", key="paper_glossary_csv", disabled=not entries)
        st.markdown(glossary_table(entries), unsafe_allow_html=True)
        st.markdown(names_line(whole, entries), unsafe_allow_html=True)
        st.caption("'처음 나온 곳'은 모든 절을 이어 붙였을 때의 문장 번호예요. 한국어 대응어는 절마다 번역했을 때 그 절의 단어표에 나와요.")


def sec_head(num: str, title: str, note: str = "") -> str:
    return (f'<div class="pp-sec"><div class="pp-sec-title"><span class="pp-sec-num">{num}</span>{esc(title)}</div>'
            f'<div class="pp-sec-note">{esc(note)}</div></div>')


# ================================================================ 상단 바·소개

def go_home() -> None:
    """로고를 누르면: '논문 읽기'의 첫 화면(히어로·자료 넣기·저장된 문서). 읽던 자료와 번역은 그대로 남아 '다시 열기'로 돌아온다."""
    ss = st.session_state
    ss.page = PAGES[0]
    ss.show_input = True
    ss.resume_offer = None


def topbar() -> None:
    ok = engine_ok()
    with st.container(key="topbar"):
        c1, c2, c3 = st.columns([1.25, 2.1, 1.25], vertical_alignment="center")
        with c1.container(key="brand_home"):   # 로고·이름을 누르면 첫 화면(읽던 자료는 그대로 남는다)
            st.markdown(f'<div class="pp-brand"><div class="pp-mark">{ICON["doc"]}</div><div>'
                        f'<div class="pp-name">PaperPath</div><div class="pp-sub">처음 읽는 영어 논문 도우미</div></div></div>',
                        unsafe_allow_html=True)
            st.button("첫 화면으로", key="go_home", on_click=go_home, help="첫 화면으로")
        with c2, st.container(key="nav"):
            st.radio("페이지", PAGES, key="page", horizontal=True, label_visibility="collapsed")
        status = ('<span class="pp-dot"></span>규칙 엔진 준비됨' if ok
                  else '<span class="pp-dot err"></span>규칙 엔진 오류')
        if not auth_ready():
            c3.markdown(f'<div class="pp-status"><span role="status">{status}</span>'
                        f'<span class="pp-avatar" aria-hidden="true">PP</span></div>', unsafe_allow_html=True)
            return
        s1, s2 = c3.columns([1.3, 1], vertical_alignment="center")
        s1.markdown(f'<div class="pp-status"><span role="status">{status}</span></div>', unsafe_allow_html=True)
        if current_user():
            try:
                name = st.user.get("name") or "로그인됨"
            except Exception:
                name = "로그인됨"
            s2.button("로그아웃", key="logout_btn", on_click=st.logout, use_container_width=True,
                      help=f"{name} · 내 계정 기록은 남아요")
        else:
            s2.button("로그인", key="login_btn", on_click=st.login, icon=":material/login:", use_container_width=True,
                      help="Google 계정으로 로그인하면(선택) 번역 결과가 내 계정에 저장돼 다른 PC에서도 이어 볼 수 있어요")


def source_flag() -> str:
    src = st.session_state.source
    if src["kind"] == "none":
        return "아직 넣은 자료가 없어요"
    if src["kind"] == "demo":
        return "화면 예시 데이터 · 직접 작성, 실제 논문 아님"
    if src["kind"] == "arxiv":
        return f"arXiv {src['id']} · {src.get('title', '')[:40]} · 메타데이터 CC0"
    if src["kind"] == "pdf":
        return f"PDF · {src.get('title', '')[:36]} · {src.get('section', '')} · 서버에 저장하지 않음"
    return "붙여 넣은 텍스트 · 앱에 저장하지 않음"


# ================================================================ 논문 읽기

def input_panel() -> None:
    ss = st.session_state
    with st.container(key="input_panel"):
        c1, c2, c5 = st.columns([1.6, 2.4, 2.3], vertical_alignment="center")
        c1.markdown(f'<div class="pp-input-copy"><div class="pp-lead">{ICON["search"]}</div><div>'
                    f'<div class="pp-field-label">분석할 논문 넣기</div>'
                    f'<div class="pp-field-hint">논문 PDF · arXiv ID · 직접 붙여 넣기</div></div></div>', unsafe_allow_html=True)
        with c2:
            mode = st.segmented_control("입력 방식", MODES, key="input_mode", label_visibility="collapsed") or M_PDF
        c5.markdown(f'<div class="pp-privacy">{ICON["shield"]}<span>올린 파일과 붙여 넣은 텍스트는 서버에 저장하지 않아요. '
                    f'읽은 문서와 번역 결과는 이 브라우저에만 저장돼요.<br>번역 시 {engine_name()}로 전송됨을 미리 안내합니다.</span></div>',
                    unsafe_allow_html=True)
        clicked = False
        if mode == M_PDF:
            with st.container(key="pdf_drop"):
                st.markdown('<div class="pp-drop-kicker">논문 번역을 시작해 보세요</div>'
                            '<div class="pp-drop-title">PDF 파일을 여기에 끌어다 놓으세요</div>'
                            f'<div class="pp-drop-hint">PDF 업로드 · arXiv ID · 텍스트 입력 · 놓으면 바로 절별로 나눠 분석해요 · '
                            f'{MAX_PDF_MB}MB 이하, 글자를 선택할 수 있는 PDF</div>', unsafe_allow_html=True)
                st.file_uploader("논문 PDF", type=["pdf"], key="pdf_file", label_visibility="collapsed",
                                 on_change=on_pdf_drop,
                                 help="스캔한 이미지 PDF는 글자가 없어 읽지 못해요.")
        elif mode == M_ARXIV:
            i1, i2 = st.columns([3, 1], vertical_alignment="center")
            i1.text_input("arXiv ID", key="arxiv_id", placeholder="arXiv ID  예: 2402.08910",
                          label_visibility="collapsed")
            clicked = i2.button("초록 불러오기", type="primary", use_container_width=True, key="load_btn")
        else:
            st.text_area("영어 텍스트", key="paste_text", height=150, label_visibility="collapsed",
                         placeholder="영어 논문 초록이나 단락을 붙여 넣으세요. PDF 복사 잔재(줄바꿈·쪽번호·머리말)는 자동으로 정리해요.")
            clicked = st.button("분석하기", type="primary", key="load_btn")
        b1, b2 = st.columns([1, 1])
        b1.button("예시 문단으로 보기", key="demo_btn", type="tertiary", on_click=load_demo)
        if ss.input_text:                  # 다시 넣다가 그만두면 보던 결과로 돌아간다
            b2.button("← 보던 결과로 돌아가기", key="back_btn", type="tertiary", on_click=set_show_input, args=(False,))
        saved_panel()

        if clicked:
            ss.input_error = None
            if mode == M_ARXIV:
                with st.spinner("arXiv에서 초록을 불러오는 중…"):
                    try:
                        row = fetch_abstract(ss.arxiv_id)
                        ss.input_text = row["abstract"]
                        ss.source = {"kind": "arxiv", "id": row["id"], "title": row["title"]}
                        ss.paper = None
                        ss.show_input = False
                        remember_doc(SV.doc_record("arxiv", row["title"], [{"title": "Abstract", "text": row["abstract"]}],
                                                   {"arxiv_id": row["id"]}), current=True)
                    except ArxivError as e:
                        ss.input_error = str(e)
            else:
                txt = ss.paste_text.strip()
                letters = sum(ch.isalpha() for ch in txt)
                if not txt:
                    ss.input_error = "붙여 넣은 텍스트가 없어요."
                elif len(txt) > MAX_PASTE:
                    ss.input_error = f"텍스트가 너무 길어요({len(txt):,}자). {MAX_PASTE:,}자 이하로 나눠 넣어 주세요."
                elif letters and sum(ch.isascii() for ch in txt if ch.isalpha()) / letters < 0.8:
                    ss.input_error = "영어 텍스트가 아닌 것 같아요. 영어 초록이나 단락을 넣어 주세요."
                else:
                    ss.input_text = txt
                    ss.source = {"kind": "paste"}
                    ss.paper = None
                    ss.show_input = False
                    first = txt.split(".")[0][:60]
                    remember_doc(SV.doc_record("paste", f"붙여 넣은 글 · {first}", [{"title": "붙여 넣은 글", "text": txt}]),
                                 current=True)
            if not ss.input_error:
                st.rerun()                 # 위쪽 출처 표시까지 새 글 기준으로 다시 그린다
        if ss.input_error:
            st.error(ss.input_error)


def steps_panel(a) -> None:
    with st.container(key="steps_panel"):
        n_def = sum(1 for v in a.acronyms.values() if v)
        picks = ", ".join(f"{KIND_META[c.kind][0].split(' ·')[0]}={'문장 ' + str(c.sentence_idx + 1) if c.sentence_idx is not None else '없음'}"
                          for c in a.skeleton)
        steps = [
            ("① 전처리", f"최소 방어선 5종 적용. 지운 줄 {len(a.removed_lines)}개"),
            ("② 문장 분리", f"{len(a.sentences)}문장. 수식·인용·URL 안 마침표는 가림"),
            ("③ 약어 탐지", f"후보 {len(a.acronyms)}개, 풀네임 {n_def}개"),
            ("④ 뼈대 선택", picks),
            ("⑤ 추측·확신·참조", f"추측 {a.counts['hedge']} · 확신 {a.counts['confident']} · 참조 {a.counts['reference']}"),
            ("⑥ 번역(선택)", "버튼을 눌렀을 때만 번역 API 호출(Claude Haiku 또는 DeepL)"),
        ]
        st.markdown('<div class="pp-steps">' + "".join(f'<div class="pp-step"><b>{esc(t)}</b><span>{esc(d)}</span></div>'
                                                        for t, d in steps) + "</div>", unsafe_allow_html=True)
        if a.removed_lines:
            st.caption("제거된 줄: " + " / ".join(a.removed_lines[:8]))
        st.markdown("**준비된 규칙 켜고 끄기** · 끄면 위 원문 표시가 바로 바뀌어요(자유 정규식 입력은 없음)")
        rule_toggles()


def secret(name: str) -> str | None:
    """배포 사이트는 Streamlit 비밀값(st.secrets), 로컬은 .env에서 키를 읽는다. 키 값은 화면·로그에 내보내지 않는다."""
    try:
        key = st.secrets.get(name)
    except Exception:          # 로컬에 secrets.toml이 없으면 예외가 난다
        key = None
    return key or T.load_api_key(name)


# ================================================================ 로그인(선택)·내 계정 저장(C)

@st.cache_resource
def get_store():
    """내 계정 저장소. Supabase 주소·키가 없으면 None(로그인 기능을 숨긴다)."""
    if os.environ.get("PAPERPATH_TEST_STORE"):      # 테스트 전용: 서버 메모리 저장소
        return S.MemoryStore()
    url, key = secret("SUPABASE_URL"), secret("SUPABASE_SERVICE_KEY")
    return S.SupabaseStore(url, key) if url and key else None


def auth_ready() -> bool:
    """Google 로그인 설정([auth])·저장소 키·사용자 해시 소금이 모두 있을 때만 로그인 버튼을 보여 준다."""
    if os.environ.get("PAPERPATH_TEST_STORE"):
        return True
    try:
        ok = "auth" in st.secrets and bool(st.secrets["auth"].get("client_id"))
    except Exception:
        ok = False
    return ok and get_store() is not None and bool(secret("USER_ID_SALT"))


def current_user() -> str | None:
    """로그인한 사용자의 저장소 키(로그인 ID의 해시). 로그인하지 않았거나 설정이 없으면 None."""
    if not auth_ready():
        return None
    test = os.environ.get("PAPERPATH_TEST_USER")    # 테스트 전용: 로그인한 것처럼
    if test:
        return S.user_key(test, "test")
    try:
        sub = st.user.get("sub") if st.user.is_logged_in else None
    except Exception:
        return None
    return S.user_key(sub, secret("USER_ID_SALT")) if sub else None


PREFS_DAY = "prefs"                   # 내 계정 설정은 사용 기록 표(paperpath_usage)의 이 'day' 줄에 둔다(표를 새로 만들지 않음)


def doc_mark(d: dict) -> tuple:
    """내 계정에 다시 올릴지 볼 표시: 저장 시각과 지난 위치(이어 보기 위치가 바뀌어도 올린다)."""
    return (d.get("ts", 0), repr(d.get("last")))


def set_pref(name: str, value) -> None:
    """설정 하나를 이 브라우저와(로그인했으면) 내 계정에 저장한다."""
    ss = st.session_state
    ss.prefs[name] = value
    ss.prefs_pending[name] = value
    ss.prefs_account = True


def account_sync() -> None:
    """C: 로그인했으면 내 계정 저장소와 번역·뜻 풀이 결과·다시 열 문서를 맞춘다.
    처음 한 번 계정의 기록을 불러오고(다른 PC에서 한 번역도 API 없이 보임), 그 뒤엔 새로 생긴 것만 올린다."""
    ss = st.session_state
    user = current_user()
    if not user:
        return
    store = get_store()
    try:
        if not ss.account_loaded:
            items, docs = store.load_all(user)
            ss.account_loaded = True               # 불러오기에 성공했을 때만(실패하면 다음 실행에서 다시 시도)
            tr, gl = SV.unpack_items(items)
            for k, r in tr.items():
                ss.translations.setdefault(k, r)
            for k, g in gl.items():
                ss.ai_gloss.setdefault(k, g)
            for k, item in items.items():
                ss.account_sig[k] = SV.signature(item)
            for k, v in (store.get_usage(user, PREFS_DAY) or {}).items():
                ss.prefs.setdefault(k, v)
            for k, rec in docs.items():
                if SV.valid_doc(rec):
                    ss.account_docs[k] = doc_mark(rec)
                    if k not in ss.browser_docs:
                        remember_doc(rec)              # 이 브라우저의 '저장된 문서'에도 넣는다
        items = SV.pack_items(ss.translations, ss.ai_gloss)
        pending = {k: v for k, v in items.items() if ss.account_sig.get(k) != SV.signature(v)}
        docs = {k: d for k, d in ss.browser_docs.items() if ss.account_docs.get(k) != doc_mark(d)}
        if pending:
            store.put_items(user, pending)
            ss.account_sig.update({k: SV.signature(v) for k, v in pending.items()})
        if docs:
            store.put_docs(user, docs)
            ss.account_docs.update({k: doc_mark(d) for k, d in docs.items()})
        if ss.pop("prefs_account", False):
            store.put_usage(user, PREFS_DAY, ss.prefs)
        ss.account_msg = None
    except S.StoreError as e:
        ss.account_msg = str(e)


def account_delete_all() -> None:
    ss = st.session_state
    user = current_user()
    if not user:
        return
    try:
        get_store().delete_all(user)
    except S.StoreError as e:
        ss.account_msg = str(e)
        return
    # 지금 화면에 남은 결과를 다음 동기화 때 다시 올리지 않도록 '올린 것'으로 표시해 둔다
    ss.account_sig = {k: SV.signature(v) for k, v in SV.pack_items(ss.translations, ss.ai_gloss).items()}
    ss.account_docs = {k: doc_mark(d) for k, d in ss.browser_docs.items()}
    ss.saved_msg = "내 계정에 저장된 기록을 모두 지웠어요(이 브라우저에 저장된 것은 그대로예요)."


def engine():
    """번역 엔진: Claude 키가 있으면 Claude Haiku 5.5, 없으면 DeepL."""
    return T.pick_engine(secret("ANTHROPIC_API_KEY"), secret("DEEPL_API_KEY"))


def engine_name() -> str:
    return "Claude(Anthropic) API" if engine()[0] == "claude" else "DeepL API"


class CountingCaller:
    """번역 API 호출을 세는 감싸개. 긴 글 묶음은 여러 스레드에서 동시에 부르므로 화면 상태(session_state)를
    건드리지 않고 잠금 아래에서 글자 수·호출 수만 센다(캐시에서 답하면 부르지 않으니 세지 않는다)."""

    def __init__(self, fn):
        import threading
        self.fn, self.chars, self.calls, self.lock = fn, 0, 0, threading.Lock()

    def __call__(self, texts: list[str], key: str, split: str) -> list[str]:
        out = self.fn(texts, key, split)
        with self.lock:
            self.chars += sum(T.plain_len(t) for t in texts)     # 태그(<s>·<m>·<u>)를 뺀 글자 수로 센다
            self.calls += 1
        return out


def frequent_term_items(a, skip: set[str]) -> list[tuple[str, str, str]]:
    """반복 용어(밑줄 친 자주 나오는 단어) 중 단어표에 없는 것도 뜻 풀이 요청에 넣는다."""
    out, seen = [], set(skip)
    for m in a.marks:
        if m.category != "term":
            continue
        term = a.text[m.start:m.end]
        k = G.term_key(term)
        if k in seen:
            continue
        seen.add(k)
        sent = next((s.text for s in a.sentences if s.start <= m.start < s.end), "")
        out.append((term, sent, ""))
    return out


def fetch_meanings(a, rows) -> None:
    """AI 풀이: 단어표의 단어 + 반복 용어의 뜻을 Claude에 한 번 보낸다(결과는 이 화면에만 저장).
    단어표 칸과 원문 반복 용어 밑줄의 툴팁에 함께 쓴다."""
    ss = st.session_state
    ss.ai_gloss_error = None
    # 원문에 밑줄로 보이는 반복 용어를 먼저 넣고(40개 상한에 밀리지 않게), 단어표 단어를 이어 붙인다
    freq = frequent_term_items(a, set())
    seen = {G.term_key(t) for t, _, _ in freq}
    items = (freq + [it for it in G.ai_items(a, rows) if G.term_key(it[0]) not in seen])[:MAX_TERMS]
    if not items:
        return
    chars = sum(len(t) + len(s) + len(k) for t, s, k in items)
    # 단어 40개 × 문장 300자까지라 번역의 '한 번 4,000자' 한도와 성격이 달라 화면·하루 한도만 본다
    reason = usage.check(chars, ss.deepl_calls, engine="claude", per_request=False)
    if reason:
        ss.ai_gloss_error = reason
        return
    with st.spinner("단어 뜻을 풀이하는 중…"):
        try:
            got = explain_terms(items, secret("ANTHROPIC_API_KEY"))
        except T.TranslationError as e:
            ss.ai_gloss_error = str(e)
            return
    usage.record(chars)
    ss.deepl_calls += 1
    ss.ai_gloss[text_key(a.text)] = got


def term_meanings(a, tr) -> dict[str, str]:
    """반복 용어 툴팁에 쓸 뜻: AI 풀이 → 원문에서 찾은 풀네임·설명 → 번역기의 한국어 대응어 순서로."""
    ai = st.session_state.ai_gloss.get(text_key(a.text), {})
    out = {}
    for e in G.build(a, G.korean_marks(tr)):
        explained = e.explanation if e.explanation and e.explanation != "이 글에서 정의를 찾지 못함" else ""
        mean = ai.get(e.term) or explained or (f"한국어로 '{e.korean}'" if e.korean else "")
        if mean:
            out[G.term_key(e.term)] = mean
    for term, mean in ai.items():
        out.setdefault(G.term_key(term), mean)
    return out


def run_translate(sents, a, masked: bool, file_cache: bool, **layers):
    """긴 절은 엔진의 한 번 한도 이하 묶음으로 나눠 동시에 번역한다. 사용 제한은 보내기 전에 묶음 전체로 확인한다."""
    ss = st.session_state
    name, key, fn = engine()
    lim = usage.LIMITS[name]
    sizes = [sum(len(s) + 1 for s in sents[x:y]) for x, y in T.chunk_ranges(sents, lim.per_request)]
    reason = usage.check_batch(sizes, ss.deepl_calls, engine=name) if key else None
    gate = quota_gate(a, sum(sizes)) if key and not reason else None
    if reason or (gate and gate[0]):
        return T.TranslationResult(ko=None, mode="none", masked=masked, error=reason or gate[0])
    caller = CountingCaller(fn)
    res = T.translate_chunked(sents, lim.per_request, acronyms=a.acronyms, masked=masked,
                              use_file_cache=file_cache, api_key=key, caller=caller, engine=name, **layers)
    if caller.calls:
        usage.record(caller.chars)
        ss.deepl_calls += caller.calls             # 엔진과 상관없이 이 화면의 번역 호출 수
        quota_record(gate, caller.chars)
    return res


# ---------------------------------------------------------------- 사용자별 번역 한도(src/quota.py)

def quota_doc(a) -> tuple[str, bool]:
    """번역 한도를 셀 문서 id와, 오늘 전에 이미 번역을 시작한 문서인지(저장된 번역이 있는 글이 있으면)."""
    ss = st.session_state
    rec = ss.browser_docs.get(ss.current_doc) if ss.current_doc else None
    if rec is None:
        return text_key(a.text), False
    return rec["id"], doc_progress(rec)[0] > 0


def quota_gate(a, chars: int):
    """보내기 전 확인. 로그인 설정이 없으면(로컬 개발) 사용자별 한도를 두지 않는다.
    돌려주는 값: (막는 이유 또는 None, 기록에 쓸 정보)."""
    if not auth_ready():
        return None
    ss = st.session_state
    user = current_user()
    if not user:
        return Q.check_guest(ss.guest_chars, chars), ("guest",)
    doc_id, started = quota_doc(a)
    try:
        day_usage = get_store().get_usage(user, usage._today())
    except S.StoreError:
        return None, None                  # 저장소가 잠깐 안 되면 사이트 전체 한도만 적용한다
    started = started and doc_id not in (day_usage.get("docs") or {})
    return Q.check_user(day_usage, doc_id, started, chars), ("user", user, doc_id, started)


def quota_record(gate, chars: int) -> None:
    """번역 API를 실제로 부른 글자 수만 기록한다(저장된 번역·캐시는 세지 않음)."""
    if not gate or not gate[1]:
        return
    ss = st.session_state
    info = gate[1]
    if info[0] == "guest":
        ss.guest_chars += chars
        return
    _, user, doc_id, started = info
    try:
        day = usage._today()
        get_store().put_usage(user, day, Q.add_user(get_store().get_usage(user, day), doc_id, started, chars))
    except S.StoreError as e:
        ss.account_msg = str(e)


def quota_line() -> str | None:
    """번역 칸에 보여 줄 오늘 남은 번역. 로그인 설정이 없으면 None."""
    if not auth_ready():
        return None
    ss = st.session_state
    user = current_user()
    if not user:
        return (f"로그인 없이 맛보기 번역 {ss.guest_chars:,}/{Q.GUEST_CHARS:,}자 · 로그인하면 하루에 논문 "
                f"{Q.PAPERS_PER_DAY}편을 끝까지 번역할 수 있어요")
    try:
        day_usage = get_store().get_usage(user, usage._today())
    except S.StoreError:
        return None
    docs = day_usage.get("docs") or {}
    cur = docs.get(ss.current_doc or "", {})
    return (f"오늘 새 논문 {Q.new_papers(day_usage)}/{Q.PAPERS_PER_DAY}편 번역 · 이 논문 오늘 {cur.get('chars', 0):,}/"
            f"{Q.MAX_CHARS_PER_PAPER:,}자 · 저장된 번역을 다시 여는 것은 세지 않아요")


def ko_html(res, i: int, a, off=None) -> str:
    """i번째 문장의 한국어 번역(형광펜·밑줄 포함)과 마스킹 끔 비교 줄."""
    marks_ko, uls_ko = res.ko_marks or [], res.ko_underlines or []
    extra = ""
    if off and off.ko is not None and i < len(off.ko) and off.ko[i] != res.ko[i]:
        extra = f'<div class="pp-off">마스킹 끔: {esc(off.ko[i])}</div>'
    body = render_ko(res.ko[i], marks_ko[i] if i < len(marks_ko) else [], a.active_rules,
                     as_wrappers(uls_ko[i]) if i < len(uls_ko) else None,
                     en_tips(a, i) if i < len(a.sentences) else None,
                     (cur_float_ctx(a)[:2], i))
    return f'<div class="pp-text">{body}</div>{extra}'


def hard_marks(a) -> list:
    """단어표의 어려운 단어('rare': 일상 영어에서 아주 드문 말)를 본문 위치마다 점선 표시로. 설명창에는 뜻(AI 풀이·원문 설명·
    번역 대응어 중 있는 것)·난이도·이 글에 나온 횟수. 정규식 표시(약어·반복 용어 등)와 겹치는 곳은 그쪽을 남긴다."""
    from src.extract import Mark

    ss = st.session_state
    key = text_key(a.text)
    tips = ss.get("term_tips", {}).get(key, {})
    cache = ss.setdefault("hard_marks_cache", {})
    sig = (key, len(tips), tuple(sorted(tips.items()))[:3])
    if cache.get("sig") == sig:
        return cache["marks"]
    rows = {G.term_key(e.term): e for e in G.build(a) if "rare" in e.kinds}
    taken = [(m.start, m.end) for m in a.marks]
    out = []
    for x, y, low in G.rare_word_spans(a.text, frozenset(t.start for t in a.sentences)):
        e = rows.get(G.term_key(low))
        if e is None or any(x < b and y > s0 for s0, b in taken):
            continue
        mean = tips.get(G.term_key(e.term)) or "뜻은 '번역하기'나 단어표의 'AI로 뜻 풀이 받기'를 누르면 여기에 떠요"
        level = f" · 난이도 {e.level}" if e.level else ""
        out.append(Mark(x, y, "jargon", JARGON_RULE, f"{e.term}: {mean}{level} · 이 글에 {e.count}번"))   # 앞에 '어려운 단어 · '는 mark_tip이 붙인다
    cache.clear()
    cache.update(sig=sig, marks=out)
    return out


def all_marks(a) -> list:
    """화면에 그릴 표시: 정규식 표시 + (켜져 있으면) 어려운 단어 점선."""
    if JARGON_RULE not in a.active_rules:
        return a.marks
    return sorted([*a.marks, *hard_marks(a)], key=lambda m: m.start)


def en_html(a, s, uls) -> str:
    html_ = render_marked(a.text, s.start, s.end, all_marks(a), uls.get(s.idx))
    # 시약 품번 괄호 '(Sigma-Aldrich, cat. no. B3883)'는 작고 흐리게: 지우지 않고 시약 이름이 눈에 띄게만 한다
    html_ = CATALOG_HTML.sub(dim_catalog, html_)
    return f'<div class="pp-text">{html_}</div>'


def current_translation(a):
    """지금 글의 번역 결과(문장별로 짝이 맞은 것만). 번역을 껐거나 아직 없으면 None."""
    ss = st.session_state
    res = ss.translations.get((text_key(a.text), True))
    if not ss.show_translation or res is None or res.ko is None:
        return None
    return res


def table_card(t, prefix: str) -> None:
    """표 하나: 번호·쪽·캡션, 잘라 낸 이미지, 칸이 깔끔하면 '표로 보기'(CSV)."""
    where = "쪽 전체(영역을 찾지 못함)" if t.whole_page else ("표 영역" if t.kind == "table" else "그림 영역")
    st.markdown(f'<div class="pp-table-cap"><b>{esc(t.label)}</b> <span class="pp-meta">· {t.page}쪽 · {where}</span><br>'
                f'{esc(t.caption)}</div>', unsafe_allow_html=True)
    st.image(t.png, width=min(t.width or 900, 900))    # 좁은 표를 억지로 늘리지 않는다
    if t.grid:
        with st.expander(f"Table {t.num} 표로 보기(자동 인식, 원본 이미지와 비교해 주세요)"):
            import pandas as pd
            width = max(len(r) for r in t.grid)
            rows = [r + [""] * (width - len(r)) for r in t.grid]
            df = pd.DataFrame(rows[1:], columns=[c or f"열{i + 1}" for i, c in enumerate(rows[0])]) \
                if len(set(rows[0])) == width else pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.download_button("CSV로 내려받기", df.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"table_{t.num}.csv", mime="text/csv", key=f"tcsv_{prefix}_{t.num}")


def float_context(a) -> tuple[dict, dict]:
    """지금 글이 언급하는 표·그림과 처음 언급한 문장(정규식 TABLE_MENTION·FIGURE_MENTION).
    ({(table|figure, 번호): TableInfo}, {(table|figure, 번호): 문장 번호}). PDF가 아니면 비어 있다."""
    ss = st.session_state
    if ss.source["kind"] != "pdf":
        return {}, {}
    pool = {("table", t.num): t for t in ss.get("paper_tables") or []}
    pool |= {("figure", f.num): f for f in ss.get("paper_figures") or []}
    first = {k: i for k, i in first_mentions(a.text, a.sentences).items() if k in pool}
    return {k: pool[k] for k in first}, first


def cur_float_ctx(a) -> tuple:
    """reading_page가 저장해 둔 지금 글의 (표·그림, 처음 언급 문장, 문장 시작 위치 → 번호)."""
    return st.session_state.get("float_ctx", {}).get(text_key(a.text)) or ({}, {}, {})


def float_style(floats: dict) -> str:
    """표·그림 이미지를 CSS 클래스 하나에 한 번만 싣는다(본문 카드·마우스 미리보기가 같은 이미지를 나눠 씀)."""
    import base64
    rules = "".join(f".pp-flt-{k}-{n}{{background-image:url(data:{t.mime};base64,{base64.b64encode(t.png).decode()})}}"
                    for (k, n), t in floats.items())
    return f"<style>{rules}</style>" if rules else ""


def float_box(key, t, cls: str, max_w: int, max_h: int | None = None, fixed: bool = False) -> str:
    """이미지 자리(배경 이미지 + 원래 가로세로 비율). max_h를 주면 그 높이에 맞춰 줄인 미리보기.
    fixed면 너비를 픽셀로 고정한다(마우스 미리보기 상자는 내용 너비를 따르므로 %를 쓰면 0이 된다)."""
    w = t.width or 600
    if max_h and t.height:
        w = min(w, int(max_h * t.width / t.height))
    ratio = f"{t.width or 4} / {t.height or 3}"
    return (f'<span class="pp-flt pp-flt-{key[0]}-{key[1]} {cls}" role="img" aria-label="{esc(t.label)}" '
            f'style="aspect-ratio:{ratio}; width:{f"{min(w, max_w)}px" if fixed else f"min(100%, {min(w, max_w)}px)"}"></span>')


def float_cards(i: int, ctx, ko: bool = False) -> str:
    """i번째 문장이 처음 언급한 표·그림 카드: 작은 미리보기, 누르면 원래 크기.
    ko면 한국어 번역 쪽 카드(이름을 '표 2'·'그림 1'로, 캡션은 원문 그대로)."""
    floats, first = ctx[0], ctx[1]
    out = []
    for key, idx in first.items():
        if idx != i:
            continue
        t = floats[key]
        icon = "📊" if t.kind == "table" else "🖼️"
        label = f"{'표' if t.kind == 'table' else '그림'} {t.num}" if ko else t.label
        where = " · 쪽 전체(영역을 찾지 못함)" if t.whole_page else ""
        cap = t.caption if len(t.caption) <= 160 else t.caption[:159] + "…"
        # 기본은 한 줄(이름·쪽·캡션 앞부분)로 접어 두어 문장 흐름을 끊지 않는다. 누르면 원래 크기로 펼친다
        short = cap if len(cap) <= 70 else cap[:69] + "…"
        out.append(f'<div class="pp-float"><details><summary><div class="pp-float-head">{icon} <b>{esc(label)}</b>'
                   f' <span class="pp-meta">· {t.page}쪽{where} · 펼치기</span>'
                   f' <span class="pp-float-short">{esc(short)}</span></div></summary>'
                   f'<div class="pp-float-cap">{esc(cap)}</div>{float_box(key, t, "pp-full", 900)}</details></div>')
    return "".join(out)


def float_pop(phrase: str, sent_idx: int | None, ctx) -> tuple[str, str]:
    """'Table 2'·'Fig. 3' 표시에 붙일 (툴팁 덧말, 마우스를 올리면 뜨는 미리보기 HTML)."""
    floats, first = ctx[0], ctx[1]
    for kind, pat in (("table", P.TABLE_MENTION), ("figure", P.FIGURE_MENTION)):
        m = pat.search(phrase)
        if not m:
            continue
        n = int(re.findall(r"\d+", m.group(0))[0])
        key = (kind, n)
        if key not in floats:
            return "", ""
        idx = first[key]
        where = "바로 아래에 있어요" if idx == sent_idx else f"문장 {idx + 1} 아래에 있어요"
        return (f" · {floats[key].label}: {where}(마우스를 올리면 미리보기)",
                f'<span class="pp-pop">{float_box(key, floats[key], "", 520, fixed=True)}</span>')
    return "", ""


LONG_SENTS = 40                       # 문장이 이보다 많은 글은 나눠 읽는다
PART_SENTS = 30                       # 소제목 위치를 모를 때 한 부분의 문장 수
CATALOG_HTML = re.compile(r"\((?:[^()]*?,\s*)?cat\.\s*no\.[^()]*\)")   # 렌더된 HTML 안 품번 괄호


def dim_catalog(m) -> str:
    """품번 괄호를 흐린 글씨로 감싼다. 안에 형광펜 태그('Sigma-Aldrich' 반복 용어)가 있어도 짝이 맞을 때만 감싼다."""
    inner = m.group()
    if inner.count("<span") != inner.count("</span>"):
        return inner
    return f'<span class="pp-cat">{inner}</span>'
PART_PILLS_MAX = 6                    # 부분이 이보다 많으면 버튼 대신 드롭다운


def section_now():
    """지금 읽는 PDF 절(본문 쪽에서 절 하나를 고른 경우). 아니면 None."""
    ss = st.session_state
    ids = ss.source.get("section_ids") or []
    if ss.get("paper") is None or ss.source.get("kind") != "pdf" or len(ids) != 1:
        return None
    return ss.paper.sections[ids[0]]


def fallback_ok() -> bool:
    """위치로 대신 고르는 뼈대 카드(첫 문장=분야 배경 등)를 써도 되는 글: 초록·서론·결론 쪽, PDF가 아닌 글."""
    ss = st.session_state
    if ss.source.get("kind") != "pdf" or ss.source.get("page") != "본문":
        return True
    sec = section_now()
    return sec is None or bool(P.FALLBACK_TITLE.match(sec.title))


def protocol_section(a) -> bool:
    """실험 절차 절: 제목이 Procedure·Materials·Timing 등이거나, 원문에 주의·멈춤 지점 표시가 3개 이상."""
    sec = section_now()
    if sec is not None and P.PROTO_SECTION.match(sec.title):
        return True
    return sum(bool(P.PROTO_CAUTION.search(s.text) or P.PROTO_PAUSE.search(s.text)) for s in a.sentences) >= 3


def reagent_name(before: str) -> str:
    """품번 괄호 바로 앞 글자에서 시약 이름: 마지막 문장·목록 경계 뒤, 대문자(또는 '3,5-Di…'처럼 숫자+글자)로
    시작하는 마지막 낱말부터. 대문자가 없으면 끝 세 낱말('porcine bile'). 글자가 없으면 ''."""
    tail = re.split(r"[;.)\n]\s*|,\s+", before)[-1].split()
    starts = [i for i, w in enumerate(tail) if w[:1].isupper() and w != "CAUTION" or re.match(r"\d[\d,]*-[A-Za-z]", w)]
    if starts:
        i = starts[-1]
        while i - 1 in starts:             # 'Bile Acid Assay Kit'처럼 대문자 낱말이 이어지면 처음부터
            i -= 1
        words = tail[i:]
    else:
        words = tail[-3:]
    name = " ".join(words).strip(" ,")
    return name if re.search(r"[A-Za-z]{3}", name) else ""


def protocol_cards(a) -> None:
    """절차 요점 카드 3장: 주의(CRITICAL·CAUTION) · 멈춤 지점(PAUSE POINT) · 단계와 소요 시간 · 시약·제품(품번) 중
    원문에서 찾은 것이 있는 카드부터. 모두 원문 글자 그대로."""
    def items(pat, limit=3):
        hits = [s for s in a.sentences if pat.search(s.text)]
        lis = []
        for s in hits[:limit]:
            m = pat.search(s.text)
            body = (esc(s.text[:m.start()]) + f'<span class="pp-proto-tag">{esc(m.group())}</span>'
                    + esc(s.text[m.end():][:220]) + ("…" if len(s.text) - m.end() > 220 else ""))
            lis.append(f'<li><span class="pp-sk-no">문장 {s.idx + 1}</span>{body}</li>')
        return hits, "".join(lis)

    sec = section_now()
    steps = [int(m.group("n")) for m in P.PROTO_STEP.finditer(a.text)]
    timed = [h for h in (sec.subheads if sec else []) if "Timing" in h]
    cards = []                             # (찾았는지, 제목, 색, 개수, 본문)
    for title, color, pat, none_msg in (
            ("주의 · CRITICAL·CAUTION", "blue", P.PROTO_CAUTION, "이 절에는 주의(CRITICAL·CAUTION) 표시가 없어요."),
            ("멈춤 지점 · PAUSE POINT", "teal", P.PROTO_PAUSE, "이 절에는 멈춰도 되는 지점(PAUSE POINT) 표시가 없어요.")):
        hits, lis = items(pat)
        body = (f'<ul class="pp-proto-list">{lis}</ul>' if hits else f'<div class="pp-sk-missing">{none_msg}</div>')
        more = " · 처음 3개만" if len(hits) > 3 else ""
        cards.append((bool(hits), title, color, f"{len(hits)}개{more}", body))
    hits, lis = items(P.PROTO_TIMING)
    head = []
    if steps:
        head.append(f"<li>번호 단계 <b>{min(steps)}–{max(steps)}</b> · {len(set(steps))}개</li>")
    head += [f"<li>{esc(h)}</li>" for h in timed[:3]]
    body = (f'<ul class="pp-proto-list">{"".join(head)}{lis}</ul>' if head or hits else
            '<div class="pp-sk-missing">이 절에는 번호 단계나 소요 시간 표시가 없어요.</div>')
    cards.append((bool(head or hits), "단계 · 소요 시간", "violet", f"단계 {len(set(steps))}개", body))
    # 시약·제품: 'Bovine bile (Sigma-Aldrich, cat. no. B3883)'처럼 품번이 붙은 항목. 이름은 괄호 바로 앞 낱말들
    names = []
    for m in P.CATALOG_PAREN.finditer(a.text):
        name = reagent_name(a.text[max(0, m.start() - 80):m.start()])
        if name and name not in names:
            names.append(name)
    lis = "".join(f"<li><b>{esc(n)}</b></li>" for n in names[:5])
    body = (f'<ul class="pp-proto-list">{lis}</ul>' if names else
            '<div class="pp-sk-missing">이 절에는 품번(cat. no.)이 붙은 시약·제품이 없어요.</div>')
    more = " · 처음 5개만" if len(names) > 5 else ""
    cards.append((bool(names), "시약·제품 · cat. no.", "blue", f"{len(names)}개{more}", body))
    # 찾은 것이 있는 카드부터 3장(빈 카드만 나란히 서지 않게). 순서는 위에 적은 차례를 지킨다
    shown_cards = ([c for c in cards if c[0]] + [c for c in cards if not c[0]])[:3]
    for col, (_, title, color, count, body) in zip(st.columns(3), shown_cards):
        col.markdown(f'<div class="pp-panel pp-card {color}"><div class="pp-card-head"><div class="pp-kind">'
                     f'<span class="pp-kdot"></span>{title}</div><div class="pp-basis signal">{esc(count)}</div></div>'
                     f'{body}<div class="pp-reason">{ICON["info"]}<span>실험 절차 절이라 문제·방법·결과 대신 원문에 '
                     f'표시된 요점을 보여 줘요.</span></div></div>', unsafe_allow_html=True)


def reading_parts(a) -> list[tuple[str, int, int]]:
    """긴 글을 나눠 읽을 부분 [(이름, 시작 문장, 끝 문장(제외))]. 소제목 위치를 알면 소제목마다, 모르면 30문장씩.
    짧은 글이나 나눌 곳이 없으면 []."""
    n = len(a.sentences)
    if n <= LONG_SENTS:
        return []
    starts: dict[int, str] = {}
    sec = section_now()
    if sec is not None and sec.text == a.text:
        for name, at in zip(sec.subheads, sec.sub_at):
            i = next((s.idx for s in a.sentences if s.end > at), None) if at >= 0 else None
            if i is not None:
                starts.setdefault(i, name)
    if len(starts) >= 2 or (starts and 0 not in starts):
        starts.setdefault(0, "처음")
        idx = sorted(starts)
        parts = [(starts[i], i, idx[k + 1] if k + 1 < len(idx) else n) for k, i in enumerate(idx)]
    else:
        parts = [(f"문장 {i + 1}–{min(i + PART_SENTS, n)}", i, min(i + PART_SENTS, n)) for i in range(0, n, PART_SENTS)]
    return parts if len(parts) > 1 else []


def part_picker(a) -> list[tuple[str, int, int]]:
    """긴 글이면 '읽을 부분' 버튼을 보이고 고른 범위를 기억한다. 번역·형광펜·뼈대는 글 전체 그대로."""
    ss = st.session_state
    key = text_key(a.text)
    parts = reading_parts(a)
    if not parts:
        ss.pop("part_resume", None)
        ss.parts_n = 0
        ss.part_range = {key: (0, len(a.sentences))}
        return []
    if "part_resume" in ss:                 # '이어서 보기'로 연 부분
        ss.part_of = key
        ss.part_pick = min(ss.pop("part_resume"), len(parts) - 1)
    elif ss.get("part_of") != key or ss.get("part_pick") is None or ss.part_pick >= len(parts):
        ss.part_of = key
        ss.part_pick = ss.get("part_last", 0) if ss.get("part_of_last") == key else 0
    ss.parts_n = len(parts)
    by_sub = not parts[0][0].startswith("문장 ")
    labels = {k: f"{name} · {hi - lo}문장" for k, (name, lo, hi) in enumerate(parts)}
    with st.container(key="part_bar"):
        what = "이 절 안의 소제목" if by_sub else "이 글 안의 부분(30문장씩)"
        st.markdown(f'<div class="pp-nav-label">{what} · {len(a.sentences)}문장을 나눠 보여 줘요</div>',
                    unsafe_allow_html=True)
        st.caption("번역·형광펜·뼈대는 글 전체 기준이에요. 아래 ◀ ▶ 버튼으로도 넘길 수 있어요.")
        if len(parts) > PART_PILLS_MAX:    # 부분이 많으면 버튼이 여러 줄로 쌓이므로 드롭다운 하나로
            st.selectbox("읽을 부분", list(range(len(parts))), format_func=labels.get, key="part_pick",
                         label_visibility="collapsed", on_change=track_position)
        else:
            st.pills("읽을 부분", list(range(len(parts))), format_func=labels.get, key="part_pick",
                     label_visibility="collapsed", on_change=track_position)
    k = ss.part_pick if ss.part_pick is not None else 0
    ss.part_last, ss.part_of_last = k, key
    ss.part_range = {key: parts[k][1:]}
    if ss.pop("part_scroll", False):
        ss.part_scroll_n = ss.get("part_scroll_n", 0) + 1   # 내용이 같으면 Streamlit이 다시 실행하지 않아 번호를 바꿔 넣는다
        components.html(f"<script>/* {ss.part_scroll_n} */ const b = window.parent.document.querySelector('.st-key-part_bar');"
                        "if (b) b.scrollIntoView({block: 'start'});</script>", height=0)
    return parts


def go_part(k: int) -> None:
    st.session_state.part_pick = k
    st.session_state.part_scroll = True      # 글 아래 버튼으로 넘기면 새 부분의 처음으로 올라간다
    track_position()


def reading_stops() -> list[tuple[str, int, str]]:
    """PDF 논문을 처음부터 읽는 순서 [(쪽, 본문 절 번호, 이름)]: 초록 → 본문 절 하나씩 → 결론. PDF가 아니면 []."""
    ss = st.session_state
    if ss.get("paper") is None or ss.source.get("kind") != "pdf":
        return []
    stops = []
    for page, ids in paper_pages(ss.paper).items():
        if page == "본문":
            stops += [(page, j, ss.paper.sections[i].title) for j, i in enumerate(ids)]
        else:
            stops.append((page, 0, page))
    return stops


def stop_now(stops) -> int | None:
    """지금 읽는 곳이 reading_stops의 몇 번째인지."""
    ss = st.session_state
    page = ss.source.get("page")
    body = ss.get("body_cur", 0) if page == "본문" else 0
    return next((k for k, s in enumerate(stops) if s[:2] == (page, body)), None)


def go_stop(page: str, body: int) -> None:
    """글 아래 '다음 절 ▶ / ◀ 이전 절': 그 쪽·절의 첫 부분으로 옮기고, 화면은 절 고르기 막대로 올린다."""
    ss = st.session_state
    ss.paper_cur, ss.body_cur = page, body
    for k in ("paper_page", "section_pick"):   # 위 쪽·절 고르기 버튼도 새 위치로 다시 채운다
        ss.pop(k, None)
    set_view()
    track_position(new_section=True)
    ss.stop_scroll = True


def stop_label(stop) -> str:
    return f"절: {stop[2]}" if stop[0] == "본문" else f"쪽: {stop[2]}"


def part_nav_buttons(parts, narrow: bool = False) -> None:
    """글 바로 아래 막대: '◀ 이전 · 지금 위치 · 다음 ▶'. 긴 절 안에서는 부분끼리 넘기고, 절의 마지막 부분(나누지 않은
    절은 그 끝)에서는 '다음 절 ▶', 첫 부분에서는 '◀ 이전 절'로 논문 순서(초록 → 본문 절 → 결론)대로 넘어간다.
    본문 칸과 같은 폭(영어만·한국어만이면 가운데 960px)으로 붙여, 버튼이 본문 밖에 떠 보이지 않게 한다."""
    ss = st.session_state
    stops = reading_stops()
    at = stop_now(stops)
    if not parts and at is None:
        return
    k = (ss.get("part_pick") or 0) if parts else 0
    box = st.container(key="part_nav_narrow" if narrow else "part_nav")
    prev_col, mid_col, next_col = box.columns([1, 0.8, 1], vertical_alignment="center")
    if parts and k > 0:
        prev_col.button(f"◀ 이전 부분: {parts[k - 1][0]}", key="part_prev", on_click=go_part, args=(k - 1,))
    elif at is not None and at > 0:
        prev = stops[at - 1]
        prev_col.button(f"◀ 이전 {stop_label(prev)}", key="stop_prev", on_click=go_stop, args=prev[:2])
    sec = section_now()
    if parts:
        where = f"{sec.title} 안 " if sec is not None else ""
        mid = f"{where}{k + 1} / {len(parts)} 부분"
    else:
        mid = f"논문 순서 {at + 1} / {len(stops)}"   # 절 이름은 위 막대·버튼에 있어 짧게(두 줄로 꺾이지 않게)
    mid_col.markdown(f'<div class="pp-meta" style="text-align:center">{esc(mid)}</div>', unsafe_allow_html=True)
    with next_col.container(key="part_next_box"):
        if parts and k + 1 < len(parts):
            st.button(f"다음 부분: {parts[k + 1][0]} ▶", key="part_next", on_click=go_part, args=(k + 1,))
        elif at is not None and at + 1 < len(stops):
            nxt = stops[at + 1]
            st.button(f"다음 {stop_label(nxt)} ▶", key="stop_next", type="primary", on_click=go_stop, args=nxt[:2])
    if ss.pop("stop_scroll", False):      # 새 절로 넘어가면 절 고르기 막대(어느 절인지 보이게)부터
        ss.stop_scroll_n = ss.get("stop_scroll_n", 0) + 1
        components.html(f"<script>/* {ss.stop_scroll_n} */ const b = window.parent.document.querySelector('.st-key-paper_nav');"
                        "if (b) b.scrollIntoView({block: 'start'});</script>", height=0)


def range_note(a) -> str:
    """원문 헤더에 붙일 '지금 읽는 구간': 절 이름 · 문장 범위 · (논문이면) 논문 전체 문장 수."""
    ss = st.session_state
    n = len(a.sentences)
    lo, hi = ss.get("part_range", {}).get(text_key(a.text), (0, n))
    where = ss.source.get("section", "") if ss.source.get("kind") == "pdf" else ""
    span = f"문장 {lo + 1}–{hi}" + (f" / 이 글 {n}문장" if (lo, hi) != (0, n) else "")
    total = paper_sentences(ss.paper) if ss.paper is not None and ss.source.get("kind") == "pdf" else 0
    parts = [where, span] + ([f"논문 전체 {total}문장"] if total else [])
    return " · ".join(esc(x) for x in parts if x)


def paper_sentences(paper) -> int:
    """논문 전체 문장 수(초록·본문·결론, 감사의 글 제외). 논문마다 한 번만 센다."""
    ss = st.session_state
    key = text_key("\n".join(s.text for s in paper.sections))
    cache = ss.setdefault("paper_sent_total", {})
    if key not in cache:
        cache[key] = sum(len(analyze(paper.sections[i].text).sentences)
                         for ids in paper_pages(paper).values() for i in ids)
    return cache[key]


def sub_starts(a) -> dict[int, list[str]]:
    """{문장 번호: 그 문장에서 시작하는 소제목들}. 지금 읽는 PDF 절의 소제목(번호 소제목·문단 첫머리 굵은 제목) 위치로 정한다."""
    sec = section_now()
    if sec is None or sec.text != a.text:
        return {}
    out: dict[int, list[str]] = {}
    for name, at in zip(sec.subheads, sec.sub_at):
        i = next((s.idx for s in a.sentences if s.end > at), None) if at >= 0 else None
        if i is not None:
            out.setdefault(i, []).append(name)
    return out


def sub_row(a, i: int) -> str:
    """i번째 문장 바로 위에 넣을 소제목 줄(목차처럼). 소제목이 없으면 빈 글자."""
    ss = st.session_state
    key = text_key(a.text)
    cache = ss.setdefault("sub_starts_cache", {})
    if key not in cache:
        cache.clear()
        cache[key] = sub_starts(a)
    names = cache[key].get(i)
    return "".join(f'<div class="pp-subhead">{esc(n)}</div>' for n in names) if names else ""


def shown(a):
    """지금 보여 줄 문장들(긴 글이면 고른 부분만)."""
    lo, hi = st.session_state.get("part_range", {}).get(text_key(a.text), (0, len(a.sentences)))
    return a.sentences[lo:hi]


def in_part(a, i: int) -> bool:
    lo, hi = st.session_state.get("part_range", {}).get(text_key(a.text), (0, len(a.sentences)))
    return lo <= i < hi


def side_panel(a) -> None:
    """한영 나란히: 문장 짝마다 한 행(왼쪽 영어, 오른쪽 한국어). 두 칸을 따로 그리면 줄 수 차이가 쌓여
    뒤쪽 문장이 어긋나므로 행 단위로 묶는다. 표·그림 카드는 그 문장 짝 아래에 한 번."""
    ss = st.session_state
    res = current_translation(a)
    off = ss.translations.get((text_key(a.text), False))
    uls = card_underlines(a)
    ctx = cur_float_ctx(a)
    ko_head = ("한국어 번역" if res else
               ("번역을 껐어요" if not ss.show_translation else "위의 번역하기를 누르면 여기에 붙어요"))
    rows = [f'{sub_row(a, s.idx)}<div class="pp-side-row"><div class="pp-no">{s.idx + 1}</div>'
            f'<div class="pp-side-en">{en_html(a, s, uls)}</div>'
            f'<div class="pp-side-ko">{ko_html(res, s.idx, a, off) if res and s.idx < len(res.ko) else ""}</div></div>'
            f'{float_cards(s.idx, ctx)}' for s in shown(a)]
    st.markdown(f'<div class="pp-panel"><div class="pp-side-head"><div></div>'
                f'<div class="pp-reader-title"><span class="pp-lang">EN</span>영어 원문 '
                f'<span class="pp-meta">· {range_note(a)}</span></div>'
                f'<div class="pp-reader-title"><span class="pp-lang">KO</span><span class="pp-meta">{esc(ko_head)}</span></div>'
                f'</div><div class="pp-sents">{"".join(rows) or "<div class=pp-meta>문장이 없어요.</div>"}</div></div>',
                unsafe_allow_html=True)


def interleaved_panel(a) -> None:
    """문장마다 한영: 영어 문장 바로 아래에 그 문장의 한국어 번역."""
    ss = st.session_state
    res = current_translation(a)
    off = ss.translations.get((text_key(a.text), False))
    uls = card_underlines(a)
    rows = []
    ctx = cur_float_ctx(a)
    for s in shown(a):
        ko = (f'<div class="pp-pair-ko"><span class="pp-lang">KO</span>{ko_html(res, s.idx, a, off)}</div>'
              if res and s.idx < len(res.ko) else "")
        rows.append(f'{sub_row(a, s.idx)}<div class="pp-sent pp-pair"><div class="pp-no">{s.idx + 1}</div><div>'
                    f'<div class="pp-pair-en">{en_html(a, s, uls)}</div>{ko}</div></div>{float_cards(s.idx, ctx)}')
    note = "" if res else '<span class="pp-meta"> · 위 번역 칸에서 번역하면 문장마다 한국어가 붙어요</span>'
    st.markdown(f'<div class="pp-panel"><div class="pp-reader-head"><div class="pp-reader-title">'
                f'<span class="pp-lang">EN·KO</span>문장마다 한영 <span class="pp-meta">· {range_note(a)}</span>'
                f'{note}</div></div><div class="pp-sents">{"".join(rows)}</div></div>', unsafe_allow_html=True)


def translation_panel(a, rows_on: bool = True, floats_on: bool = False, where: str = "") -> None:
    """한국어 번역 칸. rows_on=False면 번역 문장 없이 버튼·안내·사용량만(문장마다 한영 모드).
    floats_on이면(한국어만 모드) 표·그림을 처음 언급한 문장 아래에 끼운다."""
    ss = st.session_state
    sents = [s.text for s in a.sentences]
    tkey = text_key(a.text)
    file_cache = ss.source["kind"] in ("demo", "arxiv")   # 붙여 넣은 텍스트의 번역은 파일에 남기지 않는다
    with st.container(key="ko_panel"):
        h1, h2 = st.columns([3, 1.3], vertical_alignment="center")
        h1.markdown('<div class="pp-reader-title"><span class="pp-lang">KO</span>한국어 번역 '
                    '<span class="pp-meta">· 문장별 대응</span></div>', unsafe_allow_html=True)
        with h2:
            on = st.toggle("번역 켜짐", key="show_translation")
        if not on:
            st.markdown('<div class="pp-notice">번역을 껐어요. 원문 표시와 3줄 뼈대는 그대로 동작해요.</div>',
                        unsafe_allow_html=True)
            return

        res = ss.translations.get((tkey, True))
        if res is None:
            st.markdown(f'<div class="pp-notice"><b>번역하기</b>를 누르면 위 영어 문장이 <b>{engine_name()}로 전송</b>돼요. '
                        '이 앱은 입력을 저장하지 않지만, 번역 서비스 측의 보관 정책은 그 서비스의 약관을 따라요. '
                        '개인정보는 넣지 마세요.</div>', unsafe_allow_html=True)
            if st.button("번역하기", type="primary", key="translate_btn"):
                with st.spinner("번역하는 중…"):
                    ss.translations[(tkey, True)] = run_translate(
                        sents, a, True, file_cache, **dict(zip(("marks", "underlines"), translation_layers(ss.input_text))))
                # Claude로 번역했으면 같은 김에 단어 뜻도 받아 둔다(반복 용어 툴팁·단어표). 이미 Claude로 보내는 글이라 안내 범위 안
                if engine()[0] == "claude" and tkey not in ss.ai_gloss and ss.translations[(tkey, True)].ko is not None:
                    fetch_meanings(a, G.build(a))
                st.rerun()
            return

        if res.error and res.ko is None and res.paragraph is None:
            st.warning(res.error)
            if st.button("다시 시도", key="retry_btn"):
                ss.translations.pop((tkey, True), None)
                st.rerun()
            return

        detail = {"aligned": "원문 문장과 1:1로 연결했어요(문단을 한 번에 보내고 문장 표시로 짝을 맞춤).",
                  "per_sentence": "원문 문장과 1:1로 연결했어요(문장 짝이 어긋나 문장 번호로 다시 맞춤).",
                  "paragraph": ""}[res.mode]
        short = "AI 번역(Claude) · 참고용" if engine()[0] == "claude" else "DeepL 번역 · 참고용"
        note = "문장 수가 맞지 않아 문단 단위로 보여 줘요 · " if res.mode == "paragraph" else ""
        st.markdown(f'<div class="pp-meta">{note}{short}{" · 저장된 번역" if res.cached else ""} '
                    f'<span class="pp-info" title="{esc(T.ENGINE_LABEL[engine()[0]] + ". " + detail)}">ⓘ</span></div>',
                    unsafe_allow_html=True)
        off = ss.translations.get((tkey, False))
        if res.ko is not None and rows_on:
            ctx = cur_float_ctx(a)
            rows = [f'{sub_row(a, i)}<div class="pp-sent pp-ko"><div class="pp-no">{i + 1:02d}</div><div>{ko_html(res, i, a, off)}</div></div>'
                    + (float_cards(i, ctx, ko=True) if floats_on else "") for i in range(len(res.ko)) if in_part(a, i)]
            st.markdown(f'<div class="pp-sents" style="padding:0">{"".join(rows)}</div>', unsafe_allow_html=True)
        elif res.ko is not None:
            st.markdown(f'<div class="pp-meta">번역 문장은 {where} 보여요.</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="pp-text">{esc(res.paragraph or "")}</div>', unsafe_allow_html=True)

        if off is None:
            if st.button("보호 없이 번역해 비교", key="compare_btn",
                         help="약어·인용·참조를 지키는 보호 표시(마스킹) 없이 한 번 더 번역해 차이를 보여 줘요(번역 API 추가 호출)"):
                with st.spinner("마스킹 없이 번역하는 중…"):
                    ss.translations[(tkey, False)] = run_translate(sents, a, False, file_cache)
                st.rerun()
        elif off.ko is None:
            st.warning(off.error or "비교 번역을 만들지 못했어요.")
        elif res.ko is not None:
            toks = [(t, i) for i, ts in enumerate(res.protected) for t in ts]
            lost_off = [t for t, i in toks if i < len(off.ko) and t not in off.ko[i]]
            diff = sum(1 for x, y in zip(res.ko, off.ko) if x != y)
            st.info(f"보호 토큰 {len(toks)}개 중 마스킹 끔에서 바뀐 것 {len(lost_off)}개"
                    f"{(' (' + ', '.join(lost_off[:5]) + ')') if lost_off else ''} · 번역이 달라진 문장 {diff}개. "
                    "차이가 작을 수 있어요(검증 결과 탭의 30편 실측 참고).")
        lim = usage.LIMITS[engine()[0]]
        line = quota_line()
        if line:
            st.caption(line)
        if ss.deepl_calls >= lim.per_session * 0.8:     # 평소에는 숨기고 한도에 가까울 때만 알린다
            st.caption(f"번역 {ss.deepl_calls}/{lim.per_session}회",
                       help=f"이 화면에서 번역을 {lim.per_session}번까지 쓸 수 있어요. 오늘 사이트 전체 {usage.used_today():,}/"
                            f"{lim.daily:,}자 사용. 긴 글은 {lim.per_request:,}자씩 나눠 동시에 번역해요.")


def hero() -> None:
    """첫 화면 히어로: 왼쪽 소개 문구, 오른쪽은 배경 이미지(static/hero.jpg, 이의상 님이 만든 이미지)."""
    st.markdown('<div class="pp-hero"><div class="pp-hero-text"><div class="pp-eyebrow">ACADEMIC READING WORKSPACE</div>'
                '<h1 class="pp-hero-h1">논문을 읽는 길을<br>더 쉽게 만들어 드려요.</h1>'
                '<div class="pp-hero-desc">영어 논문을 한국어로 이해하고, 핵심 내용을 빠르게 파악하세요.</div>'
                '<div class="pp-hero-badges"><span class="pp-hero-badge">AI-Powered Translation</span>'
                '<span class="pp-hero-badge soft">3줄 뼈대 · 형광펜 · 어려운 단어표</span></div>'
                '</div></div>', unsafe_allow_html=True)


def empty_guide() -> None:
    """자료를 넣기 전 화면: 결과 창들은 숨기고 쓰는 순서만 보여 준다."""
    steps = [("1", "논문 넣기", "PDF를 위 칸에 끌어다 놓거나, arXiv ID·텍스트를 넣어요."),
             ("2", "절 고르기", "PDF는 Abstract·Introduction처럼 절로 나뉘어요. 읽을 절을 눌러요."),
             ("3", "읽는 길 따라가기", "3줄 뼈대 → 형광펜·밑줄 원문 → 번역 → 흐름도·어려운 단어표 순서로 봐요.")]
    cards = "".join(f'<div class="pp-guide-step"><div class="pp-guide-no">{n}</div><div><b>{t}</b>'
                    f'<div class="pp-guide-desc">{d}</div></div></div>' for n, t, d in steps)
    st.markdown(f'<div class="pp-guide">{cards}</div>'
                '<div class="pp-meta" style="margin-top:8px">자료를 넣으면 3줄 뼈대, 원문·번역, 흐름도와 단어표가 이 아래에 나타나요. '
                '먼저 둘러보려면 위의 <b>예시 문단으로 보기</b>를 눌러요.</div>', unsafe_allow_html=True)


def reading_page() -> None:
    ss = st.session_state
    if ss.show_input or not ss.input_text:
        hero()                             # 첫 화면·다시 넣기: 큰 히어로
        input_panel()
        if not ss.input_text:
            empty_guide()
        return                             # 입력 칸이 열려 있는 동안은 결과 창을 숨긴다

    # 자료를 넣은 뒤: 큰 제목 없이, 스크롤해도 위에 붙어 있는 막대 하나(약어·어려운 단어·결과 파일·다시 넣기)
    rules = set(ss.active_rules)
    if not fallback_ok():
        rules.discard("SK_FALLBACK")       # 본문 절의 첫 문장은 '분야 배경'이 아니므로 위치로 대신 고르지 않는다
    a = analyze(ss.input_text, frozenset(rules))
    loaded_bar(a)
    resume_prompt()
    if ss.paper is not None and ss.source["kind"] == "pdf":
        paper_overview(ss.paper)
    ss.term_tips = {text_key(a.text): term_meanings(a, ss.translations.get((text_key(a.text), True)))}
    floats, first = float_context(a)
    ss.float_ctx = {text_key(a.text): (floats, first, {s.start: s.idx for s in a.sentences})}

    where = f" · {ss.source['section']}" if ss.source["kind"] == "pdf" else ""
    if protocol_section(a):                # 실험 절차 절: 문제·방법·결과 대신 원문의 주의·멈춤 지점·소요 시간 표시
        st.markdown(sec_head("01", f"먼저 볼 실험 절차 요점{where}", "원문에 표시된 주의·멈춤 지점·소요 시간 · 정규식으로 찾음"),
                    unsafe_allow_html=True)
        with st.container(key="card_row"):
            protocol_cards(a)
    else:
        st.markdown(sec_head("01", f"먼저 읽을 3줄 뼈대{where}", "원문에서 찾은 문장 · 신호 문구와 선택 이유를 함께 표시"),
                    unsafe_allow_html=True)
        cols = st.container(key="card_row").columns(3)
        tr = ss.translations.get((text_key(a.text), True))
        for col, card in zip(cols, a.skeleton):
            i = card.sentence_idx
            ko = tr.ko[i] if tr and tr.ko and i is not None and i < len(tr.ko) else None
            ko_ul = as_wrappers(tr.ko_underlines[i]) if ko and tr.ko_underlines and i < len(tr.ko_underlines) else None
            col.markdown(skeleton_card(card, a, ko, ko_ul), unsafe_allow_html=True)
    if a.limit_idx:
        st.markdown(f'<div class="pp-limit">한계·향후 과제 문장: {", ".join("문장 " + str(i + 1) for i in a.limit_idx)}</div>',
                    unsafe_allow_html=True)

    # 제목 줄(02 · 단계 보기 토글) 아래에 칩을 한 줄 전체 폭으로: 가운데 좁은 칸에 두면 칩이 많을 때 토글을 덮었다
    t1, t3 = st.columns([3, 1], vertical_alignment="center")
    t1.markdown(sec_head("02", "원문과 번역 읽기"), unsafe_allow_html=True)
    with t3:
        st.toggle("읽기 전용 단계 보기", key="show_steps")
    with st.container(key="chip_row"):
        st.markdown('<div class="pp-nav-label">형광펜 켜고 끄기 · 누르면 그 표시만 꺼지고 다시 누르면 켜져요</div>',
                    unsafe_allow_html=True)
        mark_chips(a)
    if ss.show_steps:
        steps_panel(a)

    ss.setdefault("read_mode", READ_MODES[0])
    with st.container(key="read_mode_bar"):
        st.segmented_control("읽기 모드", READ_MODES, key="read_mode", format_func=lambda m: READ_MODE_LABEL[m],
                             label_visibility="collapsed", on_change=track_position)
    mode = ss.read_mode or READ_MODES[0]       # 같은 버튼을 다시 눌러 선택이 풀려도 기본 모드로 보여 준다
    parts = part_picker(a)                     # 긴 글은 소제목(없으면 30문장) 단위로 나눠 읽는다
    uls = card_underlines(a)
    ctx = cur_float_ctx(a)
    if ctx[0]:
        st.markdown(float_style(ctx[0]), unsafe_allow_html=True)   # 이 글이 언급한 표·그림 이미지(한 번만)
    rows = "".join(f'{sub_row(a, s.idx)}<div class="pp-sent"><div class="pp-no">{s.idx + 1}</div>{en_html(a, s, uls)}</div>'
                   f'{float_cards(s.idx, ctx)}' for s in shown(a))   # 처음 언급한 문장 바로 아래에 표·그림
    en_panel = (f'<div class="pp-panel pp-narrow"><div class="pp-reader-head"><div class="pp-reader-title">'
                f'<span class="pp-lang">EN</span>영어 원문 <span class="pp-meta">· {range_note(a)}</span></div>'
                f'<div class="pp-meta">표현에 마우스를 올리거나 Tab으로 이동하면 정의와 규칙이 보여요</div></div>'
                f'<div class="pp-sents">{rows or "<div class=pp-meta>문장이 없어요.</div>"}</div></div>')
    if mode == "side":
        translation_panel(a, rows_on=False, where="아래에서 영어 문장과 한 줄씩 나란히")
        if current_translation(a) is None:     # 번역 전에는 오른쪽 칸이 통째로 비므로 영어 원문만 한 칸으로
            st.markdown(en_panel, unsafe_allow_html=True)
        else:
            side_panel(a)                  # 문장 짝마다 한 행: 영어·한국어 높이가 같이 늘어 끝까지 어긋나지 않는다
    elif mode == "pair":
        translation_panel(a, rows_on=False, where="아래 원문 문장마다 바로 아래에")   # 번역하기 버튼을 문장 목록 위에
        interleaved_panel(a)
    elif mode == "en":
        st.markdown(en_panel, unsafe_allow_html=True)      # 한 언어만 볼 때는 줄이 너무 길지 않게 가운데 좁은 폭
    else:
        with st.container(key="ko_only"):
            translation_panel(a, floats_on=True)
    # 이전·다음(부분, 절 끝이면 절)은 본문 바로 끝에, 본문과 같은 폭(표·그림 안내는 그 아래)
    narrow = mode in ("en", "ko") or (mode == "side" and current_translation(a) is None)   # 본문이 가운데 좁은 칸일 때
    part_nav_buttons(parts, narrow=narrow)
    if ctx[0]:
        st.caption(f"이 글에 나온 표·그림 {len(ctx[0])}개({', '.join(t.label for t in ctx[0].values())})는 처음 언급한 "
                   "문장 바로 아래에 접어 두었어요. 누르면 펼쳐지고, 'Table 2'·'Fig. 3' 표시에 마우스를 올리면 미리보기가 떠요.")
    st.markdown('<hr class="pp-end">', unsafe_allow_html=True)   # 본문 끝과 03 보조 자료를 나누는 선
    track_position(user=False)             # 처음 연 위치 등 콜백 없이 바뀐 위치(다음 실행의 브라우저 저장에 실림)

    st.markdown('<div id="pp-03"></div>' + sec_head("03", "이해를 돕는 자료", "흐름도·단어표 — 정규식이 찾은 결과를 한눈에 모은 것"),
                unsafe_allow_html=True)
    tr = ss.translations.get((text_key(a.text), True))
    rows = G.build(a, G.korean_marks(tr))
    tab_flow, tab_gloss, tab_rule = st.tabs(["논문 흐름도", f"어려운 단어 {len(rows)}", f"약어·규칙 근거 {len(a.acronyms)}"])
    with tab_flow:                         # 세로 길이를 줄이려고 한 번에 하나만 보이는 탭으로
        st.markdown(flow_map(a, tr), unsafe_allow_html=True)
    with tab_rule:
        evidence_cards(a)
    with tab_gloss:
        glossary_block(a, rows)

    st.markdown('<div class="pp-foot"><span><strong>읽는 길은 정규식이, 판단은 학생이.</strong> '
                '원문은 수정하거나 요약문으로 대체하지 않습니다.</span><span>번역은 버튼을 누를 때만 · 외부 서비스 전송 안내 포함</span></div>',
                unsafe_allow_html=True)
    maybe_tutorial()                       # 화면 위 안내는 가리킬 요소가 다 그려진 맨 끝에서


def glossary_block(a, rows) -> None:
    """어려운 단어표(AI 뜻 풀이·CSV 버튼, 처음 8개 + 전체 보기, 이름 칩)."""
    ss = st.session_state
    meanings = ss.ai_gloss.get(text_key(a.text))
    if meanings:
        G.apply_ai(rows, meanings)
    g1, g2, g3 = st.columns([3, 1.1, 1.1], vertical_alignment="bottom")
    g1.markdown(f'<div class="pp-sub-title">{f"어려운 단어표 · {len(rows)}개" if rows else "어려운 단어 없음"}</div>',
                unsafe_allow_html=True)
    claude_ok = engine()[0] == "claude"
    if g2.button("AI로 뜻 풀이 받기", key="ai_gloss_btn", icon=":material/auto_awesome:", use_container_width=True,
                 disabled=not rows or not claude_ok or bool(meanings),
                 help=("단어 선정은 정규식, 뜻 풀이만 Claude Haiku가 써요(참고용). 단어와 처음 나온 문장이 Claude로 전송돼요."
                       if claude_ok else "Claude API 키가 있을 때만 쓸 수 있어요.")):
        fetch_meanings(a, rows)
        st.rerun()
    g3.download_button("단어표 내려받기(CSV)", G.to_csv(rows), file_name="paperpath_glossary.csv", mime="text/csv",
                       key="glossary_csv", disabled=not rows, use_container_width=True)
    if ss.get("ai_gloss_error"):
        st.warning(ss.ai_gloss_error)
    st.caption("한국어로 옮겨도 뜻이 바로 안 풀릴 전문용어만 모았어요. 한국어·AI 풀이는 참고용이에요.",
               help="고르는 기준: 일상 영어에서 아주 드문 말(wordfreq Zipf 3.0 미만, 자주 나오는 말은 3.5 미만), 글 안에 풀네임이 "
                    "있는 약어. 흔한 말에 접사가 붙은 말(unlabeled, bidirectional)·부사·사람 이름(et al. 같은 인용 표시가 붙는 말)·"
                    "수식 기호는 뺐어요. 같은 낱말의 어형(annotate·annotating)은 한 줄로 묶고 횟수를 합쳐요. "
                    f"'한국어'는 번역기가 옮긴 대응어, 'AI 풀이'는 Claude Haiku가 쓴 뜻풀이예요(AI 풀이는 {MAX_TERMS}개까지).")
    st.markdown(glossary_table(rows[:GLOSS_PREVIEW]), unsafe_allow_html=True)
    if len(rows) > GLOSS_PREVIEW:
        with st.expander(f"전체 단어표 보기 · {len(rows)}개"):
            st.markdown(glossary_table(rows), unsafe_allow_html=True)
    st.markdown(names_line(a, rows), unsafe_allow_html=True)


def evidence_cards(a) -> None:
    """탐지된 약어·규칙 근거 카드(단어표 위). 버튼은 아이콘을 붙여 눌러지는 것임을 분명히 한다."""
    b1, b2 = st.columns(2)
    defined = {k: v for k, v in a.acronyms.items() if v}
    first = next(iter(defined.items()), None)
    with b1, st.container(key="ev_abbr"):
        m1, m2 = st.columns([4, 1.1], vertical_alignment="center")
        snip = f'<span class="pp-snip">{esc(first[0])} → {esc(first[1])}</span>' if first else ""
        m1.markdown(f'<div class="pp-mini"><div class="pp-mini-icon">{ICON["list"]}</div><div>'
                    f'<div class="pp-mini-title">탐지된 약어 {len(a.acronyms)}개 {snip}</div>'
                    f'<div class="pp-mini-desc">괄호 안 약어 후보를 찾고, 앞 단어의 첫 글자를 맞춰 풀네임을 확인합니다.</div>'
                    f'</div></div>', unsafe_allow_html=True)
        with m2.popover("정의 확인", icon=":material/menu_book:", use_container_width=True):
            if a.acronyms:
                st.markdown("\n".join(f"- **{k}** → {v if v else '정의를 찾지 못함'}" for k, v in a.acronyms.items()))
            else:
                st.write("탐지된 약어가 없어요.")
    with b2, st.container(key="ev_rule"):
        m1, m2 = st.columns([4, 1.1], vertical_alignment="center")
        m1.markdown(f'<div class="pp-mini"><div class="pp-mini-icon">{ICON["rule"]}</div><div>'
                    f'<div class="pp-mini-title">규칙 근거 <span class="pp-snip">may / might / could</span></div>'
                    f'<div class="pp-mini-desc">추측·가능성 표현을 한 등급으로 표시하고, 가능·허용 의미는 제외합니다.</div>'
                    f'</div></div>', unsafe_allow_html=True)
        m2.button("규칙 보기", key="to_rules", on_click=go_page, args=("규칙 탐색",), icon=":material/rule:",
                  use_container_width=True)


# ================================================================ 규칙 탐색

def rules_page() -> None:
    st.markdown('<div class="pp-intro"><div><div class="pp-eyebrow">RULE EXPLORER</div>'
                '<h1 class="pp-h1">화면의 모든 표시는 이 규칙에서 나와요.</h1>'
                '<div class="pp-desc">준비된 규칙만 켜고 끌 수 있어요. 임의의 정규식을 입력하는 기능은 없습니다.</div></div></div>',
                unsafe_allow_html=True)
    with st.container(key="rule_panel"):
        st.markdown("**처리 단계(읽기 전용)**")
        st.markdown('<div class="pp-steps">' + "".join(
            f'<div class="pp-step"><b>{t}</b><span>{d}</span></div>' for t, d in [
                ("① 전처리", "줄바꿈·공백·하이픈·쪽번호·반복 머리말"),
                ("② 문장 분리", "수식·인용·URL을 가린 뒤 마침표 경계"),
                ("③ 약어", "괄호 안 후보(regex) → 글자 맞추기(Python)"),
                ("④ 3줄 뼈대", "신호 문구 → 위치 대체(추정 표시)"),
                ("⑤ 추측·확신·참조", "단일 등급 + 가능·허용 제외"),
                ("⑥ 번역(선택)", "보호 마스킹 → Claude Haiku 또는 DeepL → 태그로 짝 복원"),
            ]) + "</div>", unsafe_allow_html=True)
        st.markdown("**규칙 켜고 끄기 미리보기**")
        rule_toggles()
        a = analyze(RULE_DEMO, frozenset(st.session_state.active_rules))
        s = a.sentences[0]
        st.markdown(f'<div class="pp-text" style="padding:8px 0">{render_marked(a.text, s.start, s.end, a.marks)}</div>',
                    unsafe_allow_html=True)
        st.caption("예: '추측 제외(가능·허용)'를 끄면 may be applied의 may가 추측으로 칠해져요.")

    st.markdown(sec_head("R", "준비된 규칙 목록", "patterns.py에서 그대로 가져온 내용"), unsafe_allow_html=True)
    for r in (*P.TOKEN_RULES, *P.SKELETON_RULES, P.POSITION_FALLBACK_RULE):
        with st.expander(f"{r.label} · {r.name}"):
            st.write(r.desc)
            if r.reason:
                st.caption(f"화면 이유 문구: {r.reason}")
            if r.examples:
                st.markdown("**잡는 예**: " + " / ".join(f"`{e}`" for e in r.examples))
            if r.misses:
                st.markdown("**일부러 놓치는 경우**: " + " / ".join(f"`{e}`" for e in r.misses))
            st.code(r.pattern.pattern, language="regex")


# ================================================================ 검증 결과

@st.cache_data(show_spinner=False)
def cached_skeleton_stats() -> dict:
    return skeleton_stats(load_samples())


def validation_page() -> None:
    st.markdown('<div class="pp-intro"><div><div class="pp-eyebrow">VALIDATION</div>'
                '<h1 class="pp-h1">측정한 것과 목표를 나눠서 보여 드려요.</h1>'
                '<div class="pp-desc">표본이 작아 일반화하지 않습니다. 측정 전인 항목은 목표나 예정으로 표시합니다.</div></div></div>',
                unsafe_allow_html=True)
    stats = cached_skeleton_stats()
    n = stats["n"]
    so, wf = stats["signal_only"], stats["with_fallback"]
    bench = load_benchmark()
    if bench:
        b = bench["summary"]
        mask_row = ("measured", f"{b['date']} · 초록 {b['papers']}편 · 보호 토큰 {b['tokens']}개 중 문자열 그대로 남은 것: "
                    f"마스킹 켬 {b['on_kept']}개 / 끔 {b['off_kept']}개. 단, 끔 쪽 차이 대부분은 값이 같은 표기 변환이었고 "
                    "의미 있는 손실은 약어 2건뿐. 켬 쪽에서는 수치 보호 때문에 틀린 숫자가 1건 나와 수치 보호를 뺐어요(아래 상세)")
    else:
        mask_row = ("planned", "2주차 실측 예정(30편). 사전 3문장 실험(2026-11-04)에서는 마스킹 끔에서도 BERT·3.1%·[4]가 "
                    "보존됐고 Table 1이 '표 1'로 바뀐 차이만 있었어요")
    rows = [
        ("3줄 뼈대 채움률 — 신호 문구만", "measured",
         f"arXiv cs.CL 초록 {n}편(연도 6개 × 50편) 중 셋 다 채움 {so['all_three_filled']}편 ({so['all_three_filled'] / n:.0%})"),
        ("3줄 뼈대 채움률 — 위치 대체 포함", "measured",
         f"같은 {n}편 중 셋 다 채움 {wf['all_three_filled']}편 ({wf['all_three_filled'] / n:.0%}). 위치 대체 카드는 '추정'으로 표시"),
        ("마스킹 기여(on/off)", *mask_row),
        ("약어 정밀도", "target", "목표 0.9 이상 · 3주차 초록 20편 검수 예정(미측정)"),
        ("추측·가능성 정밀도", "target", "목표 0.8 이상 · 3주차 초록 20편 검수 예정(미측정)"),
        ("규칙 재현율", "planned", "10편 소표본 점검 예정(미측정)"),
        ("규칙 테스트", "measured", "tests/ 폴더의 양성·음성·경계 사례. `py -I -m pytest`로 실행"),
    ]
    label = {"measured": "측정", "target": "목표", "planned": "예정"}
    body = "".join(f'<tr><td>{esc(t)}</td><td><span class="pp-tag {k}">{label[k]}</span></td><td>{esc(d)}</td></tr>'
                   for t, k, d in rows)
    st.markdown(f'<div class="pp-panel" style="padding:6px 8px"><table class="pp-table"><thead><tr><th>항목</th>'
                f'<th>상태</th><th>내용</th></tr></thead><tbody>{body}</tbody></table></div>', unsafe_allow_html=True)
    with st.expander("카드별 근거 분포(측정값)"):
        st.write({"신호 문구만": so["by_card"], "위치 대체 포함": wf["by_card"]})
    if bench and bench.get("analysis"):
        an = bench["analysis"]
        with st.expander("마스킹 실측 상세 — 손실을 하나씩 분류한 결과"):
            st.caption(an["metric_note"])
            st.markdown(f"**마스킹 끔에서 문자열이 바뀐 토큰 {an['off_lost_total']}개**")
            for title, items in (("값은 같고 표기만 바뀜", an["off_lost_benign"]),
                                 ("의미 있는 손실", an["off_lost_meaningful"]),
                                 ("탐지 오류(수정함)", an["off_lost_detection_error"])):
                st.markdown(f"- {title}: " + ", ".join(f"{k} {v}개" for k, v in items.items()))
            st.markdown("**마스킹 켬에서 생긴 문제**: " + ", ".join(f"{k} ({v}개)" for k, v in an["on_lost"].items()))
            st.markdown(f"**결정**: {an['decision']}")
            st.caption(f"설정: {bench['summary'].get('config', '')} · 전송량 약 {bench['summary'].get('chars_sent_approx', 0):,}자")
    st.markdown(sec_head("!", "알려진 한계"), unsafe_allow_html=True)
    st.markdown(
        "- 샘플은 연도별 연말 최신 제출 50편이라 대표성이 없어요.\n"
        "- `XGBoost`, `BiLSTM`, `k-NN` 같은 약어는 풀네임을 찾지 못해요(틀린 풀네임을 만들지 않고 '정의를 찾지 못함'으로 표시).\n"
        "- 추측 표시는 조동사 자체를 표시하며, 문장을 넘는 문맥은 보지 않아요.\n"
        "- 번역은 말투(~습니다/~한다)가 섞일 수 있어요.\n"
        "- PDF 2단 편집 순서는 복원하지 않아요. arXiv ID 입력을 권장해요.\n"
        "- 번역은 Claude Haiku 5.5(AI 번역, 키가 있을 때) 또는 DeepL이 해요. AI 번역은 드물게 뜻을 바꾸거나 빼먹을 수 있어 참고용이에요.\n"
        "- 번역 서비스 측의 데이터 보관은 각 서비스의 약관을 따르며, 이 앱이 따로 확인하지는 못했어요.")


# ================================================================ 메인

def main() -> None:
    st.set_page_config(page_title="PaperPath — 처음 읽는 영어 논문 도우미", page_icon="📄", layout="wide",
                       initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    init_state()
    browser_sync()
    account_sync()
    topbar()
    page = st.session_state.page
    if page == PAGES[0]:
        reading_page()
    elif page == PAGES[1]:
        rules_page()
    else:
        validation_page()


main()
