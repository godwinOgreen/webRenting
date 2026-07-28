FROM python:3.13-slim

# Prevents Python from writing .pyc files and enables unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files + README (pyproject.toml references it)
COPY pyproject.toml uv.lock README.md ./

# Use system Python (3.13) instead of uv downloading its own
ENV UV_PYTHON=/usr/local/bin/python3

# Install dependencies
RUN uv sync --no-dev --no-cache

# Copy application code
COPY . .

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
