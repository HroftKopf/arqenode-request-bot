"""Тесты переписки, истории и устойчивости диалогов."""
from __future__ import annotations

from aiogram.exceptions import TelegramForbiddenError
from support import ADMIN_ID, CLIENT_ID, OTHER_ID, AppTestCase, api_error, callback_data

import config
import database as db
import rate_limit
from handlers import dialogs
from models import MAX_REPLY
from states import ReplyForm


class DialogTests(AppTestCase):
    async def _reply_state(self, rid: int, role: str = 'client', user_id: int = CLIENT_ID):
        state = self.state(user_id)
        await state.set_state(ReplyForm.text)
        await state.update_data(request_id=rid, role=role)
        return state

    async def test_client_reply_saved_and_status_moves_to_work(self):
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для ответа клиента')
        await db.set_status(rid, 'wait')
        state = await self._reply_state(rid)
        await dialogs.record_reply(self.message('Уточняю детали задачи', CLIENT_ID), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertEqual((await db.get_request(rid))['status'], 'work')
        self.assertTrue(any(call['chat_id'] == ADMIN_ID and 'Ответ клиента' in call['text']
                            for call in self.bot.sent))
        rows, _, _ = await db.history(rid)
        self.assertEqual(rows[0]['body'], 'Уточняю детали задачи')

    async def test_admin_reply_notifies_client_and_audits(self):
        config.ADMIN_IDS = frozenset({ADMIN_ID, OTHER_ID})
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для ответа админа')
        state = await self._reply_state(rid, role='admin', user_id=ADMIN_ID)
        await dialogs.record_reply(self.message('Ответ администратора', ADMIN_ID), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertEqual((await db.admin_actions(rid))[0]['admin_id'], ADMIN_ID)
        self.assertTrue(any(call['chat_id'] == CLIENT_ID and 'Ответ по заявке' in call['text']
                            for call in self.bot.sent))
        self.assertTrue(any('Коллега ответил' in call['text'] for call in self.bot.sent))

    async def test_admin_reply_delivery_failure_warns_admin(self):
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для сбоя доставки')
        state = await self._reply_state(rid, role='admin', user_id=ADMIN_ID)
        # 1) подтверждение админу, 2) доставка клиенту (ошибка), 3) предупреждение админу
        self.bot.send_effects = [None, api_error(TelegramForbiddenError), None]
        await dialogs.record_reply(self.message('Ответ без доставки', ADMIN_ID), state, self.bot)
        self.assertTrue(any('не удалось доставить' in call['text'] and call['chat_id'] == ADMIN_ID
                            for call in self.bot.sent))

    async def test_invalid_reply_length_keeps_state(self):
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для проверки длины')
        state = await self._reply_state(rid)
        await dialogs.record_reply(self.message('', CLIENT_ID), state, self.bot)
        self.assertEqual(await state.get_state(), ReplyForm.text.state)
        await dialogs.record_reply(self.message('x' * (MAX_REPLY + 1), CLIENT_ID), state, self.bot)
        self.assertEqual(await state.get_state(), ReplyForm.text.state)
        self.assertIn('1–1500', self.bot.last_sent_text())

    async def test_stale_or_invalid_role_state(self):
        state = self.state()
        await state.set_state(ReplyForm.text)
        await dialogs.record_reply(self.message('текст', CLIENT_ID), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertIn('Сессия устарела', self.bot.last_sent_text())

    async def test_client_cannot_forge_admin_role(self):
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для проверки роли')
        state = await self._reply_state(rid, role='admin', user_id=CLIENT_ID)
        await dialogs.record_reply(self.message('попытка административного ответа', CLIENT_ID), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertIn('Недостаточно прав', self.bot.last_sent_text())
        self.assertEqual((await db.history(rid))[0], [])

    async def test_reply_rate_limit_keeps_state(self):
        rate_limit.set_limiter(rate_limit.RateLimiter({'reply': (1, 60)}, clock=lambda: 1000.0))
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для rate limit')
        state = await self._reply_state(rid)
        await dialogs.record_reply(self.message('первое сообщение', CLIENT_ID), state, self.bot)
        state = await self._reply_state(rid)
        await dialogs.record_reply(self.message('второе сообщение', CLIENT_ID), state, self.bot)
        self.assertEqual(await state.get_state(), ReplyForm.text.state)
        self.assertIn('Слишком часто', self.bot.last_sent_text())
        messages, _, _ = await db.history(rid)
        self.assertEqual([row['body'] for row in messages], ['первое сообщение'])

    async def test_reply_to_missing_or_closed_request(self):
        state = await self._reply_state(999)
        await dialogs.record_reply(self.message('нет заявки', CLIENT_ID), state, self.bot)
        self.assertIn('не найдена', self.bot.last_sent_text())

        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для закрытого ответа')
        await db.set_status(rid, 'done')
        state = await self._reply_state(rid)
        await dialogs.record_reply(self.message('поздний ответ', CLIENT_ID), state, self.bot)
        self.assertIn('уже закрыта', self.bot.last_sent_text())

    async def test_invalid_non_text_reply(self):
        await dialogs.invalid_reply(self.message(''))
        self.assertIn('только текст', self.bot.last_sent_text())

    async def test_show_history_empty_and_paged_with_snapshot(self):
        rid = await db.create_request(CLIENT_ID, 'Серверы', 'Заявка для истории')
        callback = self.callback(f'u:history:{rid}:0')
        await dialogs.show_history(callback, await db.get_request(rid), 'client')
        self.assertIn('Сообщений пока нет', self.bot.edited[-1]['text'])
        for index in range(4):
            await db.add_reply(rid, CLIENT_ID, 'client', f'история {index}')
        callback = self.callback(f'u:history:{rid}:0')
        await dialogs.show_history(callback, await db.get_request(rid), 'client')
        self.assertIn('история 3', self.bot.edited[-1]['text'])
        self.assertTrue(any(data.startswith('u:history:') and ':1:' in data
                            for data in callback_data(self.bot.edited[-1]['reply_markup'])))


class DialogEdgeTests(AppTestCase):
    async def test_none_item_after_saved_reply(self):
        from unittest.mock import AsyncMock, patch
        rid = await db.create_request(CLIENT_ID, 'Другое', 'Заявка с неожиданным ответом')
        state = self.state()
        await state.set_state(ReplyForm.text)
        await state.update_data(request_id=rid, role='client')
        with patch.object(dialogs, 'add_reply', new=AsyncMock(return_value=('saved', None))):
            await dialogs.record_reply(self.message('текст', CLIENT_ID), state, self.bot)
        self.assertIn('Не удалось отправить', self.bot.last_sent_text())
