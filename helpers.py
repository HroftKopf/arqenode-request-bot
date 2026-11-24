"""Форматирование, безопасная доставка уведомлений и обновление сообщений."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import config
from keyboards import admin_detail
from models import MAX_CALLBACK_ID, MAX_HISTORY_BODY, MAX_PAGE, status_label

log = logging.getLogger(__name__)

SEND_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5
MAX_RETRY_DELAY = 5.0


def request_text(item: dict, admin: bool = False) -> str:
    text = (
        f"📋 Заявка #{item['id']}\n"
        f"Категория: {item['category']}\n"
        f"Статус: {status_label(item['status'])}\n"
        f"Создана: {item['created_at']} UTC\n\n"
        f"📝 {item['description']}"
    )
    if admin:
        text = f"👤 Telegram ID клиента: {item['user_id']}\n" + text
    return text


async def safe_send(
    bot: Bot,
    uid: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Доставить сообщение с ограниченными повторами для 429/сетевых сбоев.

    Постоянные ошибки (403, 400) не повторяются. В логи не попадают текст
    сообщения и токен — только ID получателя, тип ошибки и retry_after.
    """
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            await bot.send_message(uid, text, reply_markup=reply_markup)
            return True
        except TelegramRetryAfter as exc:
            if attempt >= SEND_ATTEMPTS:
                log.warning('Доставка отложена: uid=%s, лимит Telegram (429)', uid)
                return False
            delay = min(max(float(exc.retry_after), RETRY_BASE_DELAY), MAX_RETRY_DELAY)
            log.warning(
                'Telegram 429 для uid=%s, повтор через %.1f с (попытка %d/%d)',
                uid, delay, attempt, SEND_ATTEMPTS,
            )
            await asyncio.sleep(delay)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning('Доставка невозможна: uid=%s, ошибка=%s', uid, type(exc).__name__)
            return False
        except (TelegramNetworkError, TelegramServerError) as exc:
            if attempt >= SEND_ATTEMPTS:
                log.warning('Доставка не удалась: uid=%s, ошибка=%s', uid, type(exc).__name__)
                return False
            delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
            log.warning(
                'Временный сбой Telegram для uid=%s, повтор через %.1f с (%s)',
                uid, delay, type(exc).__name__,
            )
            await asyncio.sleep(delay)
        except TelegramAPIError as exc:
            log.warning('Не удалось доставить: uid=%s, ошибка=%s', uid, type(exc).__name__)
            return False


async def notify_admins(
    bot: Bot,
    text: str,
    item: dict | None = None,
    exclude: int | None = None,
) -> list[int]:
    """Разослать уведомление админам; вернуть ID тех, кому доставить не удалось."""
    markup = admin_detail(item) if item else None
    failed: list[int] = []
    for admin_id in config.ADMIN_IDS:
        if admin_id == exclude:
            continue
        if not await safe_send(bot, admin_id, text, reply_markup=markup):
            failed.append(admin_id)
    if failed:
        log.warning('Уведомление не доставлено %d администратор(ам)', len(failed))
    return failed


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    return 'message is not modified' in str(exc).lower()


async def redraw(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Обновить сообщение, не создавая дубликат при «message is not modified»."""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            if _is_not_modified(exc):
                return False
            log.warning('Не удалось изменить сообщение, отправляю новое: %s', type(exc).__name__)
        except TelegramAPIError as exc:
            log.warning('Ошибка редактирования сообщения: %s', type(exc).__name__)
    try:
        if isinstance(message, Message):
            await message.answer(text, reply_markup=reply_markup)
        else:
            await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)
        return True
    except TelegramAPIError as exc:
        log.warning('Не удалось отправить замену сообщения: %s', type(exc).__name__)
        return False


def format_history_body(body: str) -> str:
    """Обрезать длинное сообщение, явно обозначив сокращение."""
    if len(body) <= MAX_HISTORY_BODY:
        return body
    return body[:MAX_HISTORY_BODY].rstrip() + '\n… (сообщение обрезано)'


def parse_history_args(parts: list[str]) -> tuple[int, int | None]:
    """Разобрать старый (4 части) и новый (5 частей) callback истории."""
    page = 0
    max_id = None
    if len(parts) >= 4 and parts[3].isdigit():
        page = min(int(parts[3]), MAX_PAGE)
    if len(parts) >= 5 and parts[4].isdigit():
        max_id = min(int(parts[4]), MAX_CALLBACK_ID)
    return page, max_id


def history_text(
    request_id: int,
    rows: list[dict],
    page: int,
    has_more: bool = False,
) -> str:
    text = (
        f'📜 Переписка по заявке #{request_id} '
        f'(страница {page + 1}, от новых к старым):\n\n'
    )
    if not rows:
        text += 'Сообщений пока нет.\nОписание заявки находится в её карточке.'
        return text
    for msg in rows:
        sender = '🛠 Администратор' if msg['sender_role'] == 'admin' else '👤 Клиент'
        text += f"{sender} · {msg['created_at']} UTC\n{format_history_body(msg['body'])}\n\n"
    if has_more:
        text += 'Показаны не все сообщения. Используйте кнопки навигации.'
    return text


def client_help(admin: bool = False) -> str:
    text = (
        '👋 Добро пожаловать в Arqenode Requests!\n\n'
        'Здесь можно оставить IT-заявку и общаться с исполнителями.\n\n'
        'Как пользоваться:\n'
        '1. Нажмите «📝 Создать заявку».\n'
        '2. Выберите категорию и опишите задачу.\n'
        '3. Подтвердите отправку.\n'
        '4. Откройте «📋 Мои заявки», чтобы проверить статус, '
        'изменить описание или написать сообщение.\n\n'
        'Статусы: новая → в работе → ожидает ответа → выполнена. '
        'Заявку можно отменить до закрытия.\n\n'
        'Команды: /start — меню, /help — инструкция, '
        '/cancel — прервать ввод, /id — ваш Telegram ID.'
    )
    if admin:
        text += (
            '\n\n🛠 Для администратора:\n'
            'Откройте «🛠 Админ-панель» или /admin, '
            'выберите фильтр и заявку.\n'
            'В карточке можно ответить клиенту, '
            'посмотреть переписку и сменить статус.\n'
            '/requests — все заявки, /cancel — выйти из ввода ответа.\n'
            'Уведомления о новых заявках приходят автоматически.'
        )
    return text
