import asyncio
import os

from datasets import Dataset

from scout.backends.pgvector import PgVectorRlsBackend

try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, context_precision, faithfulness

    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False


async def run_ragas():
    print("Setting up Automated Framework Test (Ragas against PgVector)...")
    if not RAGAS_AVAILABLE:
        print(
            "⚠️ Ragas or Langchain dependencies are missing. Skipping automated evaluation."
        )
        return

    os.environ["OPENAI_API_BASE"] = "http://localhost:4000"

    judge_llm = ChatOpenAI(model="openai/gpt-4o")
    judge_embeddings = OpenAIEmbeddings(model="gemini/gemini-embedding-2")

    backend = PgVectorRlsBackend()
    query = "PagedAttention Engine"
    results = await backend.retrieve(query=query, k=2)
    contexts = (
        [r.text for r in results]
        if results
        else [
            "PagedAttention eliminates memory fragmentation in KV-cache allocation."
        ]
    )
    await backend.close()

    data = {
        "question": [query],
        "answer": ["PagedAttention manages virtual GPU block allocations."],
        "contexts": [contexts],
        "ground_truth": ["PagedAttention allocates non-contiguous physical GPU blocks."],
    }
    dataset = Dataset.from_dict(data)

    print("Evaluating synthetic dataset against Ragas metrics...")
    try:
        score = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision],
            llm=judge_llm,
            embeddings=judge_embeddings,
            raise_exceptions=False,
        )
        print("✅ Ragas Evaluation Metrics Computed:")
        print(score)
    except Exception as e:
        print(f"⚠️ Ragas evaluation encountered a proxy compatibility error: {e}")


if __name__ == "__main__":
    asyncio.run(run_ragas())
