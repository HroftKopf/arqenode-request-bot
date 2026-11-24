"""Тесты статусов, переходов и общих констант."""
from __future__ import annotations

import unittest

from models import (
    ALLOWED_TRANSITIONS,
    EDITABLE,
    STATUS_CODES,
    STATUS_LABELS,
    TERMINAL,
    allowed_statuses,
    can_transition,
    status_label,
)


class StatusModelTests(unittest.TestCase):
    def test_status_codes_and_labels_are_consistent(self):
        self.assertEqual(set(STATUS_CODES), set(STATUS_LABELS))
        self.assertEqual(STATUS_LABELS['new'], 'Новая')
        self.assertEqual(status_label('done'), 'Выполнена')
        self.assertEqual(status_label('unknown'), 'unknown')

    def test_terminal_and_editable_sets(self):
        self.assertEqual(TERMINAL, frozenset({'done', 'cancel'}))
        self.assertEqual(EDITABLE, frozenset({'new', 'wait'}))

    def test_allowed_transitions_are_explicit(self):
        self.assertTrue(can_transition('new', 'work'))
        self.assertTrue(can_transition('work', 'wait'))
        self.assertTrue(can_transition('wait', 'done'))
        self.assertFalse(can_transition('work', 'new'))
        self.assertFalse(can_transition('done', 'work'))
        self.assertFalse(can_transition('cancel', 'new'))
        self.assertFalse(can_transition('unknown', 'work'))
        self.assertEqual(ALLOWED_TRANSITIONS['done'], frozenset())

    def test_allowed_statuses_respects_button_order(self):
        self.assertEqual(allowed_statuses('new'), ('work', 'wait', 'done', 'cancel'))
        self.assertEqual(allowed_statuses('work'), ('wait', 'done', 'cancel'))
        self.assertEqual(allowed_statuses('done'), ())
