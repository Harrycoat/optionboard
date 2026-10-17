"""Read-only SORA training example retrieval. Does not call any model or change trading services.

This module is safe to integrate into api/sora_chat.py after isolated tests.
Input data: docs/sora-openai-training-500.jsonl. Absence means no examples.
"""
from pathlib import Path
import json
import re

DATA = Path(__file__).resolve().parent.parent / "docs" / "sora-openai-training-500.jsonl"

def _terms(value):
    return set(re.findall(r"[가-힣]{2,}|[a-zA-Z0-9]+", str(value).lower()))

def training_examples(question, limit=2, path=None):
    """Return up to two relevant, non-sensitive training examples.

    Files are reference examples, not verified price data. Never use this
    function as the source of live scanner URLs or trading market figures.
    """
    location = Path(path) if path is not None else DATA
    if not location.is_file():
        return []
    q = _terms(question)
    if not q:
        return []
    ranked = []
    try:
        with location.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file):
                if line_number >= 1000:
                    break
                try:
                    example = json.loads(line)
                except (ValueError, TypeError):
                    continue
                user = str(example.get("user", ""))
                answer = str(example.get("target_answer", ""))
                if not user or not answer:
                    continue
                score = len(q & _terms(user))
                if score:
                    ranked.append((score, line_number, {
                        "category": str(example.get("category", ""))[:100],
                        "question": user[:400],
                        "target_answer": answer[:600],
                    }))
    except OSError:
        return []
    ranked.sort(key=lambda item: (-item[0], item[1]))
    count = min(2, max(0, int(limit)))
    return [item[2] for item in ranked[:count]]
