"""Запуск Arqenode Request Bot: python main.py"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

import config
from database import init_db
from handlers import admin, client, common, dialogs

log = logging.getLogger(__name__)


def setup_logging(log_path: Path | None = None) -> None:
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    path = Path(log_path) if log_path is not None else config.LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler], force=True)


async def _notify_error_user(event: ErrorEvent) -> None:
    """Мягко сообщить пользователю о сбое, не раскрывая детали."""
    update = event.update
    callback = getattr(update, 'callback_query', None)
    if callback is not None:
        with contextlib.suppress(Exception):
            await callback.answer(
                '⚠️ Внутренняя ошибка. Попробуйте ещё раз позже.',
                show_alert=True,
            )
        return
    message = getattr(update, 'message', None)
    if message is not None:
        with contextlib.suppress(Exception):
            await message.answer('⚠️ Внутренняя ошибка. Попробуйте ещё раз позже.')


async def on_error(event: ErrorEvent) -> bool:
    """Залогировать ошибку, уведомить пользователя и не скрывать её от aiogram."""
    error = event.exception
    log.error(
        'Ошибка обработки обновления: %s',
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__),
    )
    await _notify_error_user(event)
    return False


async def main():
    config.validate_config()
    setup_logging()
    await init_db()
    dp = Dispatcher()
    dp.errors.register(on_error)
    # Порядок важен: команды /start, /help, /cancel действуют в любом состоянии.
    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(client.router)
    dp.include_router(dialogs.router)
    async with Bot(token=config.BOT_TOKEN) as bot:
        logging.info('Arqenode Bot запущен; администраторов: %d', len(config.ADMIN_IDS))
        await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
