from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import yaml

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover
    curl_requests = None

from wxpusher_client import WxPusherClient, WxPusherError


BASE_URL = "https://pay.ldxp.cn"
GOODS_LIST_API = f"{BASE_URL}/shopApi/Shop/goodsList"


class MonitorError(Exception):
    pass


@dataclass
class GoodsSnapshot:
    goods_key: str
    name: str
    price: float
    stock_count: int
    link: str


@dataclass
class StockLevelRules:
    few_max: int = 3
    normal_max: int = 20


def stock_level_text(stock_count: int, rules: StockLevelRules) -> str:
    if stock_count <= 0:
        return "缺货"
    if stock_count <= rules.few_max:
        return "库存少量"
    if stock_count <= rules.normal_max:
        return "库存一般"
    return "库存充足"


def now_cn_str() -> str:
    cn_tz = timezone(timedelta(hours=8))
    return datetime.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")


def setup_logger(log_file: str, log_level: str) -> logging.Logger:
    logger = logging.getLogger("stock_monitor")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise MonitorError(f"配置文件不存在: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {
            "last_stock_count": None,
            "last_in_stock": None,
            "last_notify_ts": 0,
            "last_check_ts": 0,
        }
    with state_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(state_path)


def request_with_retry(
    session: requests.Session,
    url: str,
    payload: Dict[str, Any],
    timeout_sec: int,
    retries: int,
    backoff_sec: float,
) -> Dict[str, Any]:
    max_retries = max(1, retries)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.post(url, json=payload, timeout=timeout_sec)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception as exc:
                snippet = (resp.text or "")[:200].replace("\n", " ")
                if curl_requests is not None:
                    c_resp = curl_requests.post(
                        url,
                        json=payload,
                        timeout=timeout_sec,
                        headers=dict(session.headers),
                        impersonate="chrome124",
                    )
                    c_resp.raise_for_status()
                    return c_resp.json()
                raise MonitorError(f"接口返回非 JSON: status={resp.status_code}, body={snippet}") from exc
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(backoff_sec * attempt)

    raise MonitorError(f"请求失败，重试后仍异常: {last_error}")


def parse_goods(raw_item: Dict[str, Any]) -> GoodsSnapshot:
    extend = raw_item.get("extend") or {}
    stock_count = int(extend.get("stock_count", 0) or 0)
    price = float(raw_item.get("price", 0) or 0)
    return GoodsSnapshot(
        goods_key=str(raw_item.get("goods_key") or ""),
        name=str(raw_item.get("name") or ""),
        price=price,
        stock_count=stock_count,
        link=str(raw_item.get("link") or ""),
    )


def find_target_goods(items: list[Dict[str, Any]], target_goods_key: str, target_goods_name: str) -> Optional[GoodsSnapshot]:
    if target_goods_key:
        for item in items:
            if str(item.get("goods_key") or "") == target_goods_key:
                return parse_goods(item)

    if target_goods_name:
        for item in items:
            if target_goods_name in str(item.get("name") or ""):
                return parse_goods(item)
    return None


def build_notify_markdown(goods: GoodsSnapshot, stock_level: str) -> str:
    return (
        f"# 补货提醒\n\n"
        f"- 商品: **{goods.name}**\n"
        f"- 价格: **￥{goods.price:g}**\n"
        f"- 库存状态: **{stock_level}**\n"
        f"- 库存数量: **{goods.stock_count}**\n"
        f"- 时间: `{now_cn_str()}`\n\n"
        f"[立即购买]({goods.link or (BASE_URL + '/item/' + goods.goods_key)})"
    )


def validate_config(cfg: Dict[str, Any]) -> None:
    required = ["shop", "poll", "notify", "wxpusher", "runtime"]
    for key in required:
        if key not in cfg:
            raise MonitorError(f"配置缺少一级字段: {key}")

    if not cfg["shop"].get("token"):
        raise MonitorError("shop.token 不能为空")
    if not cfg["shop"].get("target_goods_key") and not cfg["shop"].get("target_goods_name"):
        raise MonitorError("target_goods_key 与 target_goods_name 至少要配置一个")
    if not cfg["wxpusher"].get("app_token"):
        raise MonitorError("wxpusher.app_token 不能为空")


def run(config_path: Path, once: bool = False) -> None:
    cfg = load_config(config_path)
    validate_config(cfg)

    runtime = cfg["runtime"]
    log_file = runtime.get("log_file", "./monitor.log")
    log_level = runtime.get("log_level", "INFO")
    logger = setup_logger(log_file=log_file, log_level=log_level)

    state_path = Path(runtime.get("state_file", "./state.json"))
    state = load_state(state_path)

    shop = cfg["shop"]
    poll = cfg["poll"]
    notify = cfg["notify"]
    wx_cfg = cfg["wxpusher"]

    token = str(shop.get("token", "")).strip()
    goods_type = str(shop.get("goods_type", "card")).strip()
    target_goods_key = str(shop.get("target_goods_key", "")).strip()
    target_goods_name = str(shop.get("target_goods_name", "")).strip()

    interval_sec = int(poll.get("interval_sec", 30))
    timeout_sec = int(poll.get("timeout_sec", 10))
    retries = int(poll.get("retries", 3))
    backoff_sec = float(poll.get("backoff_sec", 1.5))

    cooldown_sec = int(notify.get("cooldown_sec", 600))
    send_on_start_if_in_stock = bool(notify.get("send_on_start_if_in_stock", False))

    stock_display = cfg.get("stock_display") or {}
    stock_rules = StockLevelRules(
        few_max=int(stock_display.get("few_max", 3)),
        normal_max=int(stock_display.get("normal_max", 20)),
    )

    wx_client = WxPusherClient(
        app_token=str(wx_cfg.get("app_token", "")).strip(),
        uids=list(wx_cfg.get("uids") or []),
        topic_ids=list(wx_cfg.get("topic_ids") or []),
        timeout=timeout_sec,
    )

    stop_flag = {"stop": False}

    def _handle_stop(signum: int, frame: Any) -> None:
        logger.info("收到停止信号 %s，准备退出...", signum)
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    logger.info(
        "启动库存监控: token=%s, goods_type=%s, target_goods_key=%s, interval=%ss",
        token,
        goods_type,
        target_goods_key or "<未配置>",
        interval_sec,
    )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/shop/{token}",
        }
    )
    payload = {
        "token": token,
        "goods_type": goods_type,
        "current": 1,
        "pageSize": 200,
    }

    first_loop = True
    while not stop_flag["stop"]:
        try:
            data = request_with_retry(
                session=session,
                url=GOODS_LIST_API,
                payload=payload,
                timeout_sec=timeout_sec,
                retries=retries,
                backoff_sec=backoff_sec,
            )

            if data.get("code") != 1:
                raise MonitorError(f"商品接口返回异常: {data}")

            items = (data.get("data") or {}).get("list") or []
            goods = find_target_goods(items, target_goods_key=target_goods_key, target_goods_name=target_goods_name)
            if goods is None:
                logger.warning("未找到目标商品: goods_key=%s, name=%s", target_goods_key, target_goods_name)
            else:
                in_stock = goods.stock_count > 0
                level_text = stock_level_text(goods.stock_count, stock_rules)
                now_ts = int(time.time())
                prev_in_stock = state.get("last_in_stock")
                last_notify_ts = int(state.get("last_notify_ts") or 0)

                should_notify = False
                reason = ""

                if in_stock:
                    if prev_in_stock is False:
                        should_notify = True
                        reason = "缺货转有货"
                    elif first_loop and send_on_start_if_in_stock:
                        should_notify = True
                        reason = "启动时库存可用"
                    elif now_ts - last_notify_ts >= cooldown_sec:
                        should_notify = True
                        reason = "冷却时间到"

                logger.info(
                    "商品=%s(%s), 库存状态=%s, 库存数量=%s, 上次状态=%s, 触发通知=%s",
                    goods.name,
                    goods.goods_key,
                    level_text,
                    goods.stock_count,
                    prev_in_stock,
                    should_notify,
                )

                if should_notify:
                    title = f"【补货提醒】{goods.name} {level_text}"
                    content = build_notify_markdown(goods, level_text)
                    try:
                        wx_client.send(title=title, content=content, retries=retries, retry_interval=backoff_sec)
                        logger.info("发送通知成功: reason=%s", reason)
                        state["last_notify_ts"] = now_ts
                    except WxPusherError as exc:
                        logger.exception("发送通知失败: %s", exc)

                state["last_stock_count"] = goods.stock_count
                state["last_in_stock"] = in_stock
                state["last_goods_name"] = goods.name
                state["last_goods_key"] = goods.goods_key
                state["last_stock_level"] = level_text

            state["last_check_ts"] = int(time.time())
            save_state(state_path, state)

        except Exception as exc:
            logger.exception("监控循环异常: %s", exc)

        first_loop = False
        if once:
            break

        for _ in range(interval_sec):
            if stop_flag["stop"]:
                break
            time.sleep(1)

    logger.info("监控进程已退出")


def main() -> None:
    parser = argparse.ArgumentParser(description="监控 ldxp 店铺单商品库存并通过 WxPusher 通知")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="仅执行一次检查后退出")
    args = parser.parse_args()

    config_path = Path(args.config)
    run(config_path, once=args.once)


if __name__ == "__main__":
    main()
