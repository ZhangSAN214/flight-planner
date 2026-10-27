"""提醒：邮件和 Telegram，哪个配置了就用哪个。"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from ..config import Settings

log = logging.getLogger(__name__)


def channels(settings: Settings) -> list[str]:
    result = []
    if settings.smtp_host and settings.alert_email_to:
        result.append("email")
    if settings.telegram_bot_token and settings.telegram_chat_id:
        result.append("telegram")
    return result


def _send_email(settings: Settings, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user or settings.alert_email_to
    msg["To"] = settings.alert_email_to
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


async def notify(settings: Settings, subject: str, body: str) -> list[str]:
    """发送提醒，返回成功的渠道。没有配置任何渠道时只写日志。"""
    sent = []
    if "email" in channels(settings):
        try:
            await asyncio.to_thread(_send_email, settings, subject, body)
            sent.append("email")
        except (OSError, smtplib.SMTPException) as e:
            log.warning("邮件发送失败：%s", e)
    if "telegram" in channels(settings):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": settings.telegram_chat_id, "text": f"{subject}\n\n{body}", "disable_web_page_preview": True},
                )
                resp.raise_for_status()
            sent.append("telegram")
        except httpx.HTTPError as e:
            log.warning("Telegram 发送失败：%s", e)
    if not sent:
        log.info("提醒（未配置发送渠道）：%s\n%s", subject, body)
    return sent
