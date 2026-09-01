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

COPY main.py .
COPY encryption.py .
COPY static ./static

ENV VOXVAULT_PORT=8000
EXPOSE ${VOXVAULT_PORT}

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${VOXVAULT_PORT}"]
