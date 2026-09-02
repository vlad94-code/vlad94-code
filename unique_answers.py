"""Справочник уникальных ответов, который ведёт инженер вручную.

Источник правды — `knowledge/unique_answers.md` в репозитории: обычный
Markdown, который правится в любом редакторе и виден в git diff. Не docx:
разбор Word-стилей ломается от любого переформатирования, а правки в
бинарном файле нельзя прочитать в истории.

Формат источника (нарочно минимальный):

    ## Категория
    ### Вопрос?
    Контекст: необязательная строка с уточнением.
    Ответ, сколько угодно строк подряд.

Дальше — ровно тот же путь, что у загруженного документа и у одобренной
очереди ответов ИИ (verified_answers_queue.py): рендер в блоки
"## Страница N" (catalog_parser.render_verified_answers) → регистрация
версии (core.documents) → knowledge_matrix.rebuild(). Отдельного конвейера
поиска у справочника нет.

Использование после правки файла:

    python unique_answers.py
"""
from __future__ import annotations

import re
from pathlib import Path

from catalog_parser import VerifiedAnswer, render_verified_answers
from core.documents import allocate_slot, register_document

SOURCE_PATH = Path("knowledge") / "unique_answers.md"
UPLOAD_DIR = Path("uploads")

# Фиксированное логическое имя, а не дата в имени: allocate_slot() считает
# версии по original_name, и только при неизменном имени register_document()
# пометит предыдущую версию superseded (тот же приём, что у
# verified_answers_queue.LOGICAL_NAME и pricelist_store.LOGICAL_NAME).
LOGICAL_NAME = "Справочник_CNC.md"

# Авторство должно оставаться честным: справочник собран и проверен
# инженером CNC, а не прислан технической службой одним документом
# (см. catalog_parser.render_verified_answers и ARCHITECTURE.md §5).
ANSWER_PREFIX = "Справочник CNC (подтверждено инженером):"

_CONTEXT_MARKER = "Контекст:"


def parse_source(text: str) -> list[VerifiedAnswer]:
    """Разобрать Markdown справочника в записи «вопрос → ответ».

    Вопрос без ответа пропускается: заготовка — это ещё не знание, а
    проиндексированная пустая запись нашлась бы поиском по тексту вопроса
    и показала пользователю пустоту (тот же мотив, что у
    catalog_parser._is_placeholder_answer).
    """
    entries: list[VerifiedAnswer] = []
    category = ""
    question: str | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal question, body
        if question:
            lines = [line for line in body if line.strip()]
            context = ""
            if lines and lines[0].startswith(_CONTEXT_MARKER):
                context = lines.pop(0).strip()
            answer = "\n".join(line.strip() for line in lines).strip()
            if answer:
                entries.append(VerifiedAnswer(
                    number=len(entries) + 1,
                    category=category,
                    question=question,
                    context=context,
                    answer=answer,
                ))
        question = None
        body = []

    for line in text.splitlines():
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            if level == 3:
                flush()
                question = title
            elif level == 2:
                flush()
                category = title
            else:  # "# " — заголовок файла, не часть записи
                flush()
            continue
        if question is not None:
            body.append(line)
    flush()
    return entries


CLIENT_CATEGORY = "Вопросы клиентов"


def append_entry(question: str, answer: str, category: str = CLIENT_CATEGORY) -> bool:
    """Дописать в справочник ответ, который инженер только что дал клиенту.

    Так справочник растёт из реальных вопросов, без отдельной работы
    куратора (ARCHITECTURE §0 п. 3). Повтор не дописывается: один и тот же
    вопрос с двумя ответами сделал бы поиск недетерминированным.
    """
    question = " ".join(question.split()).rstrip("?") + "?"
    answer = answer.strip()
    if not answer:
        return False

    path = Path(SOURCE_PATH)
    body = path.read_text(encoding="utf-8") if path.exists() else "# Уникальные ответы CNC\n"
    if f"### {question}" in body:
        return False

    if f"## {category}" not in body:
        body = body.rstrip("\n") + f"\n\n## {category}\n"
    body = body.rstrip("\n") + f"\n\n### {question}\n{answer}\n"
    path.write_text(body, encoding="utf-8")
    return True


def rebuild_unique_answers_document() -> int:
    """Пересобрать документ базы знаний из справочника. Возвращает число записей.

    Вызывающий отвечает за knowledge_matrix.rebuild() после этого — так же,
    как bot.py делает после upload_document(), а main() ниже после этой
    функции.
    """
    if not SOURCE_PATH.exists():
        return 0
    entries = parse_source(SOURCE_PATH.read_text(encoding="utf-8"))
    if not entries:
        return 0

    content = "\n".join(
        [f"# {LOGICAL_NAME}", ""] + render_verified_answers(entries, answer_prefix=ANSWER_PREFIX)
    )

    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    slot = allocate_slot(LOGICAL_NAME)
    path = UPLOAD_DIR / slot.stored_name
    path.write_text(content, encoding="utf-8")
    # Исходника отдельно нет: сгенерированный markdown и есть документ,
    # поэтому он же и "parsed" — register_document() читает path только
    # ради чек-суммы и расширения.
    register_document(slot, path, parsed_path=path)
    return len(entries)


def main() -> None:
    from knowledge_matrix import rebuild

    count = rebuild_unique_answers_document()
    if not count:
        print(f"В «{SOURCE_PATH}» нет ни одной записи с ответом — документ не пересобран.")
        return
    pages, records = rebuild()
    print(f"Справочник пересобран: записей — {count}.")
    print(f"Матрица знаний перестроена: страниц каталогов — {pages}, записей API — {records}.")


if __name__ == "__main__":
    main()
