@echo off
chcp 65001 > nul
cd /d %~dp0

echo [InkStory Bot]

:: Проверяем Python
python --version > nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: Python не найден. Скачай с https://python.org
    pause
    exit /b 1
)

:: Создаём venv если нет
if not exist venv (
    echo Создаём виртуальное окружение...
    python -m venv venv
)

:: Активируем venv
call venv\Scripts\activate.bat

:: Устанавливаем зависимости
echo Проверяем зависимости...
pip install -r requirements.txt -q

:: Проверяем .env
if not exist .env (
    echo ОШИБКА: файл .env не найден!
    echo Скопируй .env.example в .env и заполни BOT_TOKEN
    pause
    exit /b 1
)

:: Инициализируем БД если нет
if not exist data\main.db (
    echo Инициализируем базу данных...
    python scripts\init_db.py
)

:: Запускаем бота
echo Запускаем бота...
python bot.py

pause
