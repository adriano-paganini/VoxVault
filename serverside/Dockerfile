FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /install /usr/local

COPY *.py .
COPY db ./db
COPY static ./static

ENV HF_HOME=/app/models/huggingface
ENV TORCH_HOME=/app/models/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 6100

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6100"]
