"""Тесты административной панели, прав и переходов статусов."""
from __future__ import annotations

from aiogram.exceptions import TelegramForbiddenError
from support import ADMIN_ID, CLIENT_ID, OTHER_ID, AppTestCase, api_error

import database as db
import rate_limit
from handlers import admin
from states import ReplyForm


class AdminAccessTests(AppTestCase):
    async def _create(self) -> int:
        return await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для администратора')

    async def test_admin_panel_denied_to_client(self):
        await admin.admin_start(self.message('/admin', user_id=CLIENT_ID), self.state())
        self.assertIn('Доступ закрыт', self.bot.last_sent_text())

    async def test_admin_panel_allowed_to_admin(self):
        await admin.admin_start(self.message('/admin', user_id=ADMIN_ID), self.state(ADMIN_ID))
        self.assertIn('Панель администратора', self.bot.last_sent_text())

    async def test_requests_denied_to_client(self):
        await admin.admin_requests(self.message('/requests', user_id=CLIENT_ID), self.state())
        self.assertIn('Доступ закрыт', self.bot.last_sent_text())

    async def test_requests_allowed_to_admin(self):
        rid = await self._create()
        await admin.admin_requests(self.message('/requests', user_id=ADMIN_ID), self.state(ADMIN_ID))
        self.assertIn('Все заявки', self.bot.last_sent_text())
        self.assertIn(f'a:open:{rid}', [
            button.callback_data
            for row in self.bot.sent[-1]['reply_markup'].inline_keyboard
            for button in row
        ])

    async def test_admin_callback_denied_to_client(self):
        rid = await self._create()
        await admin.admin_callbacks(self.callback(f'a:open:{rid}', user_id=CLIENT_ID), self.state(), self.bot)
        self.assertIn('Доступ запрещён', self.bot.answered[-1]['text'])

    async def test_admin_callback_denied_to_other_user(self):
        rid = await self._create()
        await admin.admin_callbacks(self.callback(f'a:open:{rid}', user_id=OTHER_ID), self.state(OTHER_ID), self.bot)
        self.assertIn('Доступ запрещён', self.bot.answered[-1]['text'])


class AdminCallbackTests(AppTestCase):
    async def _create(self) -> int:
        return await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для администратора')

    async def test_menu_and_open(self):
        rid = await self._create()
        await admin.admin_callbacks(self.callback('a:menu', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Выберите фильтр', self.bot.edited[-1]['text'])
        await admin.admin_callbacks(self.callback(f'a:open:{rid}', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn(f'Заявка #{rid}', self.bot.edited[-1]['text'])
        self.assertIn('Telegram ID клиента', self.bot.edited[-1]['text'])

    async def test_filter_list_and_invalid_filters(self):
        await self._create()
        await admin.admin_callbacks(self.callback('a:list:all:0', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Все — страница 1', self.bot.edited[-1]['text'])
        for data in ('a:list:bad:0', 'a:list:all:x'):
            with self.subTest(data=data):
                await admin.admin_callbacks(self.callback(data, ADMIN_ID), self.state(ADMIN_ID), self.bot)
                self.assertIn('Некорректный фильтр', self.bot.answered[-1]['text'])

    async def test_status_change_updates_client_and_audit(self):
        rid = await self._create()
        await admin.admin_callbacks(
            self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot
        )
        self.assertEqual((await db.get_request(rid))['status'], 'work')
        self.assertTrue(any(call['chat_id'] == CLIENT_ID and 'Статус вашей заявки' in call['text']
                            for call in self.bot.sent))
        actions = await db.admin_actions(rid)
        self.assertEqual(actions[0]['admin_id'], ADMIN_ID)
        self.assertEqual(actions[0]['details'], 'new->work')

    async def test_same_invalid_closed_and_missing_outcomes(self):
        rid = await self._create()
        await admin.admin_callbacks(self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        await admin.admin_callbacks(self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('уже установлен', self.bot.answered[-1]['text'])

        await admin.admin_callbacks(self.callback(f'a:status:{rid}:new', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Недопустимый переход', self.bot.answered[-1]['text'])

        await admin.admin_callbacks(self.callback(f'a:status:{rid}:done', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        await admin.admin_callbacks(self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('уже закрыта', self.bot.answered[-1]['text'])

        await admin.admin_callbacks(self.callback('a:status:999:work', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('не найдена', self.bot.answered[-1]['text'])

    async def test_status_rate_limit(self):
        rate_limit.set_limiter(rate_limit.RateLimiter({'status': (1, 60)}, clock=lambda: 1000.0))
        rid = await self._create()
        await admin.admin_callbacks(self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        await admin.admin_callbacks(self.callback(f'a:status:{rid}:wait', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Слишком часто', self.bot.answered[-1]['text'])
        self.assertEqual((await db.get_request(rid))['status'], 'work')

    async def test_client_delivery_failure_is_reported_to_admin(self):
        rid = await self._create()
        self.bot.send_effects = [api_error(TelegramForbiddenError), None]
        await admin.admin_callbacks(
            self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot
        )
        self.assertTrue(any('не удалось доставить' in call['text'] and call['chat_id'] == ADMIN_ID
                            for call in self.bot.sent))

    async def test_reply_starts_admin_state(self):
        rid = await self._create()
        state = self.state(ADMIN_ID)
        await admin.admin_callbacks(self.callback(f'a:reply:{rid}', ADMIN_ID), state, self.bot)
        self.assertEqual(await state.get_state(), ReplyForm.text.state)
        self.assertEqual((await state.get_data())['role'], 'admin')

    async def test_reply_denied_for_terminal_request(self):
        rid = await self._create()
        await db.set_status(rid, 'done')
        await admin.admin_callbacks(self.callback(f'a:reply:{rid}', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('закрыта', self.bot.answered[-1]['text'])

    async def test_history(self):
        rid = await self._create()
        await db.add_reply(rid, CLIENT_ID, 'client', 'вопрос клиента')
        await admin.admin_callbacks(self.callback(f'a:history:{rid}:0', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Переписка по заявке', self.bot.edited[-1]['text'])
        self.assertIn('вопрос клиента', self.bot.edited[-1]['text'])

    async def test_invalid_status_and_unknown_action(self):
        rid = await self._create()
        await admin.admin_callbacks(self.callback(f'a:status:{rid}:bad', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Некорректный статус', self.bot.answered[-1]['text'])
        await admin.admin_callbacks(self.callback(f'a:unknown:{rid}', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Неизвестная кнопка', self.bot.answered[-1]['text'])
        await admin.admin_callbacks(self.callback('a:short', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('Некорректная заявка', self.bot.answered[-1]['text'])


class AdminConcurrencyEdgeTests(AppTestCase):
    async def test_missing_after_initial_read_is_reported(self):
        from unittest.mock import AsyncMock, patch
        rid = await db.create_request(CLIENT_ID, 'Другое', 'Заявка исчезла после чтения')
        with patch.object(admin, 'set_status', new=AsyncMock(return_value=('missing', None))):
            await admin.admin_callbacks(self.callback(f'a:status:{rid}:work', ADMIN_ID), self.state(ADMIN_ID), self.bot)
        self.assertIn('не найдена', self.bot.answered[-1]['text'])
