"""Тесты SQLite-слоя: CRUD, права, статусы, аудит, история, гонки."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
import database as db


class DatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temp.name) / 'requests.db'
        await db.init_db()

    async def asyncTearDown(self):
        config.DATABASE_PATH = self.original_path
        self.temp.cleanup()


class SchemaTests(DatabaseTestCase):
    async def test_fresh_schema_is_versioned_and_uses_wal(self):
        async with db.connect() as conn:
            version = (await (await conn.execute('PRAGMA user_version')).fetchone())[0]
            journal = (await (await conn.execute('PRAGMA journal_mode')).fetchone())[0]
            tables = {
                row[0] for row in await (await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )).fetchall()
            }
            indexes = {
                row[0] for row in await (await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                )).fetchall()
            }
        self.assertEqual(version, db.SCHEMA_VERSION)
        self.assertEqual(journal.lower(), 'wal')
        self.assertTrue({'requests', 'request_messages', 'admin_actions'} <= tables)
        self.assertTrue({
            'idx_requests_user', 'idx_requests_status', 'idx_messages_request',
            'idx_admin_actions_admin', 'idx_admin_actions_request',
        } <= indexes)

    async def test_create_request_uses_code_status_and_defaults(self):
        rid = await db.create_request(456, 'Серверы', 'Настроить сервер и HTTPS')
        item = await db.get_request(rid)
        self.assertEqual(item['status'], 'new')
        self.assertIsNotNone(item['created_at'])
        self.assertEqual(item['user_id'], 456)
        self.assertEqual(item['category'], 'Серверы')

    async def test_as_dict(self):
        self.assertIsNone(db.as_dict(None))
        async with db.connect() as conn:
            row = await (await conn.execute('SELECT 1 AS value')).fetchone()
        self.assertEqual(db.as_dict(row), {'value': 1})


class CrudTests(DatabaseTestCase):
    async def test_user_list_pagination(self):
        for index in range(7):
            await db.create_request(456, 'Другое', f'Заявка номер {index}')
        page0, more0 = await db.list_user_requests(456)
        page1, more1 = await db.list_user_requests(456, 1)
        self.assertEqual(len(page0), 5)
        self.assertTrue(more0)
        self.assertEqual(len(page1), 2)
        self.assertFalse(more1)
        self.assertEqual((await db.list_user_requests(999))[0], [])
        # Отрицательная страница трактуется как первая.
        self.assertEqual(len((await db.list_user_requests(456, -5))[0]), 5)

    async def test_admin_filters(self):
        first = await db.create_request(1, 'Серверы', 'Первая заявка для фильтра')
        second = await db.create_request(2, 'Разработка', 'Вторая заявка для фильтра')
        await db.set_status(first, 'work')
        await db.set_status(second, 'done')

        all_items, more = await db.list_admin_requests('all')
        self.assertEqual(len(all_items), 2)
        self.assertFalse(more)
        work_items, _ = await db.list_admin_requests('work')
        self.assertEqual([item['id'] for item in work_items], [first])
        done_items, _ = await db.list_admin_requests('done')
        self.assertEqual([item['id'] for item in done_items], [second])
        with self.assertRaises(ValueError):
            await db.list_admin_requests('DROP TABLE requests')


class StatusTests(DatabaseTestCase):
    async def test_transitions_and_audit(self):
        rid = await db.create_request(456, 'Автоматизация', 'Автоматизировать отчёты')
        self.assertEqual((await db.set_status(rid, 'new'))[0], 'same')
        self.assertEqual((await db.set_status(rid, 'work', admin_id=77))[0], 'updated')
        self.assertEqual((await db.set_status(rid, 'new', admin_id=77))[0], 'invalid')
        self.assertEqual((await db.set_status(rid, 'wait', admin_id=77))[0], 'updated')
        self.assertEqual((await db.set_status(rid, 'done', admin_id=78))[0], 'updated')
        self.assertEqual((await db.set_status(rid, 'cancel', admin_id=78))[0], 'closed')
        self.assertEqual((await db.get_request(rid))['status'], 'done')
        actions = await db.admin_actions(rid)
        self.assertEqual([action['action'] for action in actions], ['status_change'] * 3)
        self.assertEqual(actions[0]['admin_id'], 78)
        self.assertEqual(actions[0]['details'], 'wait->done')
        with self.assertRaises(ValueError):
            await db.set_status(rid, 'unknown')

    async def test_missing_request(self):
        self.assertEqual(await db.set_status(999, 'work'), ('missing', None))

    async def test_cancel_only_owner_and_only_open(self):
        rid = await db.create_request(456, 'Другое', 'Заявка для отмены владельцем')
        self.assertEqual((await db.cancel_request(rid, 999))[0], 'missing')
        outcome, item = await db.cancel_request(rid, 456)
        self.assertEqual(outcome, 'updated')
        self.assertEqual(item['status'], 'cancel')
        self.assertEqual((await db.cancel_request(rid, 456))[0], 'closed')
        self.assertEqual((await db.cancel_request(999, 456))[0], 'missing')
        self.assertFalse((await db.edit_request(rid, 456, 'description', 'Новое описание'))[0])

    async def test_edit_permissions_and_statuses(self):
        rid = await db.create_request(456, 'Серверы', 'Исходное описание заявки')
        self.assertEqual(await db.edit_request(rid, 999, 'category', 'Другое'), (False, None))
        self.assertTrue((await db.edit_request(rid, 456, 'category', 'Другое'))[0])
        self.assertTrue((await db.edit_request(rid, 456, 'description', 'Обновлённое описание'))[0])
        await db.set_status(rid, 'work')
        ok, item = await db.edit_request(rid, 456, 'description', 'Ещё одно описание')
        self.assertFalse(ok)
        self.assertEqual(item['status'], 'work')
        with self.assertRaises(ValueError):
            await db.edit_request(rid, 456, 'status', 'done')
        await db.set_status(rid, 'wait')
        self.assertTrue((await db.edit_request(rid, 456, 'description', 'Можно менять в ожидании'))[0])


class ReplyHistoryTests(DatabaseTestCase):
    async def test_reply_permissions_status_transition_and_audit(self):
        rid = await db.create_request(456, 'Разработка', 'Заявка с перепиской')
        await db.set_status(rid, 'wait')
        self.assertEqual((await db.add_reply(rid, 999, 'client', 'Чужое сообщение'))[0], 'missing')
        self.assertEqual((await db.add_reply(rid, 456, 'client', 'Исходные данные'))[0], 'saved')
        self.assertEqual((await db.get_request(rid))['status'], 'work')
        self.assertEqual((await db.add_reply(rid, 777, 'admin', 'Ответ администратора'))[0], 'saved')
        actions = await db.admin_actions(rid)
        self.assertEqual(actions[0]['action'], 'reply')
        self.assertEqual(actions[0]['admin_id'], 777)
        await db.set_status(rid, 'done')
        self.assertEqual((await db.add_reply(rid, 456, 'client', 'Позднее сообщение'))[0], 'closed')
        self.assertEqual((await db.add_reply(999, 456, 'client', 'Нет заявки'))[0], 'missing')
        with self.assertRaises(ValueError):
            await db.add_reply(rid, 456, 'unknown', 'текст')
        with self.assertRaises(ValueError):
            await db.add_reply(rid, 456, 'client', '')
        with self.assertRaises(ValueError):
            await db.add_reply(rid, 456, 'client', 'x' * 1501)

    async def test_history_is_newest_first_with_snapshot_pages(self):
        rid = await db.create_request(456, 'Другое', 'Заявка с длинной перепиской')
        for index in range(7):
            await db.add_reply(rid, 456, 'client', f'msg{index}')
        page0, more0, max_id = await db.history(rid, 0)
        self.assertEqual([row['body'] for row in page0], ['msg6', 'msg5', 'msg4'])
        self.assertTrue(more0)
        self.assertGreater(max_id, 0)
        await db.add_reply(rid, 456, 'client', 'new-between-pages')
        page1, more1, same_max_id = await db.history(rid, 1, max_id)
        self.assertEqual([row['body'] for row in page1], ['msg3', 'msg2', 'msg1'])
        self.assertTrue(more1)
        self.assertEqual(same_max_id, max_id)
        page2, more2, _ = await db.history(rid, 2, max_id)
        self.assertEqual([row['body'] for row in page2], ['msg0'])
        self.assertFalse(more2)
        page0_again, _, _ = await db.history(rid, 0)
        self.assertEqual(page0_again[0]['body'], 'new-between-pages')

    async def test_history_empty(self):
        rid = await db.create_request(456, 'Другое', 'Заявка без сообщений')
        rows, more, max_id = await db.history(rid)
        self.assertEqual(rows, [])
        self.assertFalse(more)
        self.assertEqual(max_id, 0)


class ConcurrencyTests(DatabaseTestCase):
    async def test_two_admins_close_same_request_only_once(self):
        rid = await db.create_request(456, 'Другое', 'Заявка для гонки статусов')
        results = await asyncio.gather(
            db.set_status(rid, 'done', admin_id=1),
            db.set_status(rid, 'done', admin_id=2),
        )
        outcomes = sorted(outcome for outcome, _ in results)
        self.assertEqual(outcomes, ['closed', 'updated'])
        self.assertEqual((await db.get_request(rid))['status'], 'done')
        actions = await db.admin_actions(rid)
        self.assertEqual(len(actions), 1)

    async def test_reply_and_cancel_do_not_corrupt_state(self):
        rid = await db.create_request(456, 'Другое', 'Заявка для гонки ответа и отмены')
        reply, cancel = await asyncio.gather(
            db.add_reply(rid, 456, 'client', 'Сообщение во время отмены'),
            db.cancel_request(rid, 456),
        )
        self.assertIn(reply[0], ('saved', 'closed'))
        self.assertEqual(cancel[0], 'updated')
        self.assertEqual((await db.get_request(rid))['status'], 'cancel')
        _, _, max_id = await db.history(rid)
        self.assertGreaterEqual(max_id, 0)

    async def test_foreign_keys_cascade_on_delete(self):
        rid = await db.create_request(456, 'Другое', 'Заявка для проверки каскада')
        await db.add_reply(rid, 456, 'client', 'Сообщение для каскада')
        await db.set_status(rid, 'work', admin_id=1)
        async with db.connect() as conn:
            await conn.execute('DELETE FROM requests WHERE id=?', (rid,))
            left_messages = (await (await conn.execute(
                'SELECT COUNT(*) FROM request_messages WHERE request_id=?', (rid,)
            )).fetchone())[0]
            left_actions = (await (await conn.execute(
                'SELECT COUNT(*) FROM admin_actions WHERE request_id=?', (rid,)
            )).fetchone())[0]
            violations = await (await conn.execute('PRAGMA foreign_key_check')).fetchall()
        self.assertEqual(left_messages, 0)
        self.assertEqual(left_actions, 0)
        self.assertEqual(violations, [])

    async def test_database_unavailable_raises_clear_sqlite_error(self):
        config.DATABASE_PATH = Path(self.temp.name) / 'directory-instead-of-db'
        config.DATABASE_PATH.mkdir()
        with self.assertRaises(sqlite3.Error):
            await db.get_request(1)


class AdminActionsQueryTests(DatabaseTestCase):
    async def test_admin_actions_without_request_filter(self):
        rid = await db.create_request(456, 'Другое', 'Заявка для журнала действий')
        await db.set_status(rid, 'work', admin_id=1)
        actions = await db.admin_actions()
        self.assertTrue(actions)
        self.assertEqual(actions[0]['request_id'], rid)


class ForeignKeyCheckTests(DatabaseTestCase):
    async def test_foreign_key_violation_is_detected(self):
        async with db.connect() as conn:
            await conn.execute('PRAGMA foreign_keys=OFF')
            await conn.execute(
                'INSERT INTO request_messages(request_id,sender_id,sender_role,body) '
                "VALUES (999,1,'client','осиротевшее сообщение')"
            )
            await conn.execute('PRAGMA foreign_keys=ON')
            with self.assertRaisesRegex(RuntimeError, 'внешние ключи'):
                await db._check_foreign_keys(conn)
