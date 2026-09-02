"""Отправка писем в техническую службу.

Тонкий слой над smtplib и ничего больше: логика эскалации живёт в
escalation.py. Так тест эскалации не открывает сетевых соединений, а смена
транспорта (ARCHITECTURE §2.6) не задевает логику.

Домен cncrussia.com обслуживается Mail.ru для бизнеса: smtp.mail.ru:465,
SSL, «пароль для внешнего приложения» — не пароль входа.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_HOST = "smtp.mail.ru"
DEFAULT_PORT = 465
TIMEOUT = 20


def _setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def support_email() -> str:
    return _setting("SUPPORT_EMAIL", "help@cncrussia.com")


def configured() -> bool:
    """Есть ли чем отправлять. Отсутствие настроек — не авария: эскалация
    всё равно доходит до инженера в Telegram (спека §9)."""
    return bool(_setting("SMTP_USER") and _setting("SMTP_PASSWORD"))


def send(subject: str, body: str, *, to: str | None = None,
         attachments: list[tuple[str, bytes]] | None = None) -> bool:
    if not configured():
        logger.warning("SMTP не настроен — письмо %r не отправлено", subject)
        return False

    message = EmailMessage()
    message["From"] = _setting("SMTP_USER")
    message["To"] = to or support_email()
    message["Subject"] = subject
    message.set_content(body)
    for filename, payload in attachments or []:
        guessed, _ = mimetypes.guess_type(filename)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)

    host = _setting("SMTP_HOST", DEFAULT_HOST)
    port = int(_setting("SMTP_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=TIMEOUT) as smtp:
            smtp.login(_setting("SMTP_USER"), _setting("SMTP_PASSWORD"))
            smtp.send_message(message)
    except Exception:
        logger.exception("Не удалось отправить письмо %r", subject)
        return False
    return True
