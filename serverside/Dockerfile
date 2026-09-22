FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV HF_HOME=/app/models/huggingface
ENV TORCH_HOME=/app/models/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt \
    && pip check

COPY db ./db
COPY static ./static
COPY *.py .

EXPOSE 6100

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6100", "--workers", "1"]
