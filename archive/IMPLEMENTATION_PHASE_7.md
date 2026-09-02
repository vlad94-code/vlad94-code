# Реализация 3 ключевых улучшений для Telegram-бота CNC Electric

## 📋 Обзор

Реализованы три критически важных компонента для улучшения работы бота:

1. **Middleware проверки подписки** - обязательная подписка на канал @cncelectric_russia
2. **Контроль загрузки файлов** - только администраторы и инженеры могут загружать файлы
3. **Vision Engine** - анализ изображений и скриншотов с OCR
4. **Excel Parser** - парсинг прайс-листов с извлечением MOQ и цен
5. **Advanced PDF Parser** - извлечение габаритных чертежей из каталогов

---

## 1️⃣ Middleware проверки подписки

### Файл: `middleware/subscription.py`

**Назначение:** Блокирует доступ к боту для пользователей, не подписанных на канал компании.

### Как работает:

```python
# В bot.py при инициализации:
from middleware import setup_middlewares

setup_middlewares(dp)  # dp = Dispatcher
```

### Логика работы:

1. При каждом сообщении проверяется статус подписки через Telegram API
2. Если пользователь не подписан:
   - Отправляется сообщение с кнопкой "Я подписался"
   - Блокируется обработка сообщения
3. После нажатия кнопки проверка повторяется

### Преимущества:

- ✅ Рост базы подписчиков канала
- ✅ Легальная практика для корпоративных ботов
- ✅ Автоматическая проверка без участия человека

### Требования:

- Бот должен быть добавлен в администраторы канала (хотя бы с правом чтения участников)

---

## 2️⃣ Контроль загрузки файлов

### Файл: `middleware/file_upload.py`

**Назначение:** Запрещает обычным пользователям загрузку файлов (фото, документы).

### Логика работы:

| Роль пользователя | Загрузка файлов |
|------------------|-----------------|
| ADMIN            | ✅ Разрешено    |
| ENGINEER         | ✅ Разрешено    |
| USER             | ❌ Запрещено    |

### Сообщение при попытке загрузки:

```
📷 Загрузка файлов ограничена

К сожалению, я не могу принять фотографию. 
Эта функция доступна только администраторам и инженерам.

💡 Пожалуйста, опишите вашу проблему текстом:
- Какой вопрос у вас возник?
- Какая информация нужна?
- Артикул или название оборудования?

Я постараюсь помочь вам на основе вашего описания!
```

### Интеграция:

```python
# В middleware/__init__.py
dp.message.middleware(FileUploadMiddleware())
```

---

## 3️⃣ Vision Engine - Анализ изображений

### Файл: `engines/vision.py`

**Назначение:** Распознавание текста на фотографиях и скриншотах, поиск артикулов.

### Возможности:

- 🔍 **OCR (Optical Character Recognition)** - распознавание русского и английского текста
- 🏷️ **Поиск артикулов** - автоматическое определение YCM3-63, DZ47-60, YCB9-80M и др.
- 📊 **Определение серии** - классификация оборудования по сериям
- 💡 **Рекомендации** - подсказки пользователю на основе анализа

### Поддерживаемые артикулы:

```python
patterns = [
    r'YCM[347]-\d+',           # YCM3-63, YCM4-125
    r'YC[BLW]\d{1,2}-\d+[A-Z]?',  # YCB9-80, YCW3-63
    r'DZ47-\d+',               # DZ47-60
    r'NCB1-\d+',               # NCB1-63
    r'NXB-\d+',                # NXB-63
]
```

### Пример использования:

```python
from engines.vision import get_vision_engine

vision = get_vision_engine()
result = await vision.analyze_image("photo.jpg", context="Какие размеры?")

print(result)
# {
#     "success": True,
#     "text_detected": ["YCB9-80M", "C 80A", "3P"],
#     "articles_found": ["YCB9-80M"],
#     "series_detected": ["YCB9"],
#     "description": "Обнаружена серия: YCB9\nНайдены артикулы: YCB9-80M",
#     "recommendations": ["🔍 Попробуйте найти информацию по артикулу: YCB9-80M"]
# }
```

### Установка зависимостей:

```bash
pip install pytesseract pillow
sudo apt-get install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
```

### Обработка фото из почты:

```python
# Для архивов писем с вложениями
result = await vision.process_photo_from_archive(
    photo_data=bytes,
    email_context={"subject": "Вопрос по YCM3", "from": "client@example.com"}
)
```

---

## 4️⃣ Excel Parser - Прайс-листы

### Файл: `parsers/excel_parser.py`

**Назначение:** Парсинг Excel-файлов с прайс-листами для извлечения цен и условий заказа.

### Извлекаемые данные:

| Поле | Описание | Пример |
|------|----------|--------|
| article | Артикул товара | YCM3-63 C16 |
| name | Наименование | Автоматический выключатель |
| price | Цена | 450.00 |
| moq | Минимальный заказ (MOQ) | 10 |
| pack_qty | Кратность упаковки | 5 |
| stock | Наличие на складе | 1500 |

### Авто-определение колонок:

Парсер автоматически находит колонки по ключевым словам:

```python
keywords = {
    'article': ['артикул', 'article', 'код', 'code', 'part number'],
    'name': ['наименование', 'name', 'описание', 'description'],
    'price': ['цена', 'price', 'cost', 'руб', 'rub'],
    'moq': ['min заказ', 'moq', 'мин заказ', 'minimum order'],
    'pack_qty': ['кратность', 'pack', 'упаковка', 'qty'],
    'stock': ['наличие', 'stock', 'остаток', 'available'],
}
```

### Пример использования:

```python
from parsers.excel_parser import get_excel_parser

parser = get_excel_parser()

# Парсинг файла
result = await parser.parse_file("uploads/price_3.8.xlsx")

# Поиск конкретного артикула
product = await parser.search_article("uploads/price_3.8.xlsx", "YCM3-63")

# Получение информации о ценах для списка артикулов
pricing = await parser.get_pricing_info(
    "uploads/price_3.8.xlsx",
    ["YCM3-63", "YCB9-80M", "DZ47-60"]
)
```

### Формат ответа:

```json
{
  "success": true,
  "file_name": "price_3.8.xlsx",
  "total_rows": 1250,
  "products": [
    {
      "article": "YCM3-63 C16",
      "name": "Автоматический выключатель 3P 16A",
      "price": 450.00,
      "moq": 10,
      "pack_qty": 5,
      "stock": 1500
    }
  ]
}
```

### Установка зависимостей:

```bash
pip install openpyxl xlrd
```

---

## 5️⃣ Advanced PDF Parser - Габаритные чертежи

### Файл: `parsers/pdf_parser.py`

**Назначение:** Парсинг PDF-каталогов с извлечением текста, таблиц и габаритных чертежей.

### Возможности:

- 📄 **Извлечение текста** с сохранением структуры страниц
- 📊 **Поиск таблиц** с техническими характеристиками
- 🖼️ **Извлечение чертежей** - конвертация страниц в изображения
- 🔍 **Поиск по артикулам** - быстрый поиск информации о товаре

### Пример сценария использования:

**Ситуация:** Проектировщик спрашивает: *"Габаритные размеры дифавтомата YCB9-80M"*

**Действия бота:**

1. Ищет в базе каталог модульного оборудования
2. Находит страницу с серией YCB9
3. Извлекает таблицу с размерами
4. Конвертирует страницу с чертежом в изображение
5. Отправляет пользователю:
   - Текстовое описание размеров
   - Изображение с габаритным чертежом

### Код:

```python
from parsers.pdf_parser import get_pdf_parser

parser = get_pdf_parser()

# Получение габаритной информации
dim_info = await parser.get_dimensional_info(
    "uploads/catalog_modular.pdf",
    series="YCB9"
)

print(dim_info)
# {
#   "series": "YCB9",
#   "articles_found": ["YCB9-80M", "YCB9-63"],
#   "drawings": [
#     {
#       "page": 15,
#       "image_path": "/tmp/drawing_page_15.png",
#       "articles": ["YCB9-80M"],
#       "width": 1200,
#       "height": 1600
#     }
#   ],
#   "dimensions": [
#     {
#       "length": 105,
#       "width": 72,
#       "height": 88,
#       "unit": "mm"
#     }
#   ]
# }
```

### Установка зависимостей:

```bash
pip install pdfplumber pdf2image
sudo apt-get install poppler-utils  # Требуется для pdf2image
```

---

## 🚀 Интеграция в бота

### Обновление bot.py:

```python
from aiogram import Dispatcher
from middleware import setup_middlewares
from engines.vision import get_vision_engine
from parsers.excel_parser import get_excel_parser
from parsers.pdf_parser import get_pdf_parser

# Создание диспетчера
dp = Dispatcher()

# Регистрация middleware
setup_middlewares(dp)

# Инициализация движков
vision_engine = get_vision_engine()
excel_parser = get_excel_parser()
pdf_parser = get_pdf_parser()

# Хендлер для команды /start
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_data = message.bot_context.get("user_data", {})
    role = user_data.get("role", "USER")
    
    if role in ["ADMIN", "ENGINEER"]:
        await message.answer(
            "👋 Здравствуйте! Вы можете загружать файлы:\n"
            "- Прайс-листы (.xlsx, .xls)\n"
            "- Каталоги (.pdf)\n"
            "- Фото оборудования"
        )
    else:
        await message.answer(
            "👋 Здравствуйте! Опишите ваш вопрос текстом.\n"
            "Для загрузки файлов обратитесь к администратору."
        )

# Хендлер для загрузки файлов (только ADMIN/ENGINEER)
@dp.message(F.document)
async def handle_document(message: Message):
    file_id = message.document.file_id
    file_name = message.document.file_name
    
    # Скачиваем файл
    file = await message.bot.get_file(file_id)
    file_path = f"uploads/{file_name}"
    await message.bot.download_file(file.file_path, file_path)
    
    # Определяем тип файла и парсим
    if file_name.endswith(('.xlsx', '.xls')):
        result = await excel_parser.parse_file(file_path)
        await message.answer(f"📊 Прайс обработан: {result['total_rows']} товаров")
    
    elif file_name.endswith('.pdf'):
        result = await pdf_parser.parse_catalog(file_path)
        await message.answer(
            f"📚 Каталог обработан:\n"
            f"- Страниц: {result['total_pages']}\n"
            f"- Найдено артикулов: {len(result['articles'])}\n"
            f"- Серии: {', '.join(result['series_found'])}"
        )

# Запуск бота
async def main():
    await dp.start_polling(bot)
```

---

## 📊 Сводная таблица возможностей

| Компонент | Обычный пользователь | Админ/Инженер |
|-----------|---------------------|---------------|
| Подписка на канал | ✅ Обязательна | ✅ Обязательна |
| Загрузка файлов | ❌ Запрещено | ✅ Разрешено |
| Текстовые вопросы | ✅ Работает | ✅ Работает |
| Анализ фото (OCR) | ⚠️ Через текст | ✅ Прямая загрузка |
| Поиск в прайсах | ✅ Через запрос | ✅ Загрузка файлов |
| Габаритные чертежи | ✅ Через запрос | ✅ Загрузка каталогов |

---

## 🎯 Рекомендации по использованию

### Для обычных пользователей:

1. Обязательно подпишитесь на @cncelectric_russia
2. Описывайте вопросы текстом максимально подробно
3. Указывайте артикулы или серии оборудования
4. Не пытайтесь загрузить фото - это не сработает

### Для администраторов и инженеров:

1. Загружайте актуальные прайс-листы через бота
2. Добавляйте новые каталоги в формате PDF
3. Проверяйте результаты парсинга через команду /review
4. Используйте Vision Engine для анализа фото от клиентов

### Для проектировщиков:

1. Спрашивайте габариты конкретно: *"габаритные размеры YCB9-80M"*
2. Бот найдёт чертёж в каталоге и отправит изображение
3. Если чертёж не найден - уточните серию оборудования

### Для менеджеров:

1. Загружайте прайс-лист "Прайс 3.8" через бота
2. Спрашивайте: *"минимальный заказ YCM3-63"*
3. Бот покажет MOQ, цену и кратность упаковки

---

## 📦 Зависимости

Добавьте в `requirements.txt`:

```txt
# Middleware
aiogram>=3.0.0

# OCR и Vision
pytesseract>=0.3.10
pillow>=10.0.0

# Excel Parser
openpyxl>=3.1.0
xlrd>=2.0.0

# PDF Parser
pdfplumber>=0.10.0
pdf2image>=1.16.0

# System dependencies (установить через apt):
# sudo apt-get install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
# sudo apt-get install poppler-utils
```

---

## ✅ Чек-лист готовности

- [x] Middleware подписки создан
- [x] Middleware контроля файлов создан
- [x] Vision Engine реализован
- [x] Excel Parser реализован
- [x] PDF Parser с чертежами реализован
- [ ] Интеграция в bot.py
- [ ] Тестирование на реальных данных
- [ ] Добавление команд для админов (/upload_price, /upload_catalog)
- [ ] Документация для пользователей

---

## 🔄 Следующие шаги

1. **Интегрировать middleware в bot.py**
2. **Добавить команды для загрузки файлов админами**
3. **Протестировать на реальном архиве писем**
4. **Настроить автообработку прайс-листов**
5. **Добавить кэширование результатов парсинга**
