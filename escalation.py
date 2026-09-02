"""Вопрос клиента, на который бот не смог ответить сам.

Два пути одновременно (спека §3.3): письмо на help@ — письменный след,
уведомление инженеру в Telegram — оттуда ответ автоматически возвращается
клиенту. Срок один и тот же: до 3 рабочих дней.
"""
from __future__ import annotations

import logging

import mailer
from core.logging_ import (
    answer_escalation, get_escalation, record_escalation, set_escalation_mail_status,
)
from core.roles import ENGINEER_IDS

logger = logging.getLogger(__name__)

ANSWER_PREFIX = "Ответ технической службы CNC"

RECEIPT = (
    "Вопрос №{number} передан технической службе CNC.\n"
    "Отвечают в течение 3 рабочих дней — ответ придёт сюда же, в этот чат."
)


def client_receipt(number: int) -> str:
    return RECEIPT.format(number=number)


def format_notification(row: dict) -> str:
    lines = [f"🔔 Вопрос №{row['id']}"]
    if row.get("region"):
        lines[0] += f", клиент из {row['region']}"
    lines.append(f"«{row['question']}»")
    if row.get("context"):
        lines.append(f"Смотрел перед этим: {row['context']}")
    if row.get("email"):
        lines.append(f"Почта клиента: {row['email']}")
    lines.append("")
    lines.append("Ответьте на это сообщение — бот передаст ответ клиенту.")
    return "\n".join(lines)


def _letter(row: dict) -> tuple[str, str]:
    subject = f"Вопрос №{row['id']} от клиента из бота CNC"
    body = [row["question"], ""]
    if row.get("context"):
        body.append(f"Смотрел в боте: {row['context']}")
    if row.get("region"):
        body.append(f"Регион: {row['region']}")
    if row.get("email"):
        body.append(f"Почта клиента: {row['email']}")
    body += ["", f"Ответить можно в Telegram-боте — ответом на уведомление о вопросе №{row['id']}."]
    return subject, "\n".join(body)


async def register(bot, *, question: str, user_id: int, chat_id: int,
                   context: str | None = None, region: str | None = None,
                   email: str | None = None) -> int:
    number = record_escalation(question, user_id=user_id, chat_id=chat_id,
                               context=context, region=region, email=email)
    row = get_escalation(number)

    subject, body = _letter(row)
    set_escalation_mail_status(number, "sent" if mailer.send(subject, body) else "failed")

    text = format_notification(row)
    for engineer_id in ENGINEER_IDS:
        try:
            await bot.send_message(engineer_id, text)
        except Exception:
            logger.exception("Не удалось уведомить инженера %s о вопросе №%s", engineer_id, number)
    return number


async def deliver(bot, escalation_id: int, answer: str, *, answered_by: int) -> bool:
    """Отдать ответ инженера клиенту. Второй раз на тот же вопрос — отказ."""
    row = get_escalation(escalation_id)
    if row is None:
        return False
    if not answer_escalation(escalation_id, answer, answered_by=answered_by):
        return False
    await bot.send_message(
        row["chat_id"],
        f"{ANSWER_PREFIX} на ваш вопрос №{escalation_id}:\n\n{answer}",
    )
    return True
