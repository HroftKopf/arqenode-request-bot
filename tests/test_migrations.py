"""Тесты версионируемых миграций SQLite и сохранности данных."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
import database as db
from models import STATUS_LABELS

LEGACY_REQUESTS = '''
    CREATE TABLE requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Новая',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
'''
LEGACY_MESSAGES = '''
    CREATE TABLE request_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
        sender_id INTEGER NOT NULL,
        sender_role TEXT NOT NULL CHECK(sender_role IN ('client', 'admin')),
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
'''


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_path = config.DATABASE_PATH
        self.path = Path(self.temp.name) / 'legacy.db'
        config.DATABASE_PATH = self.path

    async def asyncTearDown(self):
        config.DATABASE_PATH = self.original_path
        self.temp.cleanup()

    def _make_legacy(self, with_messages: bool = False) -> None:
        conn = sqlite3.connect(self.path)
        conn.execute(LEGACY_REQUESTS)
        if with_messages:
            conn.execute(LEGACY_MESSAGES)
        for index, (code, label) in enumerate(STATUS_LABELS.items(), start=1):
            conn.execute(
                'INSERT INTO requests(id,user_id,category,description,status,created_at) '
                'VALUES (?,?,?,?,?,?)',
                (index, 500 + index, 'Серверы', f'Старая заявка {code}', label,
                 f'2026-01-0{index} 00:00:00'),
            )
        if with_messages:
            conn.execute(
                'INSERT INTO request_messages(request_id,sender_id,sender_role,body,created_at) '
                'VALUES (1,501,?,?,?)',
                ('client', 'старое сообщение', '2026-01-01 01:00:00'),
            )
        conn.commit()
        conn.close()

    async def test_v1_without_messages_is_migrated_without_data_loss(self):
        self._make_legacy(with_messages=False)
        await db.init_db()
        async with db.connect() as conn:
            version = (await (await conn.execute('PRAGMA user_version')).fetchone())[0]
            rows = await (await conn.execute(
                'SELECT id,user_id,category,description,status,created_at FROM requests ORDER BY id'
            )).fetchall()
            messages_exists = (await (await conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='request_messages'"
            )).fetchone()) is not None
        self.assertEqual(version, db.SCHEMA_VERSION)
        self.assertTrue(messages_exists)
        self.assertEqual([row['status'] for row in rows], list(STATUS_LABELS))
        self.assertEqual(rows[0]['created_at'], '2026-01-01 00:00:00')
        self.assertEqual(rows[0]['user_id'], 501)
        new_id = await db.create_request(999, 'Другое', 'Заявка после миграции')
        self.assertGreater(new_id, len(STATUS_LABELS))

    async def test_v1_messages_are_preserved(self):
        self._make_legacy(with_messages=True)
        await db.init_db()
        async with db.connect() as conn:
            rows = await (await conn.execute(
                'SELECT request_id,sender_id,sender_role,body,created_at FROM request_messages'
            )).fetchall()
            violations = await (await conn.execute('PRAGMA foreign_key_check')).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['body'], 'старое сообщение')
        self.assertEqual(rows[0]['sender_role'], 'client')
        self.assertEqual(rows[0]['created_at'], '2026-01-01 01:00:00')
        self.assertEqual(violations, [])

    async def test_unknown_status_blocks_migration_and_preserves_data(self):
        conn = sqlite3.connect(self.path)
        conn.execute(LEGACY_REQUESTS)
        conn.execute(
            'INSERT INTO requests(user_id,category,description,status) VALUES (1,?,?,?)',
            ('Другое', 'Заявка со странным статусом', 'Странная'),
        )
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(RuntimeError, 'неизвестным статусом'):
            await db.init_db()
        conn = sqlite3.connect(self.path)
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        status = conn.execute('SELECT status FROM requests').fetchone()[0]
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertEqual(version, 0)
        self.assertEqual(status, 'Странная')
        self.assertIn('requests', tables)
        self.assertNotIn('requests_new', tables)

    async def test_v2_is_upgraded_to_v3(self):
        conn = sqlite3.connect(self.path)
        conn.execute('''CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','work','wait','done','cancel')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.execute(LEGACY_MESSAGES)
        conn.execute("INSERT INTO requests(user_id,category,description,status) VALUES (1,'Другое','v2 заявка','work')")
        conn.execute('PRAGMA user_version=2')
        conn.commit()
        conn.close()
        await db.init_db()
        async with db.connect() as conn:
            version = (await (await conn.execute('PRAGMA user_version')).fetchone())[0]
            actions_exists = (await (await conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='admin_actions'"
            )).fetchone()) is not None
            status = (await (await conn.execute('SELECT status FROM requests')).fetchone())[0]
        self.assertEqual(version, 3)
        self.assertTrue(actions_exists)
        self.assertEqual(status, 'work')

    async def test_newer_schema_is_rejected(self):
        conn = sqlite3.connect(self.path)
        conn.execute(LEGACY_REQUESTS)
        conn.execute('PRAGMA user_version=99')
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(RuntimeError, 'новее поддерживаемой'):
            await db.init_db()

    async def test_init_db_is_idempotent(self):
        self._make_legacy(with_messages=True)
        await db.init_db()
        await db.init_db()
        async with db.connect() as conn:
            version = (await (await conn.execute('PRAGMA user_version')).fetchone())[0]
            count = (await (await conn.execute('SELECT COUNT(*) FROM requests')).fetchone())[0]
        self.assertEqual(version, db.SCHEMA_VERSION)
        self.assertEqual(count, len(STATUS_LABELS))
