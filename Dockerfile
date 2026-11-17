FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_HTTP_TIMEOUT=300 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

<<<<<<< HEAD
COPY pyproject.toml ./
RUN uv pip compile pyproject.toml -o requirements.txt && \
    uv pip install --system --no-cache --prefix=/install -r requirements.txt

=======
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml README.md ./
>>>>>>> 575219fd90867b52adff80e9d69e7417c4a701f3
COPY app ./app
COPY ingestion ./ingestion
COPY README.md ./
RUN uv pip install --system --no-cache --no-deps --prefix=/install .

<<<<<<< HEAD
=======
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --prefix=/install . && \
    uv pip install --system --prefix=/install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

>>>>>>> 575219fd90867b52adff80e9d69e7417c4a701f3
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
<<<<<<< HEAD
COPY app ./app
COPY ingestion ./ingestion
=======
COPY . /app

# Line 33:
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
>>>>>>> 575219fd90867b52adff80e9d69e7417c4a701f3

# Lines 35-37:
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && \
    chmod +x /usr/local/bin/entrypoint.sh && \
    useradd -m -u 10001 appuser && \
    chown -R appuser:appuser /app

<<<<<<< HEAD
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
=======
# Line 46:
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
>>>>>>> 575219fd90867b52adff80e9d69e7417c4a701f3
