from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.bill_extractor.pdf_preparation import PDFPreparationError
from src.bill_extractor.redaction_term_settings import (
    load_redaction_account_settings,
    load_redaction_account_settings_for_institution,
    load_redaction_term_accounts,
    load_redaction_terms_for_account,
    save_redaction_account_settings,
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

            save_redaction_account_settings(
                "tangerine_chequing",
                ("Synthetic Term One", "Synthetic Term Two"),
                etransfer_keep_first_name_only=True,
                settings_path=settings_path,
            )

            self.assertEqual(
                json.loads(settings_path.read_text(encoding="utf-8")),
                {
                    "accounts": {
                        "tangerine_chequing": {
                            "terms": [
                                "Synthetic Term One",
                                "Synthetic Term Two",
                            ],
                            "etransfer_keep_first_name_only": True,
                        },
                    },
                },
            )

    def test_etransfer_option_persists_independently_for_accounts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_account_settings(
                "account_one",
                ("Synthetic Term One",),
                etransfer_keep_first_name_only=True,
                settings_path=settings_path,
            )
            save_redaction_account_settings(
                "account_two",
                ("Synthetic Term Two",),
                etransfer_keep_first_name_only=False,
                settings_path=settings_path,
            )

            self.assertTrue(
                load_redaction_account_settings(
                    "account_one",
                    settings_path=settings_path,
                ).etransfer_keep_first_name_only
            )
            self.assertFalse(
                load_redaction_account_settings(
                    "account_two",
                    settings_path=settings_path,
                ).etransfer_keep_first_name_only
            )

    def test_old_list_schema_remains_readable_with_option_off(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "old_account": [
                                "Synthetic Old Term",
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            settings = load_redaction_account_settings(
                "old_account",
                settings_path=settings_path,
            )

            self.assertEqual(
                settings.terms,
                ("Synthetic Old Term",),
            )
            self.assertFalse(
                settings.etransfer_keep_first_name_only
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

    def test_detected_names_are_never_stored(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_account_settings(
                "account_one",
                (),
                etransfer_keep_first_name_only=True,
                settings_path=settings_path,
            )

            text = settings_path.read_text(encoding="utf-8")

            self.assertNotIn("ADRIAN", text)
            self.assertNotIn("CORY", text)
            self.assertNotIn("SUTHERLAND", text)

    def test_institution_terms_combine_legacy_account_terms(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "institutions": {
                            "Synthetic Bank": {
                                "terms": [
                                    "Synthetic Shared One",
                                    "Synthetic Shared Two",
                                ],
                            },
                        },
                        "accounts": {
                            "account_one": {
                                "terms": [
                                    "Synthetic Legacy One",
                                ],
                                "etransfer_keep_first_name_only": True,
                            },
                            "account_two": {
                                "terms": [
                                    "Synthetic Shared Two",
                                    "Synthetic Legacy Two",
                                ],
                                "etransfer_keep_first_name_only": False,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            settings = load_redaction_account_settings_for_institution(
                "account_one",
                institution="Synthetic Bank",
                institution_account_keys=(
                    "account_one",
                    "account_two",
                ),
                settings_path=settings_path,
            )

        self.assertEqual(
            settings.terms,
            (
                "Synthetic Shared One",
                "Synthetic Shared Two",
                "Synthetic Legacy One",
                "Synthetic Legacy Two",
            ),
        )
        self.assertTrue(settings.etransfer_keep_first_name_only)

    def test_saving_known_institution_migrates_terms_to_institution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "account_one": {
                                "terms": [
                                    "Synthetic Removed Term",
                                ],
                                "etransfer_keep_first_name_only": False,
                            },
                            "account_two": {
                                "terms": [
                                    "Synthetic Removed Term",
                                    "Synthetic Old Duplicate",
                                ],
                                "etransfer_keep_first_name_only": True,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            save_redaction_account_settings(
                "account_one",
                ("Synthetic Current Term",),
                etransfer_keep_first_name_only=True,
                institution="Synthetic Bank",
                institution_account_keys=(
                    "account_one",
                    "account_two",
                ),
                settings_path=settings_path,
            )

            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            settings = load_redaction_account_settings_for_institution(
                "account_two",
                institution="Synthetic Bank",
                institution_account_keys=(
                    "account_one",
                    "account_two",
                ),
                settings_path=settings_path,
            )

        self.assertEqual(
            payload["institutions"],
            {
                "Synthetic Bank": {
                    "terms": [
                        "Synthetic Current Term",
                    ],
                },
            },
        )
        self.assertEqual(
            payload["accounts"]["account_one"]["terms"],
            [],
        )
        self.assertEqual(
            payload["accounts"]["account_two"]["terms"],
            [],
        )
        self.assertEqual(
            settings.terms,
            ("Synthetic Current Term",),
        )
        self.assertTrue(
            payload["accounts"]["account_one"][
                "etransfer_keep_first_name_only"
            ]
        )
        self.assertTrue(
            payload["accounts"]["account_two"][
                "etransfer_keep_first_name_only"
            ]
        )

    def test_unmapped_account_keeps_account_level_behavior(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"

            save_redaction_account_settings(
                "unknown_account",
                ("Synthetic Account Term",),
                settings_path=settings_path,
            )

            payload = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertNotIn("institutions", payload)
        self.assertEqual(
            payload,
            {
                "accounts": {
                    "unknown_account": {
                        "terms": [
                            "Synthetic Account Term",
                        ],
                        "etransfer_keep_first_name_only": False,
                    },
                },
            },
        )

    def test_unrelated_institution_does_not_receive_terms(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = Path(temporary_folder) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "institutions": {
                            "Synthetic Tangerine Bank": {
                                "terms": [
                                    "Synthetic Tangerine Term",
                                ],
                            },
                        },
                        "accounts": {
                            "other_account": {
                                "terms": [
                                    "Synthetic Other Term",
                                ],
                                "etransfer_keep_first_name_only": False,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            settings = load_redaction_account_settings_for_institution(
                "other_account",
                institution="Synthetic Other Bank",
                institution_account_keys=("other_account",),
                settings_path=settings_path,
            )

        self.assertEqual(
            settings.terms,
            ("Synthetic Other Term",),
        )


if __name__ == "__main__":
    unittest.main()
