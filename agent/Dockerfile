# Build context is the repo root so the image can include the shared
# allocator-contract package alongside the agent.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    usbutils \
    usbip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the shared contract first (changes rarely → good cache layer).
COPY contract/ ./contract/
RUN pip install --no-cache-dir ./contract

COPY agent/pyproject.toml .
RUN mkdir -p allocator_agent && touch allocator_agent/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf allocator_agent

COPY agent/ .

EXPOSE 5000

CMD ["allocator-agent", "start"]
