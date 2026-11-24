"""SQLite-слой: версионируемые миграции, транзакции и защищённые операции.

Схема версионируется через ``PRAGMA user_version``:

* v1 — исходная схема с русскими статусами в ``requests.status``;
* v2 — стабильные коды статусов, CHECK-ограничение и WAL;
* v3 — журнал административных действий ``admin_actions``.

Все операции записи выполняются в ``BEGIN IMMEDIATE``, чтение — вне
транзакции. Это сохраняет защиту от гонок при нескольких администраторах.
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

import config
from models import (
    EDITABLE,
    HISTORY_PAGE_SIZE,
    MAX_REPLY,
    PAGE_SIZE,
    STATUS_CODES,
    STATUS_LABELS,
    TERMINAL,
    can_transition,
)

SCHEMA_VERSION = 3

_REQUESTS_TABLE = '''
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new'
            CHECK(status IN ('new', 'work', 'wait', 'done', 'cancel')),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
'''

_REQUESTS_TABLE_V2 = '''
    CREATE TABLE requests_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new'
            CHECK(status IN ('new', 'work', 'wait', 'done', 'cancel')),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
'''

_MESSAGES_TABLE = '''
    CREATE TABLE IF NOT EXISTS request_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
        sender_id INTEGER NOT NULL,
        sender_role TEXT NOT NULL CHECK(sender_role IN ('client', 'admin')),
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
'''

_ADMIN_ACTIONS_TABLE = '''
    CREATE TABLE IF NOT EXISTS admin_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
        details TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
'''

_INDEXES_BASE = (
    'CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id, id DESC)',
    'CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status, id DESC)',
    'CREATE INDEX IF NOT EXISTS idx_messages_request ON request_messages(request_id, id DESC)',
)
_INDEXES_ADMIN = (
    'CREATE INDEX IF NOT EXISTS idx_admin_actions_admin ON admin_actions(admin_id, id DESC)',
    'CREATE INDEX IF NOT EXISTS idx_admin_actions_request ON admin_actions(request_id, id DESC)',
)
_INDEXES = _INDEXES_BASE + _INDEXES_ADMIN


def as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


@asynccontextmanager
async def connect():
    """Соединение с autocommit; транзакции открываются явно."""
    async with aiosqlite.connect(
        config.DATABASE_PATH,
        timeout=10,
        isolation_level=None,
    ) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('PRAGMA foreign_keys=ON')
        await db.execute('PRAGMA busy_timeout=10000')
        await db.execute('PRAGMA synchronous=NORMAL')
        yield db


async def _fetchone(db: aiosqlite.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def _fetchall(db: aiosqlite.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    async with db.execute(sql, params) as cursor:
        return list(await cursor.fetchall())


async def _scalar(db: aiosqlite.Connection, sql: str, params: tuple = ()) -> Any:
    row = await _fetchone(db, sql, params)
    return row[0] if row is not None else None


async def _user_version(db: aiosqlite.Connection) -> int:
    return int(await _scalar(db, 'PRAGMA user_version') or 0)


async def _set_user_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute(f'PRAGMA user_version={int(version)}')


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    row = await _fetchone(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return row is not None


async def _enable_wal(db: aiosqlite.Connection) -> None:
    async with db.execute('PRAGMA journal_mode=WAL') as cursor:
        await cursor.fetchone()


async def _check_foreign_keys(db: aiosqlite.Connection) -> None:
    violations = await _fetchall(db, 'PRAGMA foreign_key_check')
    if violations:
        raise RuntimeError(f'Нарушены внешние ключи SQLite: {len(violations)} записей')


async def _create_indexes(
    db: aiosqlite.Connection,
    statements: tuple[str, ...] = _INDEXES,
) -> None:
    for statement in statements:
        await db.execute(statement)


async def _create_fresh_schema(db: aiosqlite.Connection) -> None:
    await db.execute('BEGIN IMMEDIATE')
    try:
        await db.execute(_REQUESTS_TABLE)
        await db.execute(_MESSAGES_TABLE)
        await db.execute(_ADMIN_ACTIONS_TABLE)
        await _create_indexes(db)
        await _set_user_version(db, SCHEMA_VERSION)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    """Перевести русские статусы в коды, пересоздав таблицу с CHECK."""
    await db.execute('PRAGMA foreign_keys=OFF')
    try:
        await db.execute('BEGIN IMMEDIATE')
        try:
            await db.execute(_MESSAGES_TABLE)
            labels = tuple(STATUS_LABELS.values())
            placeholders = ','.join('?' for _ in labels)
            invalid = await _scalar(
                db,
                f'SELECT COUNT(*) FROM requests WHERE status NOT IN ({placeholders})',
                labels,
            )
            if invalid:
                raise RuntimeError(
                    'Миграция остановлена: в requests есть заявки с неизвестным статусом '
                    f'({invalid} шт.). Данные не изменены; проверьте статусы вручную.'
                )
            case_sql = ' '.join(
                f"WHEN '{label}' THEN '{code}'" for code, label in STATUS_LABELS.items()
            )
            await db.execute(_REQUESTS_TABLE_V2)
            await db.execute(f'''
                INSERT INTO requests_new(id, user_id, category, description, status, created_at)
                SELECT id, user_id, category, description,
                       CASE status {case_sql} ELSE status END,
                       created_at
                FROM requests
            ''')
            await db.execute('DROP TABLE requests')
            await db.execute('ALTER TABLE requests_new RENAME TO requests')
            await _create_indexes(db, _INDEXES_BASE)
            await _set_user_version(db, 2)
            await _check_foreign_keys(db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    finally:
        await db.execute('PRAGMA foreign_keys=ON')


async def _migrate_v2_to_v3(db: aiosqlite.Connection) -> None:
    await db.execute('BEGIN IMMEDIATE')
    try:
        await db.execute(_ADMIN_ACTIONS_TABLE)
        await _create_indexes(db)
        await _set_user_version(db, 3)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def init_db() -> None:
    """Создать или безопасно обновить схему; операция идемпотентна."""
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with connect() as db:
        await _enable_wal(db)
        version = await _user_version(db)
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f'Версия схемы {version} новее поддерживаемой {SCHEMA_VERSION}; '
                'обновите приложение.'
            )
        has_requests = await _table_exists(db, 'requests')
        if version == 0 and not has_requests:
            await _create_fresh_schema(db)
            await _check_foreign_keys(db)
            return
        if version == 0 and has_requests:
            version = 1
        if version == 1:
            await _migrate_v1_to_v2(db)
            version = 2
        if version == 2:
            await _migrate_v2_to_v3(db)
            version = 3
        await _create_indexes(db)
        await _check_foreign_keys(db)


async def create_request(user_id: int, category: str, description: str) -> int:
    async with connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        try:
            cursor = await db.execute(
                'INSERT INTO requests(user_id,category,description,status) VALUES (?,?,?,?)',
                (user_id, category, description, 'new'),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)


async def get_request(request_id: int) -> dict | None:
    async with connect() as db:
        return as_dict(await _fetchone(db, 'SELECT * FROM requests WHERE id=?', (request_id,)))


async def list_user_requests(user_id: int, page: int = 0) -> tuple[list[dict], bool]:
    page = max(0, int(page))
    async with connect() as db:
        rows = await _fetchall(
            db,
            'SELECT * FROM requests WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',
            (user_id, PAGE_SIZE + 1, page * PAGE_SIZE),
        )
    results = [dict(row) for row in rows]
    return results[:PAGE_SIZE], len(results) > PAGE_SIZE


async def list_admin_requests(status_code: str, page: int = 0) -> tuple[list[dict], bool]:
    if status_code != 'all' and status_code not in STATUS_CODES:
        raise ValueError('Неизвестный фильтр')
    page = max(0, int(page))
    where = '' if status_code == 'all' else 'WHERE status=?'
    params: tuple = () if status_code == 'all' else (status_code,)
    async with connect() as db:
        rows = await _fetchall(
            db,
            f'SELECT * FROM requests {where} ORDER BY id DESC LIMIT ? OFFSET ?',
            (*params, PAGE_SIZE + 1, page * PAGE_SIZE),
        )
    results = [dict(row) for row in rows]
    return results[:PAGE_SIZE], len(results) > PAGE_SIZE


async def _insert_admin_action(
    db: aiosqlite.Connection,
    admin_id: int,
    action: str,
    request_id: int,
    details: str | None = None,
) -> None:
    await db.execute(
        'INSERT INTO admin_actions(admin_id,action,request_id,details) VALUES (?,?,?,?)',
        (admin_id, action, request_id, details),
    )


async def set_status(
    request_id: int,
    new_status: str,
    admin_id: int | None = None,
) -> tuple[str, dict | None]:
    """Сменить статус с проверкой перехода; вернуть исход операции."""
    if new_status not in STATUS_LABELS:
        raise ValueError('Неизвестный статус')
    async with connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        try:
            item = as_dict(await _fetchone(db, 'SELECT * FROM requests WHERE id=?', (request_id,)))
            if item is None:
                await db.commit()
                return 'missing', None
            old_status = item['status']
            if old_status in TERMINAL:
                await db.commit()
                return 'closed', item
            if old_status == new_status:
                await db.commit()
                return 'same', item
            if not can_transition(old_status, new_status):
                await db.commit()
                return 'invalid', item
            await db.execute('UPDATE requests SET status=? WHERE id=?', (new_status, request_id))
            if admin_id is not None:
                await _insert_admin_action(
                    db, admin_id, 'status_change', request_id, f'{old_status}->{new_status}'
                )
            item['status'] = new_status
            await db.commit()
            return 'updated', item
        except Exception:
            await db.rollback()
            raise


async def cancel_request(request_id: int, user_id: int) -> tuple[str, dict | None]:
    """Отменить заявку владельцем; чужая заявка выглядит как отсутствующая."""
    async with connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        try:
            item = as_dict(await _fetchone(db, 'SELECT * FROM requests WHERE id=?', (request_id,)))
            if item is None or item['user_id'] != user_id:
                await db.commit()
                return 'missing', None
            if item['status'] in TERMINAL:
                await db.commit()
                return 'closed', item
            await db.execute(
                'UPDATE requests SET status=? WHERE id=? AND user_id=?',
                ('cancel', request_id, user_id),
            )
            item['status'] = 'cancel'
            await db.commit()
            return 'updated', item
        except Exception:
            await db.rollback()
            raise


async def edit_request(
    request_id: int,
    user_id: int,
    field: str,
    value: str,
) -> tuple[bool, dict | None]:
    if field not in ('category', 'description'):
        raise ValueError('Некорректное поле')
    async with connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        try:
            item = as_dict(await _fetchone(db, 'SELECT * FROM requests WHERE id=?', (request_id,)))
            if item is None or item['user_id'] != user_id:
                await db.commit()
                return False, None
            if item['status'] not in EDITABLE:
                await db.commit()
                return False, item
            await db.execute(
                f'UPDATE requests SET {field}=? WHERE id=? AND user_id=?',
                (value, request_id, user_id),
            )
            item[field] = value
            await db.commit()
            return True, item
        except Exception:
            await db.rollback()
            raise


async def add_reply(
    request_id: int,
    sender_id: int,
    role: str,
    body: str,
) -> tuple[str, dict | None]:
    """Проверить владельца/статус и записать сообщение в одной транзакции."""
    if role not in ('client', 'admin'):
        raise ValueError('Некорректная роль')
    if not 1 <= len(body) <= MAX_REPLY:
        raise ValueError('Некорректная длина сообщения')
    async with connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        try:
            item = as_dict(await _fetchone(db, 'SELECT * FROM requests WHERE id=?', (request_id,)))
            if item is None or (role == 'client' and item['user_id'] != sender_id):
                await db.commit()
                return 'missing', None
            if item['status'] in TERMINAL:
                await db.commit()
                return 'closed', item
            await db.execute(
                'INSERT INTO request_messages(request_id,sender_id,sender_role,body) VALUES (?,?,?,?)',
                (request_id, sender_id, role, body),
            )
            if role == 'client' and item['status'] == 'wait':
                await db.execute('UPDATE requests SET status=? WHERE id=?', ('work', request_id))
                item['status'] = 'work'
            if role == 'admin':
                await _insert_admin_action(db, sender_id, 'reply', request_id)
            await db.commit()
            return 'saved', item
        except Exception:
            await db.rollback()
            raise


async def history(
    request_id: int,
    page: int = 0,
    max_id: int | None = None,
) -> tuple[list[dict], bool, int]:
    """Одна страница истории, от новых к старым.

    ``max_id`` фиксирует снимок на момент открытия истории: новые сообщения
    не сдвигают страницы. Возвращается также эффективный ``max_id``.
    """
    page = max(0, int(page))
    async with connect() as db:
        if max_id is None:
            max_id = int(await _scalar(
                db, 'SELECT COALESCE(MAX(id), 0) FROM request_messages WHERE request_id=?', (request_id,)
            ) or 0)
        max_id = max(0, int(max_id))
        rows = await _fetchall(
            db,
            'SELECT * FROM request_messages WHERE request_id=? AND id<=? '
            'ORDER BY id DESC LIMIT ? OFFSET ?',
            (request_id, max_id, HISTORY_PAGE_SIZE + 1, page * HISTORY_PAGE_SIZE),
        )
    results = [dict(row) for row in rows]
    return results[:HISTORY_PAGE_SIZE], len(results) > HISTORY_PAGE_SIZE, max_id


async def admin_actions(request_id: int | None = None, limit: int = 100) -> list[dict]:
    """Журнал административных действий (для проверки и отчётности)."""
    limit = max(1, min(int(limit), 1000))
    async with connect() as db:
        if request_id is None:
            rows = await _fetchall(
                db, 'SELECT * FROM admin_actions ORDER BY id DESC LIMIT ?', (limit,)
            )
        else:
            rows = await _fetchall(
                db,
                'SELECT * FROM admin_actions WHERE request_id=? ORDER BY id DESC LIMIT ?',
                (request_id, limit),
            )
    return [dict(row) for row in rows]
