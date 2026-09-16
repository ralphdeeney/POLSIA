# Multi-stage, non-root production image for the Polsia OS API.
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install "."

FROM python:3.12-slim
RUN useradd --create-home --shell /bin/bash polsia
WORKDIR /app
COPY --from=builder /install /usr/local
COPY polsia ./polsia
COPY alembic.ini ./
COPY alembic ./alembic
USER polsia
EXPOSE 8000
CMD ["uvicorn", "polsia.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
