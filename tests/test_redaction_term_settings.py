from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.bill_extractor.pdf_preparation import PDFPreparationError
from src.bill_extractor.redaction_term_settings import (
    load_redaction_term_accounts,
    load_redaction_terms_for_account,
    save_redaction_terms_for_account,
)


class RedactionTermSettingsTest(unittest.TestCase):
    def test_terms_persist_independently_for_two_accounts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_terms_for_account(
                "account_one",
                ("Synthetic Term One",),
                settings_path=settings_path,
            )
            save_redaction_terms_for_account(
                "account_two",
                ("Synthetic Term Two", "Synthetic Term Three"),
                settings_path=settings_path,
            )

            self.assertEqual(
                load_redaction_terms_for_account(
                    "account_one",
                    settings_path=settings_path,
                ),
                ("Synthetic Term One",),
            )
            self.assertEqual(
                load_redaction_terms_for_account(
                    "account_two",
                    settings_path=settings_path,
                ),
                (
                    "Synthetic Term Two",
                    "Synthetic Term Three",
                ),
            )

    def test_editing_terms_replaces_saved_list(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_terms_for_account(
                "account_one",
                ("Synthetic Old Term",),
                settings_path=settings_path,
            )
            save_redaction_terms_for_account(
                "account_one",
                ("Synthetic New Term",),
                settings_path=settings_path,
            )

            self.assertEqual(
                load_redaction_terms_for_account(
                    "account_one",
                    settings_path=settings_path,
                ),
                ("Synthetic New Term",),
            )

    def test_saved_terms_use_expected_schema(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_terms_for_account(
                "tangerine_chequing",
                ("Synthetic Term One", "Synthetic Term Two"),
                settings_path=settings_path,
            )

            self.assertEqual(
                json.loads(settings_path.read_text(encoding="utf-8")),
                {
                    "accounts": {
                        "tangerine_chequing": [
                            "Synthetic Term One",
                            "Synthetic Term Two",
                        ],
                    },
                },
            )

    def test_malformed_json_loads_conservatively(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text("{not valid json", encoding="utf-8")

            self.assertEqual(
                load_redaction_term_accounts(
                    settings_path=settings_path,
                ),
                {},
            )

    def test_invalid_account_keys_are_ignored_on_load(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "valid_account": [
                                "Synthetic Valid Term",
                            ],
                            "../escape": [
                                "Synthetic Invalid Term",
                            ],
                            "account/../escape": [
                                "Synthetic Traversal Term",
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_redaction_term_accounts(
                    settings_path=settings_path,
                ),
                {
                    "valid_account": (
                        "Synthetic Valid Term",
                    ),
                },
            )

    def test_invalid_account_key_is_not_saved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            with self.assertRaises(PDFPreparationError):
                save_redaction_terms_for_account(
                    "../escape",
                    ("Synthetic Term",),
                    settings_path=settings_path,
                )

            self.assertFalse(settings_path.exists())

    def test_passwords_are_never_stored(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_terms_for_account(
                "account_one",
                ("Synthetic Term",),
                settings_path=settings_path,
            )

            text = settings_path.read_text(encoding="utf-8")

            self.assertNotIn(
                "synthetic-password",
                text,
            )
            self.assertNotIn(
                "password",
                text.casefold(),
            )


if __name__ == "__main__":
    unittest.main()
