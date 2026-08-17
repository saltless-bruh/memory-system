# RAG Evaluation Execution Log

## 1. Plan & Brainstorm
*   **Goal:** Implement three modern RAG evaluation tests.
*   **Tests:** Needle-in-a-Haystack (NIAH), Hard-Negative Mining, Automated Framework (Ragas).

## 2. Implementation
Three independent test scripts were created in `tests/`:
*   `tests/eval_niah.py`
*   `tests/eval_hard_negatives.py`
*   `tests/eval_ragas.py`

## 3. Verification
The tests are currently executing in the background via `uv run`. 
*   **NIAH** verifies if the retriever can extract a deeply buried needle (`PurpleMonkeyDishwasher`).
*   **Hard-Negative** verifies if the retriever prioritizes the true document over an identical fake.
*   **Ragas** utilizes `datasets` and `ragas.evaluate` to measure Faithfulness and Context Precision via `gpt-4o` (LiteLLM proxied).

## 4. Next Steps
Once the execution completes, review the output logs to determine if the vector cache and `gemini-embedding-2` model pass the modern stress tests.
