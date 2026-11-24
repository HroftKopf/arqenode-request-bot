"""Тесты клиентских сценариев и разграничения доступа."""
from __future__ import annotations

from support import ADMIN_ID, CLIENT_ID, OTHER_ID, AppTestCase

import database as db
import rate_limit
from handlers import client
from models import MAX_DESCRIPTION
from states import EditRequest, NewRequest, ReplyForm


class ClientFlowTests(AppTestCase):
    async def _create(self, user_id: int = CLIENT_ID, description: str = 'Заявка для теста') -> int:
        return await db.create_request(user_id, 'Серверы', description)

    async def test_create_form_starts_category_state(self):
        state = self.state()
        await client.create_form(self.message('📝 Создать заявку'), state)
        self.assertEqual(await state.get_state(), NewRequest.category.state)

    async def test_full_create_flow_notifies_admin(self):
        state = self.state()
        await client.create_form(self.message('📝 Создать заявку'), state)
        await client.category_selected(self.message('Серверы'), state)
        self.assertEqual(await state.get_state(), NewRequest.description.state)
        self.assertEqual((await state.get_data())['category'], 'Серверы')

        await client.description_received(self.message('коротко'), state)
        self.assertEqual(await state.get_state(), NewRequest.description.state)

        description = 'Настроить Nginx, HTTPS и резервное копирование'
        await client.description_received(self.message(description), state)
        self.assertEqual(await state.get_state(), NewRequest.confirm.state)
        self.assertEqual((await state.get_data())['description'], description)

        await client.send_new(self.message('✅ Отправить'), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertTrue(any('Заявка #1 создана' in call['text'] for call in self.bot.sent))
        items, _ = await db.list_user_requests(CLIENT_ID)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['description'], description)
        self.assertTrue(any(call['chat_id'] == ADMIN_ID and 'Новая заявка' in call['text']
                            for call in self.bot.sent))

    async def test_send_new_with_stale_state(self):
        state = self.state()
        await state.set_state(NewRequest.confirm)
        await client.send_new(self.message('✅ Отправить'), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertIn('Сессия устарела', self.bot.last_sent_text())
        self.assertEqual((await db.list_user_requests(CLIENT_ID))[0], [])

    async def test_send_new_is_rate_limited(self):
        rate_limit.set_limiter(rate_limit.RateLimiter({'create': (1, 60)}, clock=lambda: 1000.0))
        state = self.state()
        for expected in ('first', 'second'):
            await state.set_state(NewRequest.confirm)
            await state.update_data(category='Другое', description=f'Описание заявки {expected}')
            await client.send_new(self.message('✅ Отправить'), state, self.bot)
        self.assertEqual(len((await db.list_user_requests(CLIENT_ID))[0]), 1)
        self.assertIn('Слишком часто', self.bot.last_sent_text())

    async def test_invalid_description_and_text_handlers(self):
        await client.description_invalid(self.message(''))
        self.assertIn('текстовое описание', self.bot.last_sent_text())
        await client.confirm_invalid(self.message('что-то'))
        self.assertIn('Нажмите', self.bot.last_sent_text())

    async def test_my_requests_empty_and_non_empty(self):
        await client.my_requests(self.message('📋 Мои заявки'))
        self.assertIn('Заявок пока нет', self.bot.last_sent_text())
        await self._create()
        await client.my_requests(self.message('📋 Мои заявки'))
        self.assertIn('страница 1', self.bot.last_sent_text())
        self.assertIn('u:open:1', [data for row in self.bot.sent[-1]['reply_markup'].inline_keyboard
                                   for button in row for data in [button.callback_data]])

    async def test_callback_list_open_and_foreign_access(self):
        rid = await self._create()
        callback = self.callback('u:list:0')
        await client.client_callbacks(callback, self.state(), self.bot)
        self.assertIn('Ваши заявки', self.bot.edited[-1]['text'])

        callback = self.callback(f'u:open:{rid}')
        await client.client_callbacks(callback, self.state(), self.bot)
        self.assertIn(f'Заявка #{rid}', self.bot.edited[-1]['text'])

        foreign = self.callback(f'u:open:{rid}', user_id=OTHER_ID)
        await client.client_callbacks(foreign, self.state(OTHER_ID), self.bot)
        self.assertIn('недоступна', self.bot.answered[-1]['text'])

    async def test_edit_category_and_description(self):
        rid = await self._create()
        callback = self.callback(f'u:edit:{rid}')
        await client.client_callbacks(callback, self.state(), self.bot)
        self.assertIn('Что хотите изменить', self.bot.edited[-1]['text'])

        state = self.state()
        await client.client_callbacks(self.callback(f'u:cat:{rid}'), state, self.bot)
        self.assertEqual(await state.get_state(), EditRequest.category.state)
        await client.save_category(self.message('Другое'), state, self.bot)
        self.assertIsNone(await state.get_state())
        self.assertEqual((await db.get_request(rid))['category'], 'Другое')
        self.assertIn('Категория изменена', self.bot.last_sent_text())

        state = self.state()
        await client.client_callbacks(self.callback(f'u:desc:{rid}'), state, self.bot)
        self.assertEqual(await state.get_state(), EditRequest.description.state)
        await client.save_description(self.message('коротко'), state, self.bot)
        self.assertEqual(await state.get_state(), EditRequest.description.state)
        new_description = 'Обновлённое описание задачи для клиента'
        await client.save_description(self.message(new_description), state, self.bot)
        self.assertEqual((await db.get_request(rid))['description'], new_description)

    async def test_edit_denied_for_non_editable_status(self):
        rid = await self._create()
        await db.set_status(rid, 'work')
        await client.client_callbacks(self.callback(f'u:edit:{rid}'), self.state(), self.bot)
        self.assertIn('нельзя изменить', self.bot.answered[-1]['text'])
        await client.client_callbacks(self.callback(f'u:cat:{rid}'), self.state(), self.bot)
        self.assertIn('недоступно', self.bot.answered[-1]['text'])

    async def test_edit_stale_state(self):
        state = self.state()
        await state.set_state(EditRequest.category)
        await client.save_category(self.message('Другое'), state, self.bot)
        self.assertIn('Сессия устарела', self.bot.last_sent_text())
        self.assertIsNone(await state.get_state())

    async def test_cancel_flow_and_repeat(self):
        rid = await self._create()
        await client.client_callbacks(self.callback(f'u:cancel:{rid}'), self.state(), self.bot)
        self.assertIn('Точно отменить', self.bot.edited[-1]['text'])
        await client.client_callbacks(self.callback(f'u:confirmcancel:{rid}'), self.state(), self.bot)
        self.assertEqual((await db.get_request(rid))['status'], 'cancel')
        self.assertIn('Заявка отменена', self.bot.edited[-1]['text'])
        await client.client_callbacks(self.callback(f'u:confirmcancel:{rid}'), self.state(), self.bot)
        self.assertIn('уже закрыта', self.bot.edited[-1]['text'])

    async def test_cancel_is_rate_limited(self):
        rate_limit.set_limiter(rate_limit.RateLimiter({'cancel': (1, 60)}, clock=lambda: 1000.0))
        first = await self._create()
        second = await self._create(description='Вторая заявка для отмены')
        await client.client_callbacks(self.callback(f'u:confirmcancel:{first}'), self.state(), self.bot)
        await client.client_callbacks(self.callback(f'u:confirmcancel:{second}'), self.state(), self.bot)
        self.assertIn('Слишком часто', self.bot.answered[-1]['text'])
        self.assertEqual((await db.get_request(second))['status'], 'new')

    async def test_reply_starts_dialog_state(self):
        rid = await self._create()
        state = self.state()
        await client.client_callbacks(self.callback(f'u:reply:{rid}'), state, self.bot)
        self.assertEqual(await state.get_state(), ReplyForm.text.state)
        self.assertEqual((await state.get_data())['role'], 'client')
        self.assertEqual((await state.get_data())['request_id'], rid)

    async def test_reply_denied_for_closed_request(self):
        rid = await self._create()
        await db.set_status(rid, 'done')
        await client.client_callbacks(self.callback(f'u:reply:{rid}'), self.state(), self.bot)
        self.assertIn('закрыта', self.bot.answered[-1]['text'])

    async def test_history_old_and_new_callback_formats(self):
        rid = await self._create()
        await db.add_reply(rid, CLIENT_ID, 'client', 'сообщение один')
        await client.client_callbacks(self.callback(f'u:history:{rid}:0'), self.state(), self.bot)
        self.assertIn('Переписка по заявке', self.bot.edited[-1]['text'])
        self.assertIn('сообщение один', self.bot.edited[-1]['text'])
        await client.client_callbacks(self.callback(f'u:history:{rid}:0:1'), self.state(), self.bot)
        self.assertIn('Переписка по заявке', self.bot.edited[-1]['text'])

    async def test_invalid_callback_data(self):
        rid = await self._create()
        cases = {
            'u:short': 'Устаревшая кнопка',
            'u:list:x': 'Некорректная страница',
            'u:open:x': 'Некорректная заявка',
            f'u:unknown:{rid}': 'Неизвестная кнопка',
        }
        for data, expected in cases.items():
            with self.subTest(data=data):
                await client.client_callbacks(self.callback(data), self.state(), self.bot)
                self.assertIn(expected, self.bot.answered[-1]['text'])

    async def test_unknown_command(self):
        await client.unknown_command(self.message('неизвестно'))
        self.assertIn('Не понял сообщение', self.bot.last_sent_text())

    async def test_edit_description_long_rejected(self):
        state = self.state()
        await state.set_state(EditRequest.description)
        await state.update_data(request_id=1)
        await client.save_description(self.message('x' * (MAX_DESCRIPTION + 1)), state, self.bot)
        self.assertEqual(await state.get_state(), EditRequest.description.state)
        self.assertIn('Новое описание', self.bot.last_sent_text())


class ClientEdgeCaseTests(AppTestCase):
    async def test_cancel_terminal_request_shows_closed_alert(self):
        rid = await db.create_request(CLIENT_ID, 'Другое', 'Заявка уже закрыта для отмены')
        await db.set_status(rid, 'done')
        await client.client_callbacks(self.callback(f'u:cancel:{rid}'), self.state(), self.bot)
        self.assertIn('уже закрыта', self.bot.answered[-1]['text'])

    async def test_confirmcancel_missing_race(self):
        from unittest.mock import AsyncMock, patch
        rid = await db.create_request(CLIENT_ID, 'Другое', 'Заявка для гонки отмены')
        with patch.object(client, 'cancel_request', new=AsyncMock(return_value=('missing', None))):
            await client.client_callbacks(self.callback(f'u:confirmcancel:{rid}'), self.state(), self.bot)
        self.assertIn('не найдена', self.bot.edited[-1]['text'])

    async def test_edit_is_rate_limited(self):
        rate_limit.set_limiter(rate_limit.RateLimiter({'edit': (1, 60)}, clock=lambda: 1000.0))
        first = await db.create_request(CLIENT_ID, 'Другое', 'Первая заявка для редактирования')
        second = await db.create_request(CLIENT_ID, 'Другое', 'Вторая заявка для редактирования')
        for rid in (first, second):
            state = self.state()
            await state.set_state(EditRequest.category)
            await state.update_data(request_id=rid)
            await client.save_category(self.message('Серверы'), state, self.bot)
        self.assertIn('Слишком часто', self.bot.last_sent_text())
        self.assertEqual((await db.get_request(second))['category'], 'Другое')

    async def test_stale_description_edit_state(self):
        state = self.state()
        await state.set_state(EditRequest.description)
        await client.save_description(self.message('Достаточно длинное описание'), state, self.bot)
        self.assertIn('Сессия устарела', self.bot.last_sent_text())
        self.assertIsNone(await state.get_state())

    async def test_invalid_edit_description(self):
        await client.invalid_edit_description(self.message(''))
        self.assertIn('/cancel', self.bot.last_sent_text())


class DescriptionRateLimitTests(AppTestCase):
    async def test_edit_description_is_rate_limited(self):
        rate_limit.set_limiter(rate_limit.RateLimiter({'edit': (1, 60)}, clock=lambda: 1000.0))
        first = await db.create_request(CLIENT_ID, 'Другое', 'Первое описание для лимита')
        second = await db.create_request(CLIENT_ID, 'Другое', 'Второе описание для лимита')
        for rid, description in ((first, 'Первое обновлённое описание'), (second, 'Второе обновлённое описание')):
            state = self.state()
            await state.set_state(EditRequest.description)
            await state.update_data(request_id=rid)
            await client.save_description(self.message(description), state, self.bot)
        self.assertIn('Слишком часто', self.bot.last_sent_text())
        self.assertEqual((await db.get_request(second))['description'], 'Второе описание для лимита')
