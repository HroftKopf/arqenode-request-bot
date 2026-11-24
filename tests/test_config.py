"""Тесты конфигурации и fail-closed поведения."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import config


class ConfigTests(unittest.TestCase):
    def test_parse_admin_ids_valid_and_deduplicated(self):
        self.assertEqual(config.parse_admin_ids('10, 20,10'), frozenset({10, 20}))

    def test_parse_admin_ids_rejects_missing_empty_and_invalid(self):
        for raw in (None, '', '   ', '10,,20'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                config.parse_admin_ids(raw)
        for raw in ('abc', '10,abc'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                config.parse_admin_ids(raw)
        for raw in ('0', '-5', '10,-5'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                config.parse_admin_ids(raw)

    def test_relative_database_path_is_resolved_from_project_dir(self):
        resolved = config._resolve_path('data/requests.db', config.BASE_DIR / 'fallback.db')
        self.assertEqual(resolved, (config.BASE_DIR / 'data/requests.db').resolve())

    def test_absolute_database_path_is_preserved(self):
        path = Path('/tmp/arqenode-test.db')
        self.assertEqual(config._resolve_path(str(path), config.BASE_DIR / 'fallback.db'), path)

    def test_validate_config_accepts_valid_settings(self):
        with mock.patch.multiple(
            config,
            BOT_TOKEN='123:TEST_TOKEN_ONLY_FOR_TESTS',
            ADMIN_IDS=frozenset({1}),
            CONFIG_ERRORS=[],
        ):
            config.validate_config()

    def test_validate_config_rejects_missing_token(self):
        with (
            mock.patch.multiple(config, BOT_TOKEN='', ADMIN_IDS=frozenset({1}), CONFIG_ERRORS=[]),
            self.assertRaisesRegex(RuntimeError, 'BOT_TOKEN'),
        ):
            config.validate_config()

    def test_validate_config_rejects_placeholder_and_malformed_token(self):
        for token in ('123:PLACE_YOUR_TOKEN_HERE', 'no-colon-token'):
            with self.subTest(token=token), mock.patch.multiple(
                config, BOT_TOKEN=token, ADMIN_IDS=frozenset({1}), CONFIG_ERRORS=[]
            ), self.assertRaises(RuntimeError):
                config.validate_config()

    def test_validate_config_rejects_empty_admins(self):
        with mock.patch.multiple(
            config,
            BOT_TOKEN='123:TEST_TOKEN_ONLY_FOR_TESTS',
            ADMIN_IDS=frozenset(),
            CONFIG_ERRORS=[],
        ), self.assertRaisesRegex(RuntimeError, 'ADMIN_IDS'):
            config.validate_config()

    def test_validate_config_reports_import_errors(self):
        with mock.patch.multiple(
            config,
            BOT_TOKEN='123:TEST_TOKEN_ONLY_FOR_TESTS',
            ADMIN_IDS=frozenset(),
            CONFIG_ERRORS=['ADMIN_IDS не задан'],
        ), self.assertRaisesRegex(RuntimeError, 'ADMIN_IDS не задан'):
            config.validate_config()

    def test_is_admin(self):
        with mock.patch.object(config, 'ADMIN_IDS', frozenset({7})):
            self.assertTrue(config.is_admin(7))
            self.assertFalse(config.is_admin(8))

    def test_import_with_invalid_admins_stays_fail_closed(self):
        env = os.environ.copy()
        env['ADMIN_IDS'] = 'not-a-number'
        env['BOT_TOKEN'] = '123:TEST_TOKEN_ONLY_FOR_TESTS'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        project = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, '-c',
             'import config; print(len(config.ADMIN_IDS)); print(bool(config.CONFIG_ERRORS)); config.validate_config()'],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip().splitlines()[:2], ['0', 'True'])
        self.assertIn('Некорректная конфигурация', result.stderr)
