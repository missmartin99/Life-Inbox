"""Keyword + lightweight semantic retrieval (local, no vector DB)."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from app.retrieval.documents import TextPassage, _tokenize, load_passages


@dataclass
class RankedPassage:
    score: float
    passage: TextPassage
    keyword_score: float
    semantic_score: float


def _idf(doc_freq: int, corpus_size: int) -> float:
    return math.log((1 + corpus_size) / (1 + doc_freq)) + 1.0


def _build_tfidf_vectors(
    passages: list[TextPassage],
) -> tuple[list[Counter[str]], dict[str, int], int]:
    doc_tokens = [_tokenize(f"{p.title} {p.passage}") for p in passages]
    df: Counter[str] = Counter()
    for tokens in doc_tokens:
        df.update(set(tokens))
    corpus_size = len(passages)
    vectors: list[Counter[str]] = []
    for tokens in doc_tokens:
        tf = Counter(tokens)
        vec: Counter[str] = Counter()
        for term, count in tf.items():
            vec[term] = count * _idf(df[term], corpus_size)
        vectors.append(vec)
    return vectors, dict(df), corpus_size


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[t] * b.get(t, 0) for t in a)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_score(query_tokens: list[str], passage: TextPassage) -> float:
    if not query_tokens:
        return 0.0
    hay = f"{passage.title} {passage.passage}".lower()
    hits = sum(1 for token in query_tokens if token in hay)
    return hits / len(query_tokens)


def search_passages(
    query: str,
    *,
    limit: int = 8,
    item_type: Optional[str] = None,
    processing_status: Optional[str] = "ready",
    min_score: float = 0.12,
) -> list[RankedPassage]:
    q = query.strip()
    if not q:
        return []
    query_tokens = _tokenize(q)
    passages = load_passages(
        item_type=item_type,
        processing_status=processing_status,
    )
    if not passages:
        return []

    vectors, _, _ = _build_tfidf_vectors(passages)
    query_vec: Counter[str] = Counter()
    for token in query_tokens:
        query_vec[token] += 1.0

    ranked: list[RankedPassage] = []
    for passage, vec in zip(passages, vectors):
        kw = _keyword_score(query_tokens, passage)
        sem = _cosine(query_vec, vec)
        score = 0.55 * kw + 0.45 * sem
        if score < min_score:
            continue
        ranked.append(
            RankedPassage(
                score=score,
                passage=passage,
                keyword_score=kw,
                semantic_score=sem,
            )
        )
    ranked.sort(key=lambda row: row.score, reverse=True)
    return ranked[:limit]
