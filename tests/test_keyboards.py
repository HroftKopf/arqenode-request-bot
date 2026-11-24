"""Тесты клавиатур и callback-протокола."""
from __future__ import annotations

import unittest

from support import button_rows, callback_data

from keyboards import (
    admin_detail,
    admin_filters,
    admin_list,
    categories,
    client_detail,
    client_list,
    confirmation,
    history_keyboard,
    menu,
)


def item(status: str = 'new', request_id: int = 5) -> dict:
    return {
        'id': request_id,
        'user_id': 1001,
        'category': 'Серверы',
        'description': 'Описание задачи достаточной длины',
        'status': status,
        'created_at': '2026-01-01 00:00:00',
    }


class KeyboardTests(unittest.TestCase):
    def test_menu_client_and_admin(self):
        client_rows = [[b.text for b in row] for row in menu().keyboard]
        admin_rows = [[b.text for b in row] for row in menu(True).keyboard]
        self.assertEqual(client_rows, [['📝 Создать заявку', '📋 Мои заявки'], ['❓ Помощь']])
        self.assertIn(['🛠 Админ-панель'], admin_rows)
        self.assertEqual(len(admin_rows), 3)

    def test_categories_and_confirmation(self):
        rows = [[b.text for b in row] for row in categories().keyboard]
        self.assertEqual(rows[-1], ['❌ Отмена'])
        self.assertEqual(len(rows), 5)
        confirm = [[b.text for b in row] for row in confirmation().keyboard]
        self.assertEqual(confirm, [['✅ Отправить', '❌ Отмена']])

    def test_client_list_pagination(self):
        markup = client_list([item('new'), item('done', 6)], page=1, has_more=True)
        data = callback_data(markup)
        self.assertIn('u:open:5', data)
        self.assertIn('u:list:0', data)
        self.assertIn('u:list:2', data)
        self.assertIn('Новая', button_rows(markup)[0][0][0])

    def test_client_detail_new_allows_reply_edit_cancel(self):
        data = callback_data(client_detail(item('new')))
        self.assertIn('u:reply:5', data)
        self.assertIn('u:edit:5', data)
        self.assertIn('u:cancel:5', data)
        self.assertIn('u:history:5:0', data)
        self.assertIn('u:list:0', data)

    def test_client_detail_terminal_hides_mutations(self):
        data = callback_data(client_detail(item('done')))
        self.assertNotIn('u:reply:5', data)
        self.assertNotIn('u:edit:5', data)
        self.assertNotIn('u:cancel:5', data)
        self.assertIn('u:history:5:0', data)

    def test_admin_filters_and_list(self):
        self.assertEqual(
            set(callback_data(admin_filters())),
            {'a:list:all:0', 'a:list:new:0', 'a:list:work:0',
             'a:list:wait:0', 'a:list:done:0', 'a:list:cancel:0'},
        )
        markup = admin_list([item('work')], 'work', page=1, has_more=True)
        data = callback_data(markup)
        self.assertIn('a:open:5', data)
        self.assertIn('a:list:work:0', data)
        self.assertIn('a:list:work:2', data)
        self.assertIn('a:menu', data)
        self.assertIn('В работе', button_rows(markup)[0][0][0])

    def test_admin_detail_only_allowed_transitions(self):
        new_data = callback_data(admin_detail(item('new')))
        self.assertIn('a:reply:5', new_data)
        self.assertIn('a:status:5:work', new_data)
        self.assertIn('a:status:5:done', new_data)
        self.assertIn('a:status:5:cancel', new_data)
        self.assertNotIn('a:status:5:new', new_data)

        work_data = callback_data(admin_detail(item('work')))
        self.assertNotIn('a:status:5:new', work_data)
        self.assertIn('a:status:5:wait', work_data)

        done_data = callback_data(admin_detail(item('done')))
        self.assertNotIn('a:reply:5', done_data)
        self.assertFalse(any(':status:' in data for data in done_data))
        self.assertIn('a:history:5:0', done_data)

    def test_history_keyboard_keeps_snapshot_max_id(self):
        markup = history_keyboard('client', 5, page=1, has_more=True, max_id=42)
        data = callback_data(markup)
        self.assertIn('u:history:5:0:42', data)
        self.assertIn('u:history:5:2:42', data)
        self.assertIn('u:open:5', data)
        self.assertTrue(all(len(value) <= 64 for value in data))

    def test_history_keyboard_admin_prefix(self):
        data = callback_data(history_keyboard('admin', 5, page=0, has_more=False, max_id=7))
        self.assertEqual(data, ['a:open:5'])
