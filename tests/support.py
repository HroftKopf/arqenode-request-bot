"""Общие тестовые двойники для aiogram и изолированной SQLite."""
from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User

import config
import database as db
import rate_limit

ADMIN_ID = 9001
CLIENT_ID = 1001
OTHER_ID = 1002
BOT_ID = 5001


def make_user(user_id: int = CLIENT_ID, first_name: str = 'Tester') -> User:
    return User(id=user_id, is_bot=False, first_name=first_name)


def make_message(
    text: str = '',
    user_id: int = CLIENT_ID,
    chat_id: int | None = None,
    message_id: int = 1,
    bot: Any = None,
) -> Message:
    message = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id if chat_id is not None else user_id, type='private'),
        from_user=make_user(user_id),
        text=text,
    )
    if bot is not None:
        message.as_(bot)
    return message


def make_callback(
    data: str,
    user_id: int = CLIENT_ID,
    message: Message | None = None,
    bot: Any = None,
) -> CallbackQuery:
    if message is None:
        message = make_message(user_id=user_id, bot=bot)
    callback = CallbackQuery(
        id='callback-1',
        from_user=make_user(user_id),
        chat_instance='chat-instance',
        data=data,
        message=message,
    )
    if bot is not None:
        callback.as_(bot)
    return callback


def make_state(user_id: int = CLIENT_ID, chat_id: int | None = None) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(
            bot_id=BOT_ID,
            chat_id=chat_id if chat_id is not None else user_id,
            user_id=user_id,
        ),
    )


def api_error(
    error_type: type[TelegramAPIError] = TelegramAPIError,
    message: str = 'test error',
    retry_after: int = 1,
) -> TelegramAPIError:
    method = SendMessage(chat_id=1, text='test')
    if error_type is TelegramRetryAfter:
        return TelegramRetryAfter(method=method, message=message, retry_after=retry_after)
    return error_type(method=method, message=message)


class FakeBot:
    """Минимальный Bot-двойник: записывает вызовы и умеет отдавать ошибки."""

    def __init__(self) -> None:
        self.id = BOT_ID
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.answered: list[dict[str, Any]] = []
        self.send_effects: list[BaseException | None] = []
        self.edit_effects: list[BaseException | None] = []
        self.answer_effects: list[BaseException | None] = []

    @staticmethod
    def _pop_effect(effects: list[BaseException | None]) -> BaseException | None:
        return effects.pop(0) if effects else None

    async def __call__(self, method: Any):
        """Диспетчер вызовов aiogram: Message.answer/edit_text и CallbackQuery.answer."""
        if isinstance(method, SendMessage):
            return await self.send_message(
                method.chat_id,
                method.text,
                reply_markup=method.reply_markup,
            )
        if isinstance(method, EditMessageText):
            return await self.edit_message_text(
                method.text,
                chat_id=method.chat_id,
                message_id=method.message_id,
                reply_markup=method.reply_markup,
            )
        if isinstance(method, AnswerCallbackQuery):
            return await self.answer_callback_query(
                callback_query_id=method.callback_query_id,
                text=method.text,
                show_alert=method.show_alert,
            )
        raise AssertionError(f'FakeBot не поддерживает метод {type(method).__name__}')

    async def send_message(self, chat_id: int, text: str, **kwargs: Any):
        effect = self._pop_effect(self.send_effects)
        if effect is not None:
            raise effect
        self.sent.append({'chat_id': chat_id, 'text': text, **kwargs})
        return make_message(text=text, user_id=chat_id)

    async def edit_message_text(self, text: str, **kwargs: Any):
        effect = self._pop_effect(self.edit_effects)
        if effect is not None:
            raise effect
        self.edited.append({'text': text, **kwargs})
        return make_message(text=text)

    async def answer_callback_query(self, **kwargs: Any):
        effect = self._pop_effect(self.answer_effects)
        if effect is not None:
            raise effect
        self.answered.append(kwargs)
        return True

    def last_sent_text(self) -> str:
        return self.sent[-1]['text'] if self.sent else ''


def button_rows(markup: InlineKeyboardMarkup | None) -> list[list[tuple[str, str]]]:
    if markup is None:
        return []
    return [[(button.text, button.callback_data or '') for button in row]
            for row in markup.inline_keyboard]


def callback_data(markup: InlineKeyboardMarkup | None) -> list[str]:
    return [data for row in button_rows(markup) for _, data in row]


class AppTestCase(unittest.IsolatedAsyncioTestCase):
    """Изолированная БД + подмена админов + управляемые часы rate limiter."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.original_db_path = config.DATABASE_PATH
        self.original_admins = config.ADMIN_IDS
        config.DATABASE_PATH = Path(self.temp.name) / 'requests.db'
        config.ADMIN_IDS = frozenset({ADMIN_ID})
        await db.init_db()
        self.now = 1000.0
        self.old_limiter = rate_limit.get_limiter()
        self.limiter = rate_limit.RateLimiter(clock=lambda: self.now)
        rate_limit.set_limiter(self.limiter)
        self.bot = FakeBot()

    async def asyncTearDown(self) -> None:
        rate_limit.set_limiter(self.old_limiter)
        config.DATABASE_PATH = self.original_db_path
        config.ADMIN_IDS = self.original_admins
        self.temp.cleanup()

    def message(self, text: str = '', user_id: int = CLIENT_ID) -> Message:
        return make_message(text=text, user_id=user_id, bot=self.bot)

    def callback(self, data: str, user_id: int = CLIENT_ID) -> CallbackQuery:
        return make_callback(data=data, user_id=user_id, bot=self.bot)

    def state(self, user_id: int = CLIENT_ID) -> FSMContext:
        return make_state(user_id=user_id)
