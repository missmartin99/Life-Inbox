"""Meaning-based search with embeddings.

An embedding turns a piece of text into a list of numbers (a "vector", here
384 numbers) such that texts with similar MEANING get similar vectors:

    "pay rent on the 5th"   -> [0.12, -0.03, 0.40, ...]
    "money stuff"           -> [0.10, -0.01, 0.38, ...]   close!
    "buy a birthday cake"   -> [-0.30, 0.22, 0.05, ...]   far away

Keyword search can't match "money stuff" to "pay rent" (no shared words);
embeddings can. We compare vectors with cosine similarity (1 = same meaning,
0 = unrelated).

Design choices:
  * The model runs LOCALLY (fastembed + a small ONNX model, ~90 MB), so note
    text never leaves the machine -- no privacy or cost questions.
  * Turned on with LIFE_INBOX_EMBEDDINGS=1. When off, or if the model can't
    load, search quietly falls back to keywords only.
  * Vectors are cached in the embedding_cache table by a hash of the text.
    If an item is edited, its text (and hash) changes, so it simply gets a
    new vector -- no "is this cache out of date?" logic needed.
"""

import hashlib
import json
import logging
import math
import os

logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv(
    "LIFE_INBOX_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)

_model = None  # loaded on first use, then kept in memory


def embeddings_enabled():
    return os.getenv("LIFE_INBOX_EMBEDDINGS", "").strip().lower() in {"1", "true", "yes"}


def embed_texts(texts):
    """Turn texts into vectors with the local model. Returns None on failure."""
    global _model
    try:
        if _model is None:
            from fastembed import TextEmbedding

            logger.info("Loading embedding model %s (first use only)", MODEL_NAME)
            _model = TextEmbedding(model_name=MODEL_NAME)
        return [[float(x) for x in vector] for vector in _model.embed(texts)]
    except Exception:
        logger.warning("Embedding model unavailable; using keyword search only", exc_info=True)
        return None


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_vectors(texts):
    """Vectors for many texts, using the cache and embedding only new texts.

    Returns a list of vectors in the same order as `texts`, or None if
    embeddings are unavailable.
    """
    from app.database import load_cached_vectors, save_cached_vectors

    hashes = [text_hash(t) for t in texts]
    cached = load_cached_vectors(hashes, MODEL_NAME)

    missing = [(h, t) for h, t in zip(hashes, texts) if h not in cached]
    if missing:
        new_vectors = embed_texts([t for _, t in missing])
        if new_vectors is None:
            return None
        fresh = {h: v for (h, _), v in zip(missing, new_vectors)}
        save_cached_vectors(fresh, MODEL_NAME)
        cached.update(fresh)

    return [cached[h] for h in hashes]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# Calibration. Measured with all-MiniLM-L6-v2 on sample notes:
#   unrelated pairs        ~0.00 - 0.15   ("teeth checkup" vs "buy cake": 0.10)
#   right answer           ~0.20 - 0.45   ("teeth checkup" vs "dentist": 0.41)
# Raw values are small, so we stretch 0.10 .. 0.50 onto 0 .. 1.
LOW, HIGH = 0.10, 0.50


def rescale(similarity):
    return min(1.0, max(0.0, (similarity - LOW) / (HIGH - LOW)))


def meaning_scores(query, passage_texts):
    """0..1 meaning score between the query and each passage, or None if off."""
    if not embeddings_enabled() or not passage_texts:
        return None
    query_vector = embed_texts([query])
    passage_vectors = get_vectors(passage_texts)
    if query_vector is None or passage_vectors is None:
        return None
    return [rescale(cosine(query_vector[0], v)) for v in passage_vectors]


def vector_to_text(vector):
    return json.dumps([round(x, 6) for x in vector])
