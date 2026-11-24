"""Общие константы, статусы, лимиты и правила переходов.

Статусы хранятся в БД стабильными внутренними кодами (``new``, ``work``,
``wait``, ``done``, ``cancel``), а отображаемые подписи живут только в UI.
"""
from __future__ import annotations

CATEGORIES = ('Серверы', 'Разработка', 'Автоматизация', 'Другое')

# Код статуса -> подпись для пользователя. Порядок важен для фильтров.
STATUS_LABELS = {
    'new': 'Новая',
    'work': 'В работе',
    'wait': 'Ожидает ответа',
    'done': 'Выполнена',
    'cancel': 'Отменена',
}
STATUS_CODES = tuple(STATUS_LABELS)
TERMINAL = frozenset(('done', 'cancel'))
EDITABLE = frozenset(('new', 'wait'))

# Явные правила переходов для администратора. Из терминальных статусов
# переходов нет; назад в «Новую» заявка не возвращается.
ALLOWED_TRANSITIONS = {
    'new': frozenset(('work', 'wait', 'done', 'cancel')),
    'work': frozenset(('wait', 'done', 'cancel')),
    'wait': frozenset(('work', 'done', 'cancel')),
    'done': frozenset(),
    'cancel': frozenset(),
}
# Порядок кнопок смены статуса в карточке администратора.
STATUS_BUTTON_ORDER = ('work', 'wait', 'done', 'cancel')

PAGE_SIZE = 5
HISTORY_PAGE_SIZE = 3
MIN_DESCRIPTION = 10
MAX_DESCRIPTION = 500
MAX_REPLY = 1500
MAX_HISTORY_BODY = 700
MAX_PAGE = 100_000
MAX_CALLBACK_ID = 10**18

# Rate limiting: действие -> (сколько операций, окно в секундах).
# Лимиты применяются к операциям, которые пишут в БД и рассылают уведомления.
RATE_LIMITS = {
    'create': (3, 3600),
    'reply': (10, 60),
    'edit': (5, 600),
    'cancel': (5, 600),
    'status': (30, 60),
}


def status_label(code: str) -> str:
    """Человекочитаемая подпись статуса; неизвестный код возвращается как есть."""
    return STATUS_LABELS.get(code, code)


def can_transition(old_status: str, new_status: str) -> bool:
    """Разрешён ли переход old_status -> new_status."""
    return new_status in ALLOWED_TRANSITIONS.get(old_status, frozenset())


def allowed_statuses(current: str) -> tuple[str, ...]:
    """Статусы, в которые администратор может перевести заявку."""
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    return tuple(code for code in STATUS_BUTTON_ORDER if code in allowed)
