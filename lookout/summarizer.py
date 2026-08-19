"""Local extractive page summarization -- no API keys, no external services.

Pipeline:

1. Strip boilerplate (scripts, styles, nav, footer, ads...) and pick the
   densest text container as the main content (a lightweight readability
   heuristic).
2. Split into sentences.
3. Score each sentence by the frequency of its significant words across the
   whole document (classic frequency-based extractive summarization), with a
   small position bonus for early sentences.
4. Return the top-N sentences in their original order, so the summary reads
   naturally.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from bs4 import BeautifulSoup

_BOILERPLATE_TAGS = (
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "iframe", "svg", "button", "figure",
)

# Small bilingual stopword list (EN + DE) -- enough to keep scoring sane
# without pulling in NLTK.
_STOPWORDS = frozenset("""
a an and are as at be but by for from has have if in into is it its of on or
that the their there these they this to was were will with you your not can
we our i he she his her them then than so what which who whom about after all
also am any been before being below between both did do does doing down during
each few further here how more most no nor only other out over own same some
such too under until up very when where why
der die das den dem des ein eine einen einem eines und oder aber ist sind war
waren wird werden mit von zu zur zum im in am auf für nicht auch als bei nach
über unter aus durch wenn dann noch nur schon sich sie er es wir ihr ich du
man kann muss soll hat haben dass wie was wer wo mehr sehr
""".split())

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9\"'(])")
_WORD = re.compile(r"[a-zA-ZäöüÄÖÜß]{2,}")


@dataclass(frozen=True)
class PageSummary:
    title: str
    sentences: list[str]
    word_count: int          # words in the extracted main content

    @property
    def text(self) -> str:
        return " ".join(self.sentences)


def summarize_html(html: str, max_sentences: int = 7) -> PageSummary:
    """Summarize an HTML page into at most *max_sentences* sentences."""
    soup = BeautifulSoup(html, "lxml")

    title = (soup.title.get_text(strip=True) if soup.title else "") or "Untitled page"

    # Grab headlines BEFORE boilerplate stripping (news portals keep them in
    # heavily-linked containers that the density heuristic discards).
    headlines = _extract_headlines(soup)

    text = _extract_main_text(soup)
    sentences = _split_sentences(text)

    # News front pages / link portals have almost no prose sentences --
    # their content IS the headlines. Fall back to those.
    if len(sentences) < 3 and len(headlines) >= 3:
        return PageSummary(
            title=title,
            sentences=headlines[:max_sentences],
            word_count=len(_WORD.findall(" ".join(headlines))),
        )

    if not sentences:
        return PageSummary(title=title, sentences=[], word_count=0)

    word_count = len(_WORD.findall(text))
    if len(sentences) <= max_sentences:
        return PageSummary(title=title, sentences=sentences, word_count=word_count)

    ranked = _rank_sentences(sentences)
    keep = sorted(sorted(ranked, key=lambda i: ranked[i], reverse=True)[:max_sentences])
    return PageSummary(
        title=title,
        sentences=[sentences[i] for i in keep],
        word_count=word_count,
    )


# --- content extraction --------------------------------------------------------


def _extract_headlines(soup: BeautifulSoup) -> list[str]:
    """Collect unique headline-like texts (h1-h3 + common headline markup)."""
    seen: set[str] = set()
    headlines: list[str] = []
    for node in soup.find_all(["h1", "h2", "h3"]):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text.split()) < 4 or len(text) > 200:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        headlines.append(text)
    return headlines


def _extract_main_text(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(_BOILERPLATE_TAGS):
        tag.decompose()

    # Prefer semantic containers if present.
    candidates = soup.find_all(["article", "main"]) or soup.find_all("div") or [soup]

    def density(node) -> float:
        text = node.get_text(" ", strip=True)
        links_len = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        if not text:
            return 0.0
        # Long text with a low share of link text looks like real content.
        link_ratio = links_len / max(len(text), 1)
        return len(text) * (1.0 - min(link_ratio, 0.9))

    best = max(candidates, key=density)
    if density(best) < 200:  # page is mostly chrome; fall back to whole body
        best = soup.body or soup
    return re.sub(r"\s+", " ", best.get_text(" ", strip=True))


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if len(p.split()) >= 4]


# --- scoring --------------------------------------------------------------------


def _rank_sentences(sentences: list[str]) -> dict[int, float]:
    freq: Counter[str] = Counter()
    tokenized: list[list[str]] = []
    for s in sentences:
        words = [w.lower() for w in _WORD.findall(s) if w.lower() not in _STOPWORDS]
        tokenized.append(words)
        freq.update(words)

    if not freq:
        return {i: 0.0 for i in range(len(sentences))}

    max_freq = freq.most_common(1)[0][1]
    scores: dict[int, float] = {}
    for i, words in enumerate(tokenized):
        if not words:
            scores[i] = 0.0
            continue
        base = sum(freq[w] / max_freq for w in words) / math.sqrt(len(words))
        position_bonus = 1.15 if i < max(3, len(sentences) // 10) else 1.0
        scores[i] = base * position_bonus
    return scores
