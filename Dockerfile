FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml README.md ./
COPY app ./app
COPY rag ./rag
COPY ingestion ./ingestion

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --prefix=/install . && \
    uv pip install --system --prefix=/install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

FROM python:3.11-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . /app

# Line 33:
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh

# Lines 35-37:
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && \
    chmod +x /usr/local/bin/entrypoint.sh && \
    useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app

# Line 46:
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]