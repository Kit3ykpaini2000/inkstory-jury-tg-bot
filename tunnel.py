"""
tunnel.py — управление туннелем (cloudflare / xtunnel)

Запускает туннель, получает публичный URL и обновляет кнопку Mini App в Telegram.
Поддерживает повторные попытки и перезапуск без перезапуска всего бота.

Поддерживаемые провайдеры:
  cloudflare — cloudflared (trycloudflare.com), установка: see README
  xtunnel    — xtunnel http, установка: see README

Использование:
    from tunnel import TunnelManager
    mgr = TunnelManager(bot_token=BOT_TOKEN, provider="xtunnel")
    url = mgr.start()          # запустить / перезапустить
    mgr.update_menu_button()   # обновить кнопку у всех пользователей

В .env:
    TUNNEL_PROVIDER=xtunnel   # или cloudflare
"""

import re
import subprocess
import threading
import time
from typing import Callable

import requests

from utils.logger import setup_logger

log = setup_logger()

# Паттерны URL для каждого провайдера
_URL_PATTERNS = {
    "cloudflare": re.compile(r"https://[\w\-]+\.trycloudflare\.com"),
    "xtunnel":    re.compile(r"https://[\w\-]+\.[\w\-]+\.[\w]+"),
}

_MENU_BUTTON_TEXT = "Открыть панель"


class TunnelManager:
    def __init__(
        self,
        bot_token: str,
        provider: str = "cloudflare",
        port: int = 8000,
        timeout: int = 30,
        retries: int = 3,
        retry_delay: int = 10,
        get_tg_ids: Callable[[], list[str]] | None = None,
    ):
        self.bot_token   = bot_token
        self.provider    = provider
        self.port        = port
        self.timeout     = timeout
        self.retries     = retries
        self.retry_delay = retry_delay
        self.get_tg_ids  = get_tg_ids

        self.url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ── Запуск ────────────────────────────────────────────────────────────────

    def start(self) -> str | None:
        """Запускает туннель с повторными попытками. Возвращает URL или None."""
        with self._lock:
            for attempt in range(1, self.retries + 1):
                log.info(f"[tunnel] Попытка {attempt}/{self.retries} ({self.provider})...")
                url = self._launch()
                if url:
                    self.url = url
                    log.info(f"[tunnel] URL получен: {url}")
                    return url
                if attempt < self.retries:
                    log.warning(f"[tunnel] Нет URL, повтор через {self.retry_delay} сек...")
                    time.sleep(self.retry_delay)

            log.error(f"[tunnel] Все {self.retries} попытки исчерпаны.")
            return None

    def _launch(self) -> str | None:
        """Убивает старый процесс и запускает новый. Возвращает URL или None."""
        self._kill()
        if self.provider == "xtunnel":
            return self._run(["xtunnel", "http", str(self.port)], "stdout")
        else:  # cloudflare
            return self._run(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{self.port}"],
                "stderr",
            )

    def _run(self, cmd: list[str], url_stream: str) -> str | None:
        """Запускает процесс и ждёт URL в указанном потоке вывода."""
        pattern = _URL_PATTERNS.get(self.provider, _URL_PATTERNS["cloudflare"])
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if url_stream == "stderr" else subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            log.error(f"[tunnel] Команда не найдена: {cmd[0]}")
            return None
        except Exception as e:
            log.error(f"[tunnel] Ошибка запуска: {e}")
            return None

        stream = proc.stderr if url_stream == "stderr" else proc.stdout
        deadline = time.time() + self.timeout
        url = None

        while time.time() < deadline:
            line = stream.readline()
            if not line:
                time.sleep(0.1)
                continue
            log.debug(f"[tunnel] {line.rstrip()}")
            match = pattern.search(line)
            if match:
                url = match.group(0)
                break

        if url:
            self._proc = proc
            self._pipe_remaining(proc, stream)
        else:
            log.warning(f"[tunnel] URL не получен за {self.timeout} сек")
            proc.terminate()

        return url

    def _pipe_remaining(self, proc: subprocess.Popen, stream) -> None:
        """Читает оставшийся вывод процесса в фоне."""
        def _read():
            for line in stream:
                if line.strip():
                    log.debug(f"[tunnel] {line.rstrip()}")
            proc.wait()

        threading.Thread(target=_read, daemon=True, name=f"tunnel-{self.provider}").start()

    def _kill(self) -> None:
        """Завершает текущий процесс туннеля если он запущен."""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
            log.info("[tunnel] Старый процесс завершён")
        except Exception:
            pass
        self._proc = None

    # ── Telegram Menu Button ──────────────────────────────────────────────────

    def update_menu_button(self, tg_ids: list[str] | None = None) -> bool:
        """
        Обновляет кнопку Mini App в Telegram для всех пользователей.
        Если tg_ids не передан — запрашивает через get_tg_ids().
        Возвращает True если глобальная кнопка обновлена успешно.
        """
        if not self.url:
            log.warning("[tunnel] URL неизвестен — кнопку обновить нельзя")
            return False

        menu_button = {
            "type":    "web_app",
            "text":    _MENU_BUTTON_TEXT,
            "web_app": {"url": self.url},
        }

        ok = self._set_menu_button(None, menu_button)

        ids = tg_ids or (self.get_tg_ids() if self.get_tg_ids else [])
        for tg_id in ids:
            self._set_menu_button(tg_id, menu_button)

        log.info(f"[tunnel] Кнопка обновлена для {len(ids)} пользователей: {self.url}")
        return ok

    def _set_menu_button(self, chat_id: str | None, menu_button: dict) -> bool:
        payload: dict = {"menu_button": menu_button}
        if chat_id:
            payload["chat_id"] = chat_id
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/setChatMenuButton",
                json=payload,
                timeout=5,
            )
            return bool(resp.json().get("ok"))
        except Exception as e:
            log.warning(f"[tunnel] setChatMenuButton ошибка (chat_id={chat_id}): {e}")
            return False

    def set_menu_button_for(self, tg_id: str) -> bool:
        """Обновляет кнопку Mini App только для одного пользователя."""
        if not self.url:
            return False
        return self._set_menu_button(tg_id, {
            "type":    "web_app",
            "text":    _MENU_BUTTON_TEXT,
            "web_app": {"url": self.url},
        })
