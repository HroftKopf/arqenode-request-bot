"""Тесты ограничителя частоты операций."""
from __future__ import annotations

import unittest

import rate_limit
from rate_limit import RateLimiter, format_retry


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.limiter = RateLimiter(
            {'create': (2, 10), 'reply': (1, 5)},
            clock=lambda: self.now,
        )

    def test_allows_until_limit_then_reports_retry_after(self):
        self.assertIsNone(self.limiter.check(1, 'create'))
        self.assertIsNone(self.limiter.check(1, 'create'))
        wait = self.limiter.check(1, 'create')
        self.assertIsNotNone(wait)
        self.assertAlmostEqual(wait, 10.0)

    def test_window_expiry_allows_again(self):
        self.limiter.check(1, 'create')
        self.limiter.check(1, 'create')
        self.now += 10.1
        self.assertIsNone(self.limiter.check(1, 'create'))

    def test_actions_and_users_are_independent(self):
        self.limiter.check(1, 'reply')
        self.assertIsNotNone(self.limiter.check(1, 'reply'))
        self.assertIsNone(self.limiter.check(1, 'create'))
        self.assertIsNone(self.limiter.check(2, 'reply'))

    def test_explicit_now_and_unknown_action(self):
        self.assertIsNone(self.limiter.check(1, 'create', now=200.0))
        with self.assertRaises(KeyError):
            self.limiter.check(1, 'unknown')

    def test_reset_clears_state(self):
        self.limiter.check(1, 'reply')
        self.limiter.reset()
        self.assertEqual(self.limiter.active_keys, 0)
        self.assertIsNone(self.limiter.check(1, 'reply'))

    def test_key_pruning_bounds_memory(self):
        limiter = RateLimiter({'x': (1, 10)}, max_keys=2)
        limiter.check(1, 'x', now=100.0)
        limiter.check(2, 'x', now=101.0)
        limiter.check(3, 'x', now=102.0)
        self.assertLessEqual(limiter.active_keys, 2)

    def test_format_retry_is_human_readable_and_rounds_up(self):
        text = format_retry(0.2, 'create')
        self.assertIn('1 с', text)
        self.assertIn('создание заявок', text)
        self.assertIn('эта операция', format_retry(2, 'unknown-action'))

    def test_global_limiter_can_be_replaced(self):
        original = rate_limit.get_limiter()
        try:
            replacement = RateLimiter({'create': (1, 10)}, clock=lambda: 1.0)
            rate_limit.set_limiter(replacement)
            self.assertIs(rate_limit.get_limiter(), replacement)
            self.assertIsNone(rate_limit.check_limit(1, 'create'))
            self.assertIsNotNone(rate_limit.check_limit(1, 'create'))
        finally:
            rate_limit.set_limiter(original)


class RateLimiterExtraTests(unittest.TestCase):
    def test_rules_property(self):
        limiter = RateLimiter({'a': (1, 2)})
        self.assertEqual(limiter.rules, {'a': (1, 2)})

    def test_prune_removes_stale_keys(self):
        limiter = RateLimiter({'x': (1, 1)}, max_keys=1)
        limiter.check(1, 'x', now=100.0)
        limiter.check(2, 'x', now=200.0)
        self.assertEqual(limiter.active_keys, 1)
