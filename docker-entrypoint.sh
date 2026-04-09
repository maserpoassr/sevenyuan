#!/usr/bin/env bash
set -euo pipefail

ARGS=(
  --url "${URL:-https://pay.ldxp.cn/shop/GPT}"
  --product-name "${PRODUCT_NAME:-GPT PLUS 月卡}"
  --oos-text "${OOS_TEXT:-缺货}"
  --confirmations "${CONFIRMATIONS:-1}"
  --cooldown "${COOLDOWN:-7200}"
  --interval "${INTERVAL:-120}"
  --timeout "${TIMEOUT:-45}"
  --state-file "${STATE_FILE:-/data/state.json}"
)

if [[ "${HEADLESS:-true}" == "true" ]]; then
  ARGS+=(--headless)
fi

if [[ "${RUN_ONCE:-false}" == "true" ]]; then
  ARGS+=(--once)
fi

exec python /app/playwright_restock_cli.py "${ARGS[@]}"
