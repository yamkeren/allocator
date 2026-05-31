FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir hatch

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "allocator_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
