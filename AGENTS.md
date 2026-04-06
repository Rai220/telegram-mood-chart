# Telegram Mood Chart -- инструкция для Claude Code

## Цель

Построить интерактивный HTML-график настроения человека на основе его экспорта Telegram (и, опционально, других мессенджеров). Результат -- файл `mood_chart.html` с двумя графиками: краткосрочные колебания и долгосрочный тренд.

## Пререквизиты

1. **Экспорт Telegram** -- файл `result.json` (Settings > Advanced > Export Telegram Data, формат JSON)
2. **Python 3.10+** с пакетами:
   ```
   pip install pandas plotly emoji
   ```
3. *Опционально* для лучшего качества сентимент-анализа:
   ```
   pip install transformers torch
   ```
   Без этого будет использован лексиконный анализатор (работает, но менее точен).

## Пошаговая инструкция

### Шаг 1: Подготовка данных

Пользователь должен положить свой `result.json` в эту директорию (или указать путь через `--json`).

### Шаг 2: Запуск анализа

```bash
python mood_analysis.py --json path/to/result.json
```

Скрипт автоматически:
- Определит user_id владельца экспорта (самый частый отправитель)
- Извлечет все текстовые сообщения
- Проведет сентимент-анализ (RuBERT если доступен, иначе лексиконный)
- Агрегирует по месяцам
- Построит интерактивный HTML-график
- Откроет его в браузере

### Шаг 3: Кастомизация вех

Для добавления персональных вех (важные события на таймлайне) отредактируйте список `MILESTONES` в `mood_analysis.py`:

```python
MILESTONES = [
    ('2020-03-11', 'COVID-19'),
    ('2022-09-01', 'Новая работа'),
    ('2023-06-15', 'Переезд'),
]
```

### Опции командной строки

| Флаг | Описание | По умолчанию |
|------|----------|--------------|
| `--json` | Путь к `result.json` | `DataExport/result.json` |
| `--user-id` | ID пользователя (`userXXXXX`) | автоопределение |
| `--window` | Окно агрегации: `month` или `week` | `month` |
| `--output` | Путь для HTML-файла | `mood_chart.html` |
| `--force` | Игнорировать кэш | нет |

## Как работает анализ

### Композитная оценка настроения

Итоговый показатель -- взвешенная комбинация 10 сигналов:

**Текстовые (55%):**

| Сигнал | Вес | Направление |
|--------|-----|-------------|
| Сентимент текста | 20% | Позитив ↑ = хорошо |
| Я-фокус (i_rate) | 10% | Больше «я» = вовлечён |
| Мы-фокус (we_rate) | 5% | Падение = изоляция |
| Ориентация на будущее | 5% | Падение = потеря мотивации |
| Тревога | -15% | Рост = плохо |

**Поведенческие (45%):**

| Сигнал | Вес | Направление |
|--------|-----|-------------|
| Вопросы | 20% | Падение = потеря любопытства |
| Соц. широта | 15% | Падение = замкнутость |
| Инициация | 10% | Падение = пассивность |

Каждый сигнал нормализуется (rolling z-score, окно 12 месяцев), комбинируется, и результат пропускается через `tanh` для приведения к диапазону [-1, 1]. Финальная кривая сглаживается скользящим средним (окно 3).

### Коррекция перспективы

Негативные сообщения о третьих лицах ("у него проблемы") получают сниженный вес (x0.3), т.к. обсуждение чужих проблем не означает плохое настроение автора.

### Тревога и стресс

- **Тревога** -- частота слов-маркеров тревоги (боюсь, тревожно, стресс, бессонница, ...)
- **Стресс** -- частота эмоционально-стрессового мата (в русском языке мат часто коррелирует со стрессом)

## Работа с несколькими источниками (SQLite)

Если пользователь хочет объединить данные из нескольких мессенджеров, помоги ему:

1. Написать `build_db.py` -- скрипт сборки единой SQLite базы
2. `mood_analysis.py` уже поддерживает чтение из базы (`--db messages.db`)

### Ожидаемая схема базы

```sql
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    is_key_person INTEGER DEFAULT 0
);

CREATE TABLE contact_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    source TEXT NOT NULL,           -- 'telegram_json', 'telegram_html', 'gchat', 'qip'
    source_user_id TEXT,            -- 'user35017383', 'email_xxx@gmail.com', 'qip_123456'
    source_name TEXT,               -- имя в этом источнике
    UNIQUE(source, source_user_id)
);

CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id),
    source TEXT NOT NULL,
    source_chat_id TEXT,
    type TEXT,                      -- 'personal_chat', 'saved_messages', 'group'
    name TEXT,
    UNIQUE(source, source_chat_id)
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    sender_contact_id INTEGER REFERENCES contacts(id),
    date TEXT NOT NULL,             -- ISO 8601
    date_unix INTEGER,
    text TEXT,
    msg_type TEXT,                  -- 'message', 'service'
    media_type TEXT,
    source TEXT NOT NULL,
    source_msg_id TEXT,
    UNIQUE(source, source_msg_id, chat_id)
);

CREATE INDEX idx_messages_chat ON messages(chat_id);
CREATE INDEX idx_messages_date ON messages(date);
CREATE INDEX idx_messages_sender ON messages(sender_contact_id);
CREATE INDEX idx_contact_aliases_source ON contact_aliases(source, source_user_id);

-- Полнотекстовый поиск
CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='messages', content_rowid='id');
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
```

### Архитектура build_db.py

`build_db.py` должен:

1. Создать БД по схеме выше
2. Импортировать каждый источник данных (см. форматы ниже)
3. Объединить контакты из разных источников
4. Удалить группы, ботов, Deleted Account
5. Дедуплицировать сообщения
6. Пересобрать FTS-индекс
7. Найти топ-20 контактов

Для объединения контактов нужен `ContactResolver` -- класс, который маппит `(source, source_user_id)` на единый `contact_id`. Автоматически мержит контакты с одинаковыми полными именами (2+ слова). После импорта всех источников пользователь подтверждает ручные мержи (один человек в Telegram, Google Chat и ICQ).

### Парсинг Telegram JSON (result.json)

Основной и самый полный формат. Структура:

```json
{
  "personal_information": {"user_id": 12345678, ...},
  "chats": {
    "list": [
      {
        "id": 12345678,
        "type": "personal_chat",       // или "saved_messages", "private_group", ...
        "name": "Имя Контакта",
        "messages": [
          {
            "id": 1,
            "type": "message",          // или "service"
            "date": "2020-03-15T10:30:00",
            "date_unixtime": "1584264600",
            "from": "Имя Отправителя",
            "from_id": "user12345678",
            "text": "Привет!"           // строка или массив (см. ниже)
          }
        ]
      }
    ]
  }
}
```

**Важно про поле `text`**: это может быть строка `"Привет"` или массив из строк и объектов (для форматированного текста):
```json
"text": [
  "Вот ссылка: ",
  {"type": "link", "text": "https://example.com"},
  " -- посмотри"
]
```
Нужна функция `flatten_text()` для извлечения чистого текста.

**Определение "своих" сообщений**: user_id владельца -- в `personal_information.user_id`. Его `from_id` = `"user{id}"`. Контакт (собеседник) -- другой `from_id` в том же чате.

**Фильтрация**: импортировать только `type == "personal_chat"` (и `"saved_messages"` если нужно). Группы, каналы, боты -- пропускать.

### Парсинг Telegram HTML (GDPR-экспорт)

Получается через [privacy.telegram.org](https://privacy.telegram.org). Структура:

```
DataExport/
  chats/
    chat_001/
      messages.html       # первая страница
      messages2.html      # вторая страница
      messages3.html
    chat_002/
      messages.html
  lists/
    chats.html            # индекс с типами чатов (personal/group/supergroup)
```

**HTML-разметка сообщений** (BeautifulSoup):

```html
<div class="message" id="message123">
  <div class="from_name">Имя Отправителя</div>
  <div class="text">Текст сообщения</div>
  <div class="date details" title="15.03.2020 10:30:00 UTC+03:00">15:30</div>
</div>
```

- Имя отправителя: `.from_name` (есть не у каждого сообщения -- если отсутствует, наследуется от предыдущего)
- Дата: аттрибут `title` у `.date.details`, формат `DD.MM.YYYY HH:MM:SS` (может быть с `UTC+HH:MM`)
- Текст: `.text` (заменить `<br>` на `\n` перед извлечением)
- Сервисные сообщения: `class="message service"`
- Пагинация: `messages.html`, `messages2.html`, ..., сортировать по числу

**Определение типа чата** (группа или личный): парсить `lists/chats.html`, искать `.pull_right.info.details` с текстом "group"/"supergroup".

### Парсинг Google Chat (Google Takeout)

Структура после распаковки Google Takeout:

```
Google Chat/
  Groups/
    DM_abc123def/
      group_info.json
      messages.json
    DM_xyz789/
      ...
```

**group_info.json**:
```json
{
  "members": [
    {"name": "Иван Иванов", "email": "ivan@gmail.com"},
    {"name": "Пётр Петров", "email": "petr@gmail.com"}
  ]
}
```

**messages.json**:
```json
{
  "messages": [
    {
      "creator": {"name": "Иван Иванов", "email": "ivan@gmail.com"},
      "created_date": "Friday, November 29, 2013 at 10:39:39 AM UTC",
      "text": "Привет!",
      "message_id": "abc123"
    }
  ]
}
```

**Логика**:
- Если `members` содержит >2 человек (не считая владельца) -- это группа, пропускать
- Если 0 (не считая владельца) -- self-chat, пропускать
- Личный чат: ровно 1 другой участник
- Дата: формат `"Friday, November 29, 2013 at 10:39:39 AM UTC"` -- убрать день недели, " at ", " UTC", парсить как `"%B %d, %Y %I:%M:%S %p"`
- Идентификация отправителя: по `creator.email` (совпадает с email владельца -> это "я")
- Контакт в `contact_aliases`: `source_user_id = "email_{email}"`

### Парсинг QIP Infium (.qhf)

Бинарный формат. **Используй готовый парсер `parse_qhf.py`** из этого проекта. Он содержит:
- `parse_qhf(filepath)` -> `(uin, nick, messages)` где каждое сообщение = `{id, time, incoming, text, timestamp}`
- `decode_qhf_text()` -- дешифровка текста (позиционный шифр)

```python
from parse_qhf import parse_qhf

result = parse_qhf("path/to/contact.qhf")
if result:
    uin, nick, messages = result
    for msg in messages:
        print(msg['time'], '←' if msg['incoming'] else '→', msg['text'][:50])
```

Структура данных QIP:
```
QIP/
  Profiles/
    my_uin/            # UIN владельца (например "123456" или "my_login")
      History/
        123456.qhf     # история с контактом UIN=123456
        user@jabber.qhf
```

**Логика**:
- Каждый .qhf файл -- переписка с одним контактом
- `uin` из файла -- идентификатор контакта
- `incoming=True` -- сообщение ОТ контакта, `incoming=False` -- от владельца
- Имя профиля (папка `Profiles/<name>`) -- UIN или Jabber-логин владельца
- Контакт в `contact_aliases`: `source_user_id = "qip_{uin}"`
- Пропускать служебные файлы: `_qip_svc.qhf`, `_qip_bot.qhf`

### Парсинг WhatsApp (TXT)

Формат экспорта из WhatsApp:

```
01.03.2020, 10:30 - Иван Иванов: Привет!
01.03.2020, 10:31 - Пётр Петров: Здравствуй
01.03.2020, 10:32 - Иван Иванов: Как дела?
Нормально, спасибо           <-- продолжение предыдущего (нет даты)
```

Парсить регуляркой: `^(\d{2}\.\d{2}\.\d{4}), (\d{2}:\d{2}) - (.+?): (.*)$`
Строки без даты -- продолжение предыдущего сообщения.

### Принципы сборки

- **Объединение контактов**: один человек в разных мессенджерах объединяется в одну запись через `contact_aliases`. Автоматически мержатся контакты с полным совпадением полного имени (2+ слова). Для остального -- спроси у пользователя.
- **Дедупликация**: если одно и то же сообщение (date ISO строка + text[:200]) встречается в нескольких источниках, оставить одну копию. **Важно**: матчить по `date` (ISO строка), а не по `date_unix` — у многих сообщений `date_unix` равен NULL. Приоритет: свежий JSON > старый JSON > Google Chat > HTML > QIP.
- **Только личные чаты**: группы и боты удаляются
- **FTS5**: полнотекстовый поиск по сообщениям
- **Порядок импорта**: сначала самый полный источник (обычно последний JSON-экспорт), потом остальные. Так дедупликация работает эффективнее.

## Пример результата

Файл `example_mood_chart.html` -- реальный пример графика за 17+ лет переписки (2008-2026). Откройте в браузере для интерактивного просмотра.

## Возможные проблемы

- **Мало данных** -- для качественного графика нужно минимум 6-12 месяцев активной переписки
- **RuBERT долго грузится** -- первый запуск скачивает модель (~500MB). Дальше она кэшируется
- **plotly.min.js** -- `include_plotlyjs='directory'` создает файл `plotly.min.js` рядом с HTML. Это нормально
- **Кэш** -- после первого анализа создается `mood_cache.pkl`. При изменении данных используйте `--force`

## Адаптация под другой язык

Скрипт заточен под русский язык. Для адаптации:
1. Заменить словари `POSITIVE_WORDS`, `NEGATIVE_WORDS`, `ANXIETY_WORDS`, `STRESS_PROFANITY`
2. Заменить регулярки местоимений `_RE_FIRST_PERSON`, `_RE_THIRD_PERSON`, `_RE_I_FOCUS`, `_RE_WE_FOCUS`
3. Выбрать подходящую модель вместо `blanchefort/rubert-base-cased-sentiment-rusentiment`
