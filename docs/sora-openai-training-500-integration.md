# SORA OpenAI 500 training — isolated branch

Base: `feature/sora-pullback-20-50-20261013`.
Only this branch is modified. No UI, robots, portfolio, trades, or deployment changes.

## Current scope
- `api/sora_training_retrieval.py` looks up relevant examples from `docs/sora-openai-training-500.jsonl`. Returns [] if file is absent.
- `tests/test_sora_training_retrieval.py` is a standalone unit test.
- The previously created 500-example JSONL must be added separately and schema-checked.
- **Not yet wired to `api/sora_chat.py`**. Current SORA bot responses are unchanged.

## Next before enabling
1. Transfer approved 500 JSONL from the local prepared training package.
2. Validate JSONL schema, all 500 IDs, and factual/rule consistency.
3. Integrate example selection into SORA-only request handling with priority for current user intent over stale drill state.
4. Test generic chat, topic switching, scanner URL grounding, R01 wrong-answer sequence.
5. Confirm deployed AI provider separately. Never assume OpenAI from code default.
6. Obtain explicit authorization before paid testing, merge or deployment.

VF-0.2 remains authoritative over training examples and third-party market commentary.
