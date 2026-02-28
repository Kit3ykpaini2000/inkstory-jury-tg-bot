#!/bin/bash

# Переходим в корень проекта (папка выше deploy/)
cd "$(dirname "$0")/.."

echo "[InkStory Bot]"

# Проверяем Python
if ! command -v python3 &> /dev/null; then
    echo "ОШИБКА: python3 не найден"
    echo "Установи: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# Создаём venv если нет
if [ ! -d "venv" ]; then
    echo "Создаём виртуальное окружение..."
    python3 -m venv venv
fi

# Активируем venv
source venv/bin/activate

# Зависимости
echo "Проверяем зависимости..."
pip install -r requirements.txt -q

# Проверяем .env
if [ ! -f ".env" ]; then
    echo "ОШИБКА: файл .env не найден!"
    echo "Скопируй .env.example в .env и заполни BOT_TOKEN"
    exit 1
fi

# Инициализируем БД если нет
if [ ! -f "data/main.db" ]; then
    echo "Инициализируем базу данных..."
    python scripts/init_db.py
fi

echo "Запускаем бота..."
python bot.py