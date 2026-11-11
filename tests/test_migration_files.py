from pathlib import Path
import tempfile
import unittest

from src.migrations import load_migrations, MigrationError


class MigrationFileTests(unittest.TestCase):
    def test_windows_and_unix_newlines_have_the_same_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "0001_example.sql"
            path.write_bytes(b"SELECT 1;\nSELECT 2;\n")
            unix = load_migrations(directory)[0].checksum
            path.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
            self.assertEqual(load_migrations(directory)[0].checksum, unix)

    def test_duplicate_versions_invalid_names_and_empty_files_are_rejected(self):
        for files in (
            {"0001_first.sql": "SELECT 1;", "0001_second.sql": "SELECT 2;"},
            {"invalid.sql": "SELECT 1;"},
            {"0001_empty.sql": "  "},
        ):
            with (
                self.subTest(files=list(files)),
                tempfile.TemporaryDirectory() as directory,
            ):
                for name, script in files.items():
                    (Path(directory) / name).write_text(script)
                with self.assertRaises(MigrationError):
                    load_migrations(directory)

    def test_missing_migrations_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MigrationError):
                load_migrations(directory)
