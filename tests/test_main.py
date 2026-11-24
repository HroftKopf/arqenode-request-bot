"""Тесты точки входа, логирования и глобального обработчика ошибок."""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.exceptions import TelegramForbiddenError
from support import FakeBot, api_error, make_callback, make_message

import main


class LoggingTests(unittest.TestCase):
    def test_setup_logging_creates_rotating_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'logs' / 'bot.log'
            try:
                main.setup_logging(path)
                logging.getLogger('test').info('hello')
                self.assertTrue(path.exists())
                self.assertIn('hello', path.read_text(encoding='utf-8'))
            finally:
                for handler in logging.getLogger().handlers[:]:
                    logging.getLogger().removeHandler(handler)
                    handler.close()
                logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()], force=True)


class ErrorHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_callback_user_and_returns_false(self):
        bot = FakeBot()
        callback = make_callback('u:list:0', bot=bot)
        event = SimpleNamespace(
            update=SimpleNamespace(callback_query=callback, message=None),
            exception=ValueError('boom'),
        )
        with self.assertLogs('main', level='ERROR'):
            result = await main.on_error(event)
        self.assertFalse(result)
        self.assertIn('Внутренняя ошибка', bot.answered[-1]['text'])

    async def test_notifies_message_user(self):
        bot = FakeBot()
        message = make_message('x', bot=bot)
        event = SimpleNamespace(
            update=SimpleNamespace(callback_query=None, message=message),
            exception=RuntimeError('boom'),
        )
        with self.assertLogs('main', level='ERROR'):
            result = await main.on_error(event)
        self.assertFalse(result)
        self.assertIn('Внутренняя ошибка', bot.sent[-1]['text'])

    async def test_notification_failure_does_not_mask_original_error(self):
        bot = FakeBot()
        callback = make_callback('u:list:0', bot=bot)
        bot.answer_effects = [api_error(TelegramForbiddenError)]
        event = SimpleNamespace(
            update=SimpleNamespace(callback_query=callback, message=None),
            exception=ValueError('boom'),
        )
        with self.assertLogs('main', level='ERROR'):
            self.assertFalse(await main.on_error(event))


class _BotContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


class MainWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_validates_config_initializes_db_and_starts_polling(self):
        dispatcher = SimpleNamespace(
            errors=SimpleNamespace(register=Mock()),
            include_router=Mock(),
            start_polling=AsyncMock(),
        )
        with (
            patch.object(main.config, 'validate_config') as validate,
            patch.object(main, 'setup_logging') as setup_logging,
            patch.object(main, 'init_db', new=AsyncMock()) as init_db,
            patch.object(main, 'Dispatcher', return_value=dispatcher),
            patch.object(main, 'Bot', return_value=_BotContext()),
        ):
            await main.main()
        validate.assert_called_once()
        setup_logging.assert_called_once()
        init_db.assert_awaited_once()
        self.assertEqual(dispatcher.include_router.call_count, 4)
        dispatcher.errors.register.assert_called_once_with(main.on_error)
        dispatcher.start_polling.assert_awaited_once()


class MessageNotificationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_notification_failure_is_suppressed(self):
        bot = FakeBot()
        message = make_message('x', bot=bot)
        bot.send_effects = [api_error(TelegramForbiddenError)]
        event = SimpleNamespace(update=SimpleNamespace(callback_query=None, message=message),
                                exception=ValueError('boom'))
        with self.assertLogs('main', level='ERROR'):
            self.assertFalse(await main.on_error(event))
