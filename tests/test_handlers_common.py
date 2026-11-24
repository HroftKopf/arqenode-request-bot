"""Тесты глобальных команд."""
from __future__ import annotations

from support import ADMIN_ID, CLIENT_ID, AppTestCase

from handlers import common
from states import NewRequest


class CommonHandlerTests(AppTestCase):
    async def test_start_clears_state_and_shows_client_menu(self):
        state = self.state()
        await state.set_state(NewRequest.category)
        await common.start(self.message('/start'), state)
        self.assertIsNone(await state.get_state())
        self.assertIn('Добро пожаловать', self.bot.last_sent_text())

    async def test_start_admin_menu_contains_admin_button(self):
        await common.start(self.message('/start', user_id=ADMIN_ID), self.state(ADMIN_ID))
        markup = self.bot.sent[-1]['reply_markup']
        texts = [[button.text for button in row] for row in markup.keyboard]
        self.assertIn(['🛠 Админ-панель'], texts)
        self.assertIn('Для администратора', self.bot.last_sent_text())

    async def test_help(self):
        await common.help_command(self.message('❓ Помощь'))
        self.assertIn('Как пользоваться', self.bot.last_sent_text())

    async def test_show_id(self):
        await common.show_id(self.message('/id', user_id=CLIENT_ID))
        self.assertIn(str(CLIENT_ID), self.bot.last_sent_text())

    async def test_cancel_clears_active_state(self):
        state = self.state()
        await state.set_state(NewRequest.description)
        await common.cancel(self.message('/cancel'), state)
        self.assertIsNone(await state.get_state())
        self.assertIn('Текущий ввод отменён', self.bot.last_sent_text())

    async def test_cancel_without_state(self):
        state = self.state()
        await common.cancel(self.message('❌ Отмена'), state)
        self.assertIn('нечего отменять', self.bot.last_sent_text())
