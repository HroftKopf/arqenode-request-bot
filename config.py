"""Настройки Arqenode Request Bot.

Конфигурация fail-closed: отсутствие или некорректный формат обязательных
настроек не подменяется значениями по умолчанию, а блокирует запуск.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()


def _resolve_path(raw: str | None, default: Path) -> Path:
    """Относительные пути трактуются от каталога проекта, а не от CWD."""
    value = Path(raw).expanduser() if raw else default
    if not value.is_absolute():
        value = BASE_DIR / value
    return value.resolve()


DATABASE_PATH = _resolve_path(os.getenv('DATABASE_PATH'), BASE_DIR / 'requests.db')
LOG_PATH = _resolve_path(os.getenv('LOG_PATH'), BASE_DIR / 'bot.log')


def parse_admin_ids(raw: str | None) -> frozenset[int]:
    """Разобрать ADMIN_IDS строго; при любой ошибке — ValueError.

    Значения не включаются в текст ошибки, чтобы не раскрывать ID в логах.
    """
    if raw is None or not raw.strip():
        raise ValueError('ADMIN_IDS не задан: укажите положительные Telegram ID через запятую')
    ids: set[int] = set()
    for part in raw.split(','):
        token = part.strip()
        if not token:
            raise ValueError('ADMIN_IDS: пустой элемент в списке ID')
        try:
            value = int(token)
        except ValueError:
            raise ValueError('ADMIN_IDS: каждый ID должен быть целым числом') from None
        if value <= 0:
            raise ValueError('ADMIN_IDS: каждый ID должен быть положительным числом')
        ids.add(value)
    return frozenset(ids)


# Ошибки импорта не выбрасываются сразу: конфиг должен оставаться импортируемым
# для тестов, но запуск блокируется validate_config(). При этом ADMIN_IDS пуст,
# то есть при случайном пропуске валидации доступ администратора закрыт.
CONFIG_ERRORS: list[str] = []
try:
    ADMIN_IDS = parse_admin_ids(os.getenv('ADMIN_IDS'))
except ValueError as exc:
    ADMIN_IDS = frozenset()
    CONFIG_ERRORS.append(str(exc))


def validate_config() -> None:
    """Проверить обязательные настройки и завершить запуск с понятной ошибкой."""
    errors = list(CONFIG_ERRORS)
    if not BOT_TOKEN:
        errors.append('BOT_TOKEN не задан: создайте .env и укажите токен от @BotFather')
    elif ':' not in BOT_TOKEN:
        errors.append('BOT_TOKEN: неверный формат токена')
    elif 'PLACE' in BOT_TOKEN.upper() or 'YOUR' in BOT_TOKEN.upper():
        errors.append('BOT_TOKEN: замените значение-заглушку на реальный токен')
    if not ADMIN_IDS and not any('ADMIN_IDS' in error for error in errors):
        errors.append('ADMIN_IDS не задан: укажите хотя бы один Telegram ID')
    if errors:
        raise RuntimeError('Некорректная конфигурация: ' + '; '.join(errors))


def is_admin(user_id: int) -> bool:
    """Явная проверка административных прав."""
    return user_id in ADMIN_IDS
