from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from wxpusher_client import WxPusherClient, WxPusherError


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now_text()}] {msg}", flush=True)


def parse_csv(raw: str) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_int_csv(raw: str) -> List[int]:
    out: List[int] = []
    for token in parse_csv(raw):
        try:
            out.append(int(token))
        except ValueError:
            log(f"WARN: 忽略无效 topic id: {token}")
    return out


def as_bool(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class RuleConfig:
    out_of_stock_text: str
    confirmations_required: int
    cooldown_seconds: int


@dataclass
class StateData:
    last_stock_text: Optional[str] = None
    consecutive_non_oos: int = 0
    last_notification_ts: float = 0.0
    last_check_ts: float = 0.0
    last_error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateData":
        return cls(
            last_stock_text=data.get("last_stock_text"),
            consecutive_non_oos=int(data.get("consecutive_non_oos") or 0),
            last_notification_ts=float(data.get("last_notification_ts") or 0),
            last_check_ts=float(data.get("last_check_ts") or 0),
            last_error=data.get("last_error"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_stock_text": self.last_stock_text,
            "consecutive_non_oos": self.consecutive_non_oos,
            "last_notification_ts": self.last_notification_ts,
            "last_check_ts": self.last_check_ts,
            "last_error": self.last_error,
        }


def load_state(path: Path) -> StateData:
    if not path.exists():
        return StateData()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return StateData.from_dict(data)
    except Exception as exc:
        log(f"WARN: 读取状态文件失败，已重置: {exc}")
    return StateData()


def save_state(path: Path, state: StateData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _norm_text(value: str) -> str:
    return " ".join((value or "").split())


def _guess_shop_token(url: str) -> str:
    path = (urlparse(url).path or "").strip("/")
    if not path:
        return "GPT"
    last = path.split("/")[-1].strip()
    return last or "GPT"


def _pick_stock_from_goods_list(data: Dict[str, Any], target_name: str) -> tuple[str, str]:
    items = ((data.get("data") or {}).get("list") or []) if isinstance(data, dict) else []
    target = _norm_text(target_name)
    target_lower = target.lower()

    for item in items:
        name = _norm_text(str(item.get("name") or ""))
        if name and name == target:
            stock = int(((item.get("extend") or {}).get("stock_count") or 0))
            return name, ("缺货" if stock <= 0 else "有货")

    for item in items:
        name = _norm_text(str(item.get("name") or ""))
        name_lower = name.lower()
        if name and (name_lower in target_lower or target_lower in name_lower):
            stock = int(((item.get("extend") or {}).get("stock_count") or 0))
            return name, ("缺货" if stock <= 0 else "有货")

    names = [_norm_text(str(x.get("name") or "")) for x in items if isinstance(x, dict)]
    raise RuntimeError(f"API fallback 未找到目标商品: 目标={target}; 商品列表={names}")


def fetch_stock_with_playwright(url: str, product_name: str, headless: bool, timeout_ms: int) -> tuple[str, str]:

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)

            # Vue/SPA sometimes renders after domcontentloaded/networkidle, wait a bit longer for product nodes.
            try:
                page.wait_for_selector(".goods-group-item", timeout=min(timeout_ms, 15000))
            except PlaywrightTimeoutError:
                pass

            target = _norm_text(product_name)
            target_lower = target.lower()
            cards = page.locator(".goods-group-item")
            card_count = cards.count()
            seen_titles: List[str] = []
            matched_title = ""
            stock_text = ""

            for i in range(card_count):
                card = cards.nth(i)
                title = _norm_text(card.locator(".goods-item-info-title").first.inner_text() if card.locator(".goods-item-info-title").count() else "")
                img_alt = _norm_text(card.locator(".goods-item-img img").first.get_attribute("alt") if card.locator(".goods-item-img img").count() else "")
                cand = title or img_alt
                if cand:
                    seen_titles.append(cand)

                cand_lower = cand.lower()
                if not cand:
                    continue

                is_exact = cand == target or img_alt == target
                is_contains = cand_lower in target_lower or target_lower in cand_lower
                if not (is_exact or is_contains):
                    continue

                stock_locator = card.locator(".stock")
                if not stock_locator.count():
                    raise RuntimeError(f"解析失败: stock_node_missing; 商品={cand}")
                stock_text = _norm_text(stock_locator.first.inner_text())
                matched_title = cand
                break

            if not matched_title:
                # Fallback: call same-origin API in browser context (often works when SPA render is delayed)
                token = _guess_shop_token(url)
                api_result = page.evaluate(
                    r"""
                    async (token) => {
                      try {
                        const resp = await fetch('/shopApi/Shop/goodsList', {
                          method: 'POST',
                          headers: {
                            'Content-Type': 'application/json;charset=UTF-8',
                            'Accept': 'application/json, text/plain, */*'
                          },
                          body: JSON.stringify({ token, goods_type: 'card', current: 1, pageSize: 200 })
                        });
                        const text = await resp.text();
                        try {
                          const json = JSON.parse(text);
                          return { ok: true, status: resp.status, json };
                        } catch (_e) {
                          return { ok: false, status: resp.status, text: text.slice(0, 240) };
                        }
                      } catch (e) {
                        return { ok: false, status: -1, text: String(e) };
                      }
                    }
                    """,
                    token,
                )

                if isinstance(api_result, dict) and api_result.get("ok") and isinstance(api_result.get("json"), dict):
                    try:
                        return _pick_stock_from_goods_list(api_result["json"], product_name)
                    except Exception:
                        pass

                page_title = _norm_text(page.title())
                body_text = _norm_text(page.locator("body").first.inner_text()) if page.locator("body").count() else ""
                body_hint = body_text[:120]
                if seen_titles:
                    raise RuntimeError(
                        f"解析失败: product_not_found; 目标={target}; 页面商品={seen_titles}; 页面标题={page_title}; 页面内容片段={body_hint}"
                    )
                raise RuntimeError(
                    f"解析失败: product_not_found; 目标={target}; 页面无商品卡片; 页面标题={page_title}; 页面内容片段={body_hint}"
                )

            if not stock_text:
                raise RuntimeError("解析失败: stock 文本为空")
            return matched_title, stock_text
        finally:
            context.close()
            browser.close()


def should_notify(stock_text: str, state: StateData, rule: RuleConfig, now_ts: float) -> tuple[bool, str]:
    if stock_text.strip() == rule.out_of_stock_text.strip():
        state.consecutive_non_oos = 0
        return False, "仍缺货"

    state.consecutive_non_oos += 1

    if state.consecutive_non_oos < max(1, rule.confirmations_required):
        return False, f"待二次确认({state.consecutive_non_oos}/{rule.confirmations_required})"

    if state.last_notification_ts > 0 and (now_ts - state.last_notification_ts) < max(0, rule.cooldown_seconds):
        return False, "冷却期内"

    return True, "满足通知条件"


def notify_wxpusher(product_name: str, stock_text: str, url: str) -> bool:
    enabled = as_bool(os.getenv("WXPUSHER_ENABLED"), default=False)
    if not enabled:
        log("INFO: WXPUSHER_ENABLED=false，跳过推送")
        return False

    app_token = (os.getenv("WXPUSHER_APP_TOKEN") or "").strip()
    uids = parse_csv(os.getenv("WXPUSHER_UIDS", ""))
    topic_ids = parse_int_csv(os.getenv("WXPUSHER_TOPIC_IDS", ""))
    timeout = int(os.getenv("WXPUSHER_TIMEOUT", "10"))
    base_url = (os.getenv("WXPUSHER_BASE_URL") or "https://wxpusher.zjiecode.com/api/send/message").strip()

    client = WxPusherClient(
        app_token=app_token,
        uids=uids,
        topic_ids=topic_ids,
        timeout=timeout,
        base_url=base_url,
    )

    content = "\n".join(
        [
            f"# 补货提醒: {product_name}",
            "",
            f"- 当前状态: `{stock_text}`",
            f"- 链接: {url}",
            f"- 时间: {now_text()}",
        ]
    )

    try:
        client.send(title=f"补货提醒: {product_name}", content=content)
        log("SUCCESS: WxPusher 推送成功")
        return True
    except WxPusherError as exc:
        log(f"ERROR: WxPusher 推送失败: {exc}")
        return False


def run_once(args: argparse.Namespace, state: StateData, state_file: Path, rule: RuleConfig) -> int:
    now_ts = time.time()
    state.last_check_ts = now_ts
    try:
        log(f"检查开始: {args.url}")
        matched_title, stock_text = fetch_stock_with_playwright(
            url=args.url,
            product_name=args.product_name,
            headless=args.headless,
            timeout_ms=args.timeout * 1000,
        )
        state.last_stock_text = stock_text
        state.last_error = None
        log(f"商品: {matched_title} | 库存: {stock_text}")

        notify, reason = should_notify(stock_text, state, rule, now_ts)
        log(f"判定结果: {reason}")
        if notify:
            if notify_wxpusher(matched_title, stock_text, args.url):
                state.last_notification_ts = now_ts

        save_state(state_file, state)
        return 0
    except PlaywrightTimeoutError:
        state.last_error = "playwright_timeout"
        save_state(state_file, state)
        log(f"ERROR: 页面加载超时 ({args.timeout}s)")
        return 2
    except Exception as exc:
        state.last_error = str(exc)
        save_state(state_file, state)
        log(f"ERROR: 检查失败: {exc}")
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LDXP 补货监控 CLI (Playwright)")
    parser.add_argument("--url", default="https://pay.ldxp.cn/shop/GPT", help="目标页面")
    parser.add_argument("--product-name", default="GPT PLUS 月卡", help="商品名(精确匹配)")
    parser.add_argument("--oos-text", default="缺货", help="缺货文案")
    parser.add_argument("--confirmations", type=int, default=1, help="连续非缺货确认次数")
    parser.add_argument("--cooldown", type=int, default=7200, help="推送冷却秒数")
    parser.add_argument("--interval", type=int, default=120, help="循环模式下检查间隔秒")
    parser.add_argument("--timeout", type=int, default=45, help="页面加载超时秒")
    parser.add_argument("--state-file", default="./state.json", help="状态文件路径")
    parser.add_argument("--once", action="store_true", help="只运行一次后退出")
    parser.add_argument("--headless", action="store_true", help="无头模式运行浏览器")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    state_file = Path(args.state_file)
    state = load_state(state_file)

    rule = RuleConfig(
        out_of_stock_text=args.oos_text,
        confirmations_required=max(1, args.confirmations),
        cooldown_seconds=max(0, args.cooldown),
    )

    if args.once:
        return run_once(args, state, state_file, rule)

    log(f"启动循环监控, 间隔 {args.interval}s")
    while True:
        run_once(args, state, state_file, rule)
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    sys.exit(main())
