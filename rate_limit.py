"""Простой in-memory rate limiter для операций, пишущих в БД.

Ограничитель намеренно не использует БД: при одном polling-процессе этого
достаточно, а после рестарта окна сбрасываются. Состояние ограничено по числу
ключей, чтобы активность многих пользователей не росла бесконечно.
"""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from models import RATE_LIMITS

ACTION_TITLES = {
    'create': 'создание заявок',
    'reply': 'отправка сообщений',
    'edit': 'редактирование заявок',
    'cancel': 'отмена заявок',
    'status': 'смена статусов',
}


class RateLimiter:
    """Скользящее окно: не более limit событий за window секунд на действие."""

    def __init__(
        self,
        rules: dict[str, tuple[int, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._rules = dict(rules or RATE_LIMITS)
        self._clock = clock
        self._events: dict[tuple[int, str], deque[float]] = {}
        self._max_keys = max(1, max_keys)
        self._max_window = max((window for _, window in self._rules.values()), default=1.0)

    @property
    def rules(self) -> dict[str, tuple[int, float]]:
        return dict(self._rules)

    @property
    def active_keys(self) -> int:
        return len(self._events)

    def check(self, user_id: int, action: str, now: float | None = None) -> float | None:
        """Вернуть None, если операция разрешена, иначе — секунды до повтора."""
        if action not in self._rules:
            raise KeyError(f'Неизвестное действие rate limit: {action}')
        limit, window = self._rules[action]
        current = self._clock() if now is None else now
        key = (int(user_id), action)
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
        cutoff = current - window
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= limit:
            return max(0.0, window - (current - events[0]))
        events.append(current)
        if len(self._events) > self._max_keys:
            self._prune(current)
        return None

    def _prune(self, now: float) -> None:
        """Удалить устаревшие и самые старые ключи при переполнении."""
        stale = [key for key, events in self._events.items()
                 if not events or events[-1] <= now - self._max_window]
        for key in stale:
            self._events.pop(key, None)
        overflow = len(self._events) - self._max_keys
        if overflow > 0:
            oldest = sorted(self._events, key=lambda key: self._events[key][-1] if self._events[key] else 0)
            for key in oldest[:overflow]:
                self._events.pop(key, None)

    def reset(self) -> None:
        self._events.clear()


_limiter = RateLimiter()


def set_limiter(limiter: RateLimiter) -> None:
    """Подменить глобальный ограничитель (используется в тестах)."""
    global _limiter
    _limiter = limiter


def get_limiter() -> RateLimiter:
    return _limiter


def check_limit(user_id: int, action: str, now: float | None = None) -> float | None:
    return _limiter.check(user_id, action, now=now)


def format_retry(seconds: float, action: str) -> str:
    """Понятное сообщение при превышении лимита, без служебных подробностей."""
    title = ACTION_TITLES.get(action, 'эта операция')
    wait = max(1, math.ceil(seconds))
    return f'⏳ Слишком часто: {title}. Попробуйте снова через {wait} с.'
