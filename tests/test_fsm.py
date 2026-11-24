"""Тесты FSM: прерывание, повторные команды и некорректный ввод."""
from __future__ import annotations

from support import CLIENT_ID, OTHER_ID, AppTestCase

from handlers import client, common
from states import EditRequest, NewRequest


class FsmTests(AppTestCase):
    async def test_cancel_interrupts_request_creation(self):
        state = self.state()
        await client.create_form(self.message('📝 Создать заявку'), state)
        await client.category_selected(self.message('Серверы'), state)
        await state.update_data(description='Описание, которое не должно сохраниться')
        await common.cancel(self.message('/cancel'), state)
        self.assertIsNone(await state.get_state())
        self.assertEqual((await state.get_data()), {})

    async def test_invalid_input_keeps_state(self):
        state = self.state()
        await state.set_state(NewRequest.category)
        await client.category_invalid(self.message('не категория'))
        self.assertEqual(await state.get_state(), NewRequest.category.state)

        await state.set_state(NewRequest.description)
        await client.description_received(self.message('коротко'), state)
        self.assertEqual(await state.get_state(), NewRequest.description.state)
        self.assertNotIn('description', await state.get_data())

    async def test_help_does_not_reset_active_state(self):
        state = self.state()
        await state.set_state(NewRequest.description)
        await common.help_command(self.message('/help'))
        self.assertEqual(await state.get_state(), NewRequest.description.state)

    async def test_repeated_start_clears_edit_state(self):
        state = self.state()
        await state.set_state(EditRequest.category)
        await state.update_data(request_id=1)
        await common.start(self.message('/start'), state)
        self.assertIsNone(await state.get_state())
        self.assertEqual(await state.get_data(), {})

    async def test_edit_state_survives_invalid_category(self):
        state = self.state()
        await state.set_state(EditRequest.category)
        await client.invalid_edit_category(self.message('не категория'))
        self.assertEqual(await state.get_state(), EditRequest.category.state)
        self.assertIn('Выберите категорию', self.bot.last_sent_text())

    async def test_states_are_isolated_by_user(self):
        first = self.state(CLIENT_ID)
        second = self.state(OTHER_ID)
        await first.set_state(NewRequest.category)
        await second.set_state(EditRequest.description)
        self.assertEqual(await first.get_state(), NewRequest.category.state)
        self.assertEqual(await second.get_state(), EditRequest.description.state)
        await first.clear()
        self.assertIsNone(await first.get_state())
        self.assertEqual(await second.get_state(), EditRequest.description.state)
