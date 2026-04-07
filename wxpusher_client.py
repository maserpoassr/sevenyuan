from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List

import requests


class WxPusherError(Exception):
    pass


@dataclass
class WxPusherClient:
    app_token: str
    uids: List[str]
    topic_ids: List[int]
    timeout: int = 10
    base_url: str = "https://wxpusher.zjiecode.com/api/send/message"

    def _build_payload(self, title: str, content: str, content_type: int = 3) -> Dict[str, Any]:
        if not self.app_token:
            raise WxPusherError("WxPusher app_token 为空")
        if not self.uids and not self.topic_ids:
            raise WxPusherError("WxPusher 至少需要 uids 或 topic_ids")

        payload: Dict[str, Any] = {
            "appToken": self.app_token,
            "summary": title,
            "content": content,
            "contentType": content_type,
            "verifyPayType": 0,
        }
        if self.uids:
            payload["uids"] = self.uids
        if self.topic_ids:
            payload["topicIds"] = self.topic_ids
        return payload

    def send(
        self,
        title: str,
        content: str,
        content_type: int = 3,
        retries: int = 3,
        retry_interval: float = 2.0,
    ) -> Dict[str, Any]:
        payload = self._build_payload(title=title, content=content, content_type=content_type)
        max_retries = max(1, retries)
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(self.base_url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") == 1000:
                    return data
                raise WxPusherError(f"WxPusher 返回失败: {data}")
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(retry_interval * attempt)

        raise WxPusherError(f"WxPusher 重试后仍失败: {last_error}")
