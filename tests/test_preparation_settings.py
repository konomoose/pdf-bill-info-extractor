from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.bill_extractor.pdf_preparation import PDFPreparationError
from src.bill_extractor.preparation_settings import (
    load_preparation_account_keys,
    remember_preparation_account_key,
    save_preparation_account_keys,
)


class PreparationSettingsTest(unittest.TestCase):
    def test_new_account_key_is_saved_with_expected_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = (
                Path(temporary_folder)
                / "config"
                / "preparation.local.json"
            )

            saved = remember_preparation_account_key(
                "tangerine_chequing",
                settings_path=settings_path,
            )

            self.assertEqual(
                saved,
                ("tangerine_chequing",),
            )
            self.assertEqual(
                json.loads(settings_path.read_text(encoding="utf-8")),
                {
                    "preparation_accounts": [
                        "tangerine_chequing",
                    ],
                },
            )

    def test_saved_key_is_available_after_reloading_settings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            remember_preparation_account_key(
                "tangerine_chequing",
                settings_path=settings_path,
            )

            self.assertEqual(
                load_preparation_account_keys(
                    settings_path=settings_path,
                ),
                ("tangerine_chequing",),
            )

    def test_saved_keys_are_sorted_and_deduplicated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            saved = save_preparation_account_keys(
                (
                    "z_account",
                    "tangerine_chequing",
                    "tangerine_chequing",
                    "a_account",
                ),
                settings_path=settings_path,
            )

            self.assertEqual(
                saved,
                (
                    "a_account",
                    "tangerine_chequing",
                    "z_account",
                ),
            )

    def test_invalid_account_keys_in_settings_are_ignored_safely(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "preparation_accounts": [
                            "tangerine_chequing",
                            "../escape",
                            "account/../escape",
                            "",
                            123,
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_preparation_account_keys(
                    settings_path=settings_path,
                ),
                ("tangerine_chequing",),
            )

    def test_invalid_account_key_is_not_persisted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            with self.assertRaises(PDFPreparationError):
                remember_preparation_account_key(
                    "../escape",
                    settings_path=settings_path,
                )

            self.assertFalse(settings_path.exists())

    def test_malformed_json_loads_conservatively(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text("{not valid json", encoding="utf-8")

            self.assertEqual(
                load_preparation_account_keys(
                    settings_path=settings_path,
                ),
                (),
            )

    def test_only_account_keys_are_stored(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            remember_preparation_account_key(
                "new_account_without_profile",
                settings_path=settings_path,
            )

            text = settings_path.read_text(encoding="utf-8")

            self.assertIn(
                "new_account_without_profile",
                text,
            )
            self.assertNotIn(
                "Synthetic Redaction Term",
                text,
            )
            self.assertNotIn(
                "synthetic-password",
                text,
            )
            self.assertNotIn(
                ".pdf",
                text,
            )


if __name__ == "__main__":
    unittest.main()
