"""Кратковременные блокировки Windows не должны уничтожать старый прогресс."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src import file_io


def locked(code=5):
    error = PermissionError("Временная блокировка Windows")
    error.winerror = code
    return error


class AtomicWriteRetryTests(unittest.TestCase):
    def test_transient_windows_locks_retry_same_complete_file(self):
        for code in (5, 32, 33):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "collection.json"
                target.write_text("old", encoding="utf-8")
                replace = file_io.os.replace
                sources = []

                def attempt(source, destination):
                    sources.append(source)
                    self.assertEqual(target.read_text(encoding="utf-8"), "old")
                    self.assertEqual(Path(source).read_text(encoding="utf-8"), "Новый прогресс")
                    if len(sources) <= 2:
                        raise locked(code)
                    replace(source, destination)

                with patch.object(file_io.os, "replace", side_effect=attempt), patch.object(file_io.time, "sleep") as sleep:
                    file_io.atomic_write_text(target, "Новый прогресс")
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(len(set(sources)), 1)
                self.assertEqual(target.read_text(encoding="utf-8"), "Новый прогресс")
                self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_persistent_lock_is_bounded_and_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "collection.json"
            target.write_text("old", encoding="utf-8")
            error = locked()
            with patch.object(file_io.os, "replace", side_effect=error) as replace, patch.object(file_io.time, "sleep") as sleep:
                with self.assertRaises(PermissionError) as raised:
                    file_io.atomic_write_text(target, "new")
            self.assertIs(raised.exception, error)
            self.assertEqual(replace.call_count, len(file_io._REPLACE_DELAYS) + 1)
            self.assertEqual(sleep.call_count, len(file_io._REPLACE_DELAYS))
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_unrelated_errors_are_not_retried(self):
        for error in (OSError("disk full"), PermissionError("ACL"), locked(1314)):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "collection.json"
                with patch.object(file_io.os, "replace", side_effect=error) as replace, patch.object(file_io.time, "sleep") as sleep:
                    with self.assertRaises(OSError):
                        file_io.atomic_write_text(target, "new")
                replace.assert_called_once()
                sleep.assert_not_called()
                self.assertFalse(target.exists())

    def test_cleanup_error_does_not_mask_original_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "collection.json"
            original = OSError("disk error")
            with patch.object(file_io.os, "replace", side_effect=original), patch.object(file_io.os, "unlink", side_effect=locked()):
                with self.assertRaises(OSError) as raised:
                    file_io.atomic_write_text(target, "new")
            self.assertIs(raised.exception, original)
