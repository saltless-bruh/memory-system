# RAG-Anything service (T-2.1) — runs on Python 3.12 (Linux) because MinerU
# requires >=3.10,<3.14; the host's Python 3.14 cannot install it. This is the
# design's intended topology: RAG-Anything is its own `rag` container (R-9.1),
# reachable ONLY by Scout on the internal network (R-4.2).
FROM python:3.12-slim

# System libs MinerU / torch / OpenCV need at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch first (from the PyTorch CPU index) so the huge CUDA wheels are
# never pulled; mineru[core] then sees torch already satisfied.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.6,<3" torchvision

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# MinerU pulls its parsing models from Hugging Face on first use; cache dir is
# a mounted volume so it downloads once (see docker-compose).
ENV MINERU_MODEL_SOURCE=huggingface \
    HF_HOME=/data/hf_cache \
    RAG_WORKING_DIR=/data/rag_storage \
    RAG_RAW_DIR=/data/raw \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
