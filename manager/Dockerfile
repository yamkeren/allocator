# Build context is the repo root (see deploy/docker-compose.yml) so the image
# can include the shared allocator-contract package alongside the manager.
FROM python:3.12-slim

WORKDIR /app

# Install the shared contract first (changes rarely → good cache layer).
COPY contract/ ./contract/
RUN pip install --no-cache-dir ./contract

# Cache dependency install — create a stub package so pip can resolve deps
# without the full source tree, then replace it with the real code.
COPY manager/pyproject.toml .
RUN mkdir -p allocator_manager && touch allocator_manager/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf allocator_manager

COPY manager/ .

EXPOSE 8000

# Run migrations then start the server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn allocator_manager.main:app --host 0.0.0.0 --port 8000"]
