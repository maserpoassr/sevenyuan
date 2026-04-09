FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app

COPY requirements.cli.txt /app/requirements.cli.txt
RUN pip install --no-cache-dir -r /app/requirements.cli.txt

COPY wxpusher_client.py /app/wxpusher_client.py
COPY playwright_restock_cli.py /app/playwright_restock_cli.py
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

RUN chmod +x /app/docker-entrypoint.sh && mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV URL=https://pay.ldxp.cn/shop/GPT
ENV PRODUCT_NAME=GPT PLUS 月卡
ENV OOS_TEXT=缺货
ENV CONFIRMATIONS=1
ENV COOLDOWN=7200
ENV INTERVAL=120
ENV TIMEOUT=45
ENV STATE_FILE=/data/state.json
ENV HEADLESS=true
ENV RUN_ONCE=false

ENTRYPOINT ["/app/docker-entrypoint.sh"]
