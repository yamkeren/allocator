# Build context is the repo root so the image can install the shared contract.
FROM python:3.12-slim

WORKDIR /app

COPY contract/ ./contract/
RUN pip install --no-cache-dir ./contract fastapi "uvicorn[standard]" httpx

COPY e2e/stub_agent/app.py ./app.py

EXPOSE 5000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
