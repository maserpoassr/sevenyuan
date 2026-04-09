# Playwright CLI Docker Deployment

## 1) Build in GitHub Actions

Workflow file: `.github/workflows/docker-image.yml`

After push to `main` (or manual `workflow_dispatch`), image will be pushed to:

- `ghcr.io/<your-github-username>/ldxp-restock-cli:latest`
- `ghcr.io/<your-github-username>/ldxp-restock-cli:sha-...`

## 2) Required runtime env vars

- `WXPUSHER_ENABLED=true`
- `WXPUSHER_APP_TOKEN=AT_xxx`
- `WXPUSHER_UIDS=UID_xxx`

Optional:

- `WXPUSHER_TOPIC_IDS=12345,67890`
- `URL=https://pay.ldxp.cn/shop/GPT`
- `PRODUCT_NAME=GPT PLUS 月卡`
- `OOS_TEXT=缺货`
- `CONFIRMATIONS=1`
- `COOLDOWN=7200`
- `INTERVAL=120`
- `TIMEOUT=45`
- `STATE_FILE=/data/state.json`

## 3) Example docker run (for 爪云)

```bash
docker run -d \
  --name ldxp-restock-cli \
  --restart unless-stopped \
  -v /opt/ldxp-data:/data \
  -e WXPUSHER_ENABLED=true \
  -e WXPUSHER_APP_TOKEN=AT_xxx \
  -e WXPUSHER_UIDS=UID_xxx \
  -e URL=https://pay.ldxp.cn/shop/GPT \
  -e PRODUCT_NAME="GPT PLUS 月卡" \
  -e CONFIRMATIONS=1 \
  -e COOLDOWN=7200 \
  -e INTERVAL=120 \
  ghcr.io/<your-github-username>/ldxp-restock-cli:latest
```

No port mapping is needed (CLI worker only, no Web UI).

## 4) Logs

```bash
docker logs -f ldxp-restock-cli
```
