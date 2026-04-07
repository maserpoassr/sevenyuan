FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor_stock.py wxpusher_client.py config.example.yaml README.md ./

RUN useradd -m -u 10001 appuser
USER appuser

CMD ["python", "monitor_stock.py", "-c", "/app/config.yaml"]
