"""Reply- и inline-клавиатуры, а также callback-протокол."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton as Btn,
)
from aiogram.types import (
    InlineKeyboardMarkup as Inline,
)
from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from models import (
    CATEGORIES,
    EDITABLE,
    STATUS_LABELS,
    TERMINAL,
    allowed_statuses,
    status_label,
)

_STATUS_ICONS = {
    'work': '🔧',
    'wait': '⏳',
    'done': '✅',
    'cancel': '🚫',
}


def menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keys = [[KeyboardButton(text='📝 Создать заявку'), KeyboardButton(text='📋 Мои заявки')],
            [KeyboardButton(text='❓ Помощь')]]
    if is_admin:
        keys.insert(1, [KeyboardButton(text='🛠 Админ-панель')])
    return ReplyKeyboardMarkup(keyboard=keys, resize_keyboard=True)


def categories() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=c)] for c in CATEGORIES] + [[KeyboardButton(text='❌ Отмена')]],
        resize_keyboard=True,
    )


def confirmation() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='✅ Отправить'), KeyboardButton(text='❌ Отмена')]],
        resize_keyboard=True,
    )


def inline(rows: list[list[tuple[str, str]]]) -> Inline:
    return Inline(inline_keyboard=[[Btn(text=t, callback_data=d) for t, d in row] for row in rows])


def client_list(items: list[dict], page: int, has_more: bool) -> Inline:
    rows = [[(f"#{x['id']} · {status_label(x['status'])}", f"u:open:{x['id']}")] for x in items]
    nav = []
    if page > 0:
        nav.append(('◀️ Назад', f'u:list:{page - 1}'))
    if has_more:
        nav.append(('Далее ▶️', f'u:list:{page + 1}'))
    if nav:
        rows.append(nav)
    return inline(rows)


def client_detail(item: dict) -> Inline:
    rid = item['id']
    rows = [[('💬 Написать по заявке', f'u:reply:{rid}')]] if item['status'] not in TERMINAL else []
    rows.append([('📜 История переписки', f'u:history:{rid}:0')])
    if item['status'] in EDITABLE:
        rows.append([('✏️ Изменить заявку', f'u:edit:{rid}')])
    if item['status'] not in TERMINAL:
        rows.append([('🚫 Отменить заявку', f'u:cancel:{rid}')])
    rows.append([('◀️ Мои заявки', 'u:list:0')])
    return inline(rows)


def admin_filters() -> Inline:
    return inline([
        [('📋 Все', 'a:list:all:0'), ('🆕 Новые', 'a:list:new:0')],
        [('🔧 В работе', 'a:list:work:0'), ('⏳ Ждут ответа', 'a:list:wait:0')],
        [('✅ Выполнены', 'a:list:done:0'), ('🚫 Отменены', 'a:list:cancel:0')],
    ])


def admin_list(items: list[dict], status: str, page: int, has_more: bool) -> Inline:
    rows = [
        [(f"#{x['id']} · {x['category']} · {status_label(x['status'])}", f"a:open:{x['id']}")]
        for x in items
    ]
    nav = []
    if page:
        nav.append(('◀️ Назад', f'a:list:{status}:{page - 1}'))
    if has_more:
        nav.append(('Далее ▶️', f'a:list:{status}:{page + 1}'))
    if nav:
        rows.append(nav)
    rows.append([('🔎 Фильтры', 'a:menu')])
    return inline(rows)


def admin_detail(item: dict) -> Inline:
    rid = item['id']
    rows = []
    if item['status'] not in TERMINAL:
        rows.append([('💬 Ответить клиенту', f'a:reply:{rid}')])
        buttons = [
            (f'{_STATUS_ICONS.get(code, "")} {STATUS_LABELS[code]}'.strip(),
             f'a:status:{rid}:{code}')
            for code in allowed_statuses(item['status'])
        ]
        for n in range(0, len(buttons), 2):
            rows.append(buttons[n:n + 2])
    rows.append([('📜 Переписка', f'a:history:{rid}:0')])
    rows.append([('🔎 Все заявки / фильтры', 'a:menu')])
    return inline(rows)


def history_keyboard(
    role: str,
    rid: int,
    page: int,
    has_more: bool,
    max_id: int,
) -> Inline:
    prefix = 'a' if role == 'admin' else 'u'
    rows = []
    nav = []
    if page > 0:
        nav.append(('◀️ Новее', f'{prefix}:history:{rid}:{page - 1}:{max_id}'))
    if has_more:
        nav.append(('Старее ▶️', f'{prefix}:history:{rid}:{page + 1}:{max_id}'))
    if nav:
        rows.append(nav)
    rows.append([('◀️ К заявке', f'{prefix}:open:{rid}')])
    return inline(rows)
