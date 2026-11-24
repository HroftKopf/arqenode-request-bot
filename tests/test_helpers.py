"""Тесты доставки уведомлений, redraw и форматирования."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from support import FakeBot, api_error, make_callback

import config
import helpers
from models import MAX_HISTORY_BODY


def item(status: str = 'new') -> dict:
    return {
        'id': 5,
        'user_id': 1001,
        'category': 'Серверы',
        'description': 'Описание задачи достаточной длины',
        'status': status,
        'created_at': '2026-01-01 00:00:00',
    }


class SafeSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = FakeBot()

    async def test_success(self):
        self.assertTrue(await helpers.safe_send(self.bot, 1, 'hello'))
        self.assertEqual(self.bot.sent[0]['text'], 'hello')

    async def test_forbidden_and_bad_request_are_not_retried(self):
        for error_type in (TelegramForbiddenError, TelegramBadRequest):
            with self.subTest(error_type=error_type.__name__):
                self.bot.send_effects = [api_error(error_type)]
                self.assertFalse(await helpers.safe_send(self.bot, 1, 'hello'))
                self.assertEqual(len(self.bot.sent), 0)

    async def test_retry_after_then_success(self):
        self.bot.send_effects = [
            api_error(TelegramRetryAfter, retry_after=2),
            None,
        ]
        with patch('asyncio.sleep', new=AsyncMock()) as sleep:
            self.assertTrue(await helpers.safe_send(self.bot, 1, 'hello'))
        sleep.assert_awaited_once_with(2.0)

    async def test_retry_after_is_bounded(self):
        self.bot.send_effects = [
            api_error(TelegramRetryAfter, retry_after=60),
            None,
        ]
        with patch('asyncio.sleep', new=AsyncMock()) as sleep:
            self.assertTrue(await helpers.safe_send(self.bot, 1, 'hello'))
        sleep.assert_awaited_once_with(helpers.MAX_RETRY_DELAY)

    async def test_network_errors_exhaust_retries(self):
        self.bot.send_effects = [
            api_error(TelegramNetworkError),
            api_error(TelegramNetworkError),
            api_error(TelegramNetworkError),
        ]
        with patch('asyncio.sleep', new=AsyncMock()) as sleep:
            self.assertFalse(await helpers.safe_send(self.bot, 1, 'hello'))
        self.assertEqual(sleep.await_count, 2)
        self.assertEqual(len(self.bot.sent), 0)

    async def test_retry_after_exhausts_attempts(self):
        self.bot.send_effects = [api_error(TelegramRetryAfter) for _ in range(3)]
        with patch('asyncio.sleep', new=AsyncMock()):
            self.assertFalse(await helpers.safe_send(self.bot, 1, 'hello'))

    async def test_generic_api_error_returns_false(self):
        self.bot.send_effects = [api_error(TelegramAPIError)]
        self.assertFalse(await helpers.safe_send(self.bot, 1, 'hello'))


class NotifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_excludes_actor_and_reports_failures(self):
        bot = FakeBot()
        bot.send_effects = [None, api_error(TelegramForbiddenError)]
        with patch.object(config, 'ADMIN_IDS', (1, 2, 3)):
            failed = await helpers.notify_admins(bot, 'text', item(), exclude=1)
        self.assertEqual(failed, [3])
        self.assertEqual([call['chat_id'] for call in bot.sent], [2])


class RedrawTests(unittest.IsolatedAsyncioTestCase):
    async def test_edits_existing_message(self):
        bot = FakeBot()
        callback = make_callback('u:list:0', bot=bot)
        self.assertTrue(await helpers.redraw(callback, 'new text'))
        self.assertEqual(bot.edited[-1]['text'], 'new text')
        self.assertEqual(bot.sent, [])

    async def test_not_modified_does_not_duplicate_message(self):
        bot = FakeBot()
        bot.edit_effects = [
            api_error(TelegramBadRequest, 'Bad Request: message is not modified'),
        ]
        callback = make_callback('u:list:0', bot=bot)
        self.assertFalse(await helpers.redraw(callback, 'same text'))
        self.assertEqual(bot.sent, [])

    async def test_other_edit_error_falls_back_to_new_message(self):
        bot = FakeBot()
        bot.edit_effects = [api_error(TelegramBadRequest, 'Bad Request: message to edit not found')]
        callback = make_callback('u:list:0', bot=bot)
        self.assertTrue(await helpers.redraw(callback, 'new text'))
        self.assertEqual(bot.sent[-1]['text'], 'new text')

    async def test_inaccessible_message_sends_to_user(self):
        bot = FakeBot()
        callback = make_callback('u:list:0', bot=bot).model_copy(update={'message': None})
        callback.as_(bot)
        self.assertTrue(await helpers.redraw(callback, 'new text'))
        self.assertEqual(bot.sent[-1]['chat_id'], callback.from_user.id)

    async def test_failed_fallback_returns_false(self):
        bot = FakeBot()
        bot.edit_effects = [api_error(TelegramBadRequest, 'Bad Request: edit failed')]
        bot.send_effects = [api_error(TelegramForbiddenError)]
        callback = make_callback('u:list:0', bot=bot)
        self.assertFalse(await helpers.redraw(callback, 'new text'))


class FormattingTests(unittest.TestCase):
    def test_request_text_client_and_admin(self):
        client_text = helpers.request_text(item())
        admin_text = helpers.request_text(item('done'), admin=True)
        self.assertIn('Заявка #5', client_text)
        self.assertIn('Статус: Новая', client_text)
        self.assertNotIn('Telegram ID клиента', client_text)
        self.assertIn('Telegram ID клиента: 1001', admin_text)
        self.assertIn('Статус: Выполнена', admin_text)

    def test_history_body_truncation_marker(self):
        short = 'x' * MAX_HISTORY_BODY
        self.assertEqual(helpers.format_history_body(short), short)
        long = 'x' * (MAX_HISTORY_BODY + 10)
        formatted = helpers.format_history_body(long)
        self.assertTrue(formatted.endswith('(сообщение обрезано)'))
        self.assertIn('…', formatted)

    def test_history_text_empty_and_rows(self):
        empty = helpers.history_text(1, [], 0)
        self.assertIn('Сообщений пока нет', empty)
        rows = [
            {'sender_role': 'admin', 'created_at': '2026-01-01', 'body': 'ответ'},
            {'sender_role': 'client', 'created_at': '2026-01-02', 'body': 'вопрос'},
        ]
        text = helpers.history_text(1, rows, 1, has_more=True)
        self.assertIn('страница 2', text)
        self.assertIn('🛠 Администратор', text)
        self.assertIn('👤 Клиент', text)
        self.assertIn('Показаны не все сообщения', text)

    def test_parse_history_args_supports_old_and_new_format(self):
        self.assertEqual(helpers.parse_history_args(['u', 'history', '5', '2']), (2, None))
        self.assertEqual(helpers.parse_history_args(['u', 'history', '5', '2', '42']), (2, 42))
        self.assertEqual(helpers.parse_history_args(['u', 'history', '5']), (0, None))
        self.assertEqual(helpers.parse_history_args(['u', 'history', '5', 'x', 'y']), (0, None))

    def test_client_help_includes_admin_section_only_for_admin(self):
        self.assertNotIn('Для администратора', helpers.client_help(False))
        self.assertIn('Для администратора', helpers.client_help(True))


class RedrawGenericErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_api_error_on_edit_falls_back(self):
        bot = FakeBot()
        bot.edit_effects = [api_error(TelegramAPIError)]
        callback = make_callback('u:list:0', bot=bot)
        self.assertTrue(await helpers.redraw(callback, 'new text'))
        self.assertEqual(bot.sent[-1]['text'], 'new text')
