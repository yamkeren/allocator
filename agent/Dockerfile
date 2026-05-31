FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    usbutils \
    usbip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir hatch

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

EXPOSE 5000

CMD ["allocator-agent", "start"]
