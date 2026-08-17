import asyncio
import os

from scout.backends.rag_anything_http import RagAnythingHttpBackend


async def stress_test() -> None:
    backend = RagAnythingHttpBackend(base_url="http://localhost:8000")

    questions = [
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "TCP three-way handshake"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "sliding window flow control"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "connection teardown"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "lower-layer protocol reliance"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "sequence numbers"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "acknowledgment numbers"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "timeout and retransmission"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "maximum segment size"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "TCP header format"},
        {"path": "raw/rfcs/rfc793-tcp.md", "hint": "port numbers"},
    ]

    print("Starting continuous stress test on RagAnythingHttpBackend...")

    i = 0
    import random

    while True:
        if i >= 3:
            print("Completed 3 iterations without failure!")
            break
        i += 1
        q = random.choice(questions)
        print(f"\\n--- Query {i}: {q['hint']} ---")
        try:
            chunks = await backend.retrieve(hint=q["hint"], path=q["path"])

            if not chunks:
                print("FAILED: Empty chunks returned.")
                # Maybe litellm rate limit, just wait a bit instead of breaking completely? Let's just break for now.
                break

            context_text = chunks[0].text
            print(f"Context snippet: {context_text[:150]}...")

            # Check for AI hallucination signs
            lower_ctx = context_text.lower()
            hallucination_signs = [
                "as an ai",
                "i cannot",
                "language model",
                "sorry",
                "i'm sorry",
                "here is the",
                "as a language model",
            ]
            if any(sign in lower_ctx for sign in hallucination_signs):
                print(
                    f"FAILED: Hallucination detected at query {i}! Found a lazy/AI phrase in response."
                )
                break

            print("SUCCESS")

        except Exception as e:
            print(f"FAILED: Exception occurred: {e}")
            break


if __name__ == "__main__":
    if not os.environ.get("LITELLM_MASTER_KEY"):
        os.environ["LITELLM_MASTER_KEY"] = ""
    asyncio.run(stress_test())
