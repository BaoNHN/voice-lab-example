"""
engine/retrieval_engine.py
Genuinely retrieval-based chat backend for this demo — not a hardcoded stub.
Indexes MrCookieDev/Vietnamese-Chatting-Dataset (Hugging Face, JSONL, ~2k rows
of short Vietnamese dialogue turns) with TF-IDF and answers by nearest-neighbor
lookup. TF-IDF (not an embedding model) is a deliberate choice for a demo this
small — no extra model download beyond Whisper itself, cosine similarity over
~2k short rows is instant.
"""

import json
import os

import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(BASE_DIR, "data")
DATA_PATH   = os.path.join(DATA_DIR, "hypermamba_data.jsonl")
DATASET_URL = (
    "https://huggingface.co/datasets/MrCookieDev/Vietnamese-Chatting-Dataset"
    "/resolve/main/hypermamba_data.jsonl"
)

SIMILARITY_FLOOR = 0.15
FALLBACK_ANSWER  = "Xin lỗi, mình chưa có câu trả lời phù hợp cho câu hỏi này trong bộ dữ liệu demo."

_vectorizer = None
_matrix     = None
_pairs      = None  # list[(question, answer)]


def _ensure_dataset():
    if os.path.exists(DATA_PATH):
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    resp = requests.get(DATASET_URL, timeout=30)
    resp.raise_for_status()
    with open(DATA_PATH, "wb") as f:
        f.write(resp.content)


def _load_pairs() -> list:
    pairs = []
    with open(DATA_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            messages = json.loads(line).get("messages", [])
            for i in range(len(messages) - 1):
                if messages[i].get("role") == "user" and messages[i + 1].get("role") == "assistant":
                    question = messages[i]["content"].strip()
                    answer   = messages[i + 1]["content"].strip()
                    if question and answer:
                        pairs.append((question, answer))
    return pairs


def load_index():
    """Call once at app startup — downloads the dataset on first run, fits the
    TF-IDF index in memory. Cheap enough (~2k short rows) to redo on every
    process start rather than persisting the fitted index to disk."""
    global _vectorizer, _matrix, _pairs
    _ensure_dataset()
    _pairs = _load_pairs()
    questions   = [q for q, _ in _pairs]
    _vectorizer = TfidfVectorizer()
    _matrix     = _vectorizer.fit_transform(questions)
    return len(_pairs)


def answer(question: str) -> str:
    if not _pairs:
        raise RuntimeError("retrieval_engine.load_index() must be called before answer()")
    query_vec   = _vectorizer.transform([question])
    similarities = cosine_similarity(query_vec, _matrix)[0]
    best_idx    = similarities.argmax()
    if similarities[best_idx] < SIMILARITY_FLOOR:
        return FALLBACK_ANSWER
    return _pairs[best_idx][1]
