# InkStory Bot

Telegram-бот для организации литературного конкурса на [inkstory.net](https://inkstory.net).  
Жюри проверяют посты участников — считают слова и ошибки. Бот автоматически собирает посты, распределяет их между жюри и ведёт статистику.

---

## Возможности

- **Два режима очереди** — общая (первый пришёл — первый взял) или распределение по наименьшей загрузке
- **Автопарсер** — собирает новые посты по расписанию и уведомляет жюри
- **AI-проверка** — отправка текста в Groq API для поиска ошибок
- **Просроченные посты** — если жюри не проверил за 30 минут, пост автоматически освобождается
- **Админ-панель** — статистика, очереди, верификация жюри, смена режима, логи
- **Экспорт в Excel** — итоги конкурса по дням с рейтингом участников
- **CLI** — консольное управление БД

---

## Установка

```bash
# 1. Клонируй репозиторий
git clone https://github.com/your/inkstory-bot.git
cd inkstory-bot

# 2. Создай виртуальное окружение
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Установи зависимости
pip install -r requirements.txt

# 4. Создай .env из примера
cp .env.example .env
# Заполни BOT_TOKEN и GROQ_API_KEY

# 5. Инициализируй БД
python scripts/init_db.py

# 6. Запусти бота
python bot.py
```

---

## Миграция со старой версии

```bash
# Создай новую пустую БД
python scripts/init_db.py --path data/new.db

# Перенеси данные
python scripts/migrate.py --old data/old.db --new data/new.db

# Замени старую БД
mv data/new.db data/main.db
```

---

## Режимы очереди

Переключаются через админ-панель бота (`/admin → Режим очереди`) или напрямую в БД:

| Режим | Описание |
|---|---|
| `distributed` | Пост сразу назначается жюри с наименьшей очередью |
| `open` | Все посты в общей очереди, первый кто нажмёт /next — тот и берёт |

В обоих режимах: если жюри взял пост но не проверил за `expire_minutes` минут — пост освобождается.

---

## Структура проекта

```
├── bot.py               — запуск бота
├── main.py              — запуск парсера вручную
├── bot/
│   ├── handlers/
│   │   ├── user.py      — команды жюри (/next, /register, /stats...)
│   │   └── admin.py     — команды администратора (/admin)
│   └── keyboards.py     — inline клавиатуры
├── parser/
│   ├── links.py         — сбор ссылок через API
│   ├── posts.py         — парсинг постов
│   └── queue_manager.py — управление очередью
├── utils/
│   ├── database.py      — подключение к БД, config хелперы
│   ├── logger.py        — логгер
│   ├── word_counter.py  — подсчёт слов
│   └── ai_utils.py      — проверка через Groq API
├── scripts/
│   ├── init_db.py       — инициализация БД
│   ├── migrate.py       — миграция со старой схемы
│   └── export_results.py — экспорт в Excel
├── tests/               — тесты
├── data/                — БД (в .gitignore)
├── logs/                — логи (в .gitignore)
└── results/             — экспорты Excel (в .gitignore)
```

---

## Схема БД

| Таблица | Описание |
|---|---|
| `reviewers` | Жюри (TGID, верификация, права админа) |
| `authors` | Авторы постов |
| `days` | Дни конкурса |
| `links` | Ссылки на посты (Parsed 0/1) |
| `blacklist` | Заблокированные ссылки |
| `posts_info` | Посты со статусом (pending/checking/done/rejected/reviewer_post) |
| `queue` | Очередь — кто за каким постом, когда взял |
| `results` | Результаты проверки (слова, ошибки, reviewer) |
| `config` | Настройки (queue_mode, expire_minutes) |

---

## Тесты

```bash
python tests/test_word_counter.py
python tests/test_database.py
python tests/test_queue_manager.py
```

---

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram бота от @BotFather |
| `PARSER_INTERVAL` | Интервал автопарсера в минутах (по умолчанию: 30) |
| `GROQ_API_KEY` | API-ключ Groq для AI-проверки (console.groq.com) |
