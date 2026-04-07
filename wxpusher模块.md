# WxPusher 通用模块

下面是一个可复用的 `WxPusher` 模块实现，包含完整注释，便于直接复制到其他项目中使用。

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


class WxPusherError(Exception):
    """WxPusher 发送失败时抛出的统一异常。"""


@dataclass
class WxPusherClient:
    """
    WxPusher 客户端。

    参数说明:
    - app_token: WxPusher 应用 Token (AT_xxx)
    - uid: 目标用户 UID (UID_xxx)
    - timeout: 单次请求超时时间(秒)
    - base_url: WxPusher 发送接口地址
    """

    app_token: str
    uid: str
    timeout: int = 10
    base_url: str = "https://wxpusher.zjiecode.com/api/send/message"

    def _build_payload(
        self,
        title: str,
        content: str,
        content_type: int = 3,
        verify_pay: bool = False,
    ) -> Dict[str, Any]:
        """
        组装请求体。

        content_type 常用值:
        - 2: HTML
        - 3: Markdown
        """
        if not self.app_token or not self.uid:
            raise WxPusherError("WxPusher 配置缺失: app_token 或 uid 为空")

        return {
            "appToken": self.app_token,
            "summary": title,
            "content": content,
            "contentType": content_type,
            "uids": [self.uid],
            "verifyPay": verify_pay,
        }

    def send(
        self,
        title: str,
        content: str,
        content_type: int = 3,
        retries: int = 3,
        retry_interval: float = 2.0,
    ) -> Dict[str, Any]:
        """
        同步发送消息。

        重试策略:
        - 失败后按 retry_interval * attempt 递增等待
        - WxPusher 返回 code == 1000 视为成功
        """
        payload = self._build_payload(title, content, content_type)
        max_retries = max(1, retries)
        last_err: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(self.base_url, json=payload, timeout=self.timeout)
                data = resp.json()
                if data.get("code") == 1000:
                    return data
                raise WxPusherError(f"WxPusher 返回失败: {data.get('msg')}")
            except Exception as e:  # 捕获网络错误/解析错误/业务错误
                last_err = e
                if attempt < max_retries:
                    import time

                    time.sleep(retry_interval * attempt)

        raise WxPusherError(f"WxPusher 重试后仍失败: {last_err}")

    async def send_async(
        self,
        title: str,
        content: str,
        content_type: int = 3,
        retries: int = 3,
        retry_interval: float = 2.0,
        request_client=requests,
    ) -> Dict[str, Any]:
        """
        异步风格发送消息。

        说明:
        - 当前实现使用 requests 作为底层客户端，并通过 asyncio.sleep 做异步等待。
        - 若你使用 aiohttp/httpx(async)，可在此处替换 request_client 的发送逻辑。
        """
        payload = self._build_payload(title, content, content_type)
        max_retries = max(1, retries)
        last_err: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = request_client.post(self.base_url, json=payload, timeout=self.timeout)
                data = resp.json()
                if data.get("code") == 1000:
                    return data
                raise WxPusherError(f"WxPusher 返回失败: {data.get('msg')}")
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    await asyncio.sleep(retry_interval * attempt)

        raise WxPusherError(f"WxPusher 重试后仍失败: {last_err}")
```

## 使用示例

```python
client = WxPusherClient(app_token="AT_xxx", uid="UID_xxx")
client.send(title="测试通知", content="# Hello\n\n这是一条测试消息", content_type=3)
```
