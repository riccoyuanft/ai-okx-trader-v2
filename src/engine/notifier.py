"""
Webhook notifier — supports DingTalk (钉钉), WeChat Work (企业微信), Feishu (飞书).
All send methods are fire-and-forget (errors are logged but not raised).
"""
import httpx
from loguru import logger


async def send_notification(provider: str, webhook_url: str, title: str, content: str) -> None:
    """
    Send a notification via the specified provider's webhook.

    Args:
        provider: 'dingtalk' | 'wecom' | 'feishu'
        webhook_url: full webhook URL
        title: short title
        content: message body (markdown supported for dingtalk/feishu)
    """
    if not webhook_url:
        return
    try:
        provider = (provider or "dingtalk").lower()
        if provider == "dingtalk":
            await _send_dingtalk(webhook_url, title, content)
        elif provider == "wecom":
            await _send_wecom(webhook_url, title, content)
        elif provider == "feishu":
            await _send_feishu(webhook_url, title, content)
        else:
            logger.warning(f"[Notifier] Unknown provider: {provider}")
    except Exception as e:
        logger.warning(f"[Notifier] Failed to send via {provider}: {e}")


async def _send_dingtalk(webhook_url: str, title: str, content: str) -> None:
    """DingTalk robot — markdown message."""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"## {title}\n\n{content}",
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=payload)
        data = resp.json()
        if data.get("errcode", 0) != 0:
            logger.warning(f"[Notifier][DingTalk] errcode={data.get('errcode')} msg={data.get('errmsg')}")


async def _send_wecom(webhook_url: str, title: str, content: str) -> None:
    """WeChat Work (企业微信) robot — markdown message."""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": f"**{title}**\n{content}",
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=payload)
        data = resp.json()
        if data.get("errcode", 0) != 0:
            logger.warning(f"[Notifier][WeCom] errcode={data.get('errcode')} msg={data.get('errmsg')}")


async def _send_feishu(webhook_url: str, title: str, content: str) -> None:
    """Feishu (飞书) robot — interactive card message."""
    payload = {
        "msg_type": "interactive",
        "card": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "content": content,
                        "tag": "lark_md",
                    },
                }
            ],
            "header": {
                "title": {
                    "content": title,
                    "tag": "plain_text",
                }
            },
        },
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=payload)
        data = resp.json()
        if data.get("code", 0) != 0:
            logger.warning(f"[Notifier][Feishu] code={data.get('code')} msg={data.get('msg')}")
