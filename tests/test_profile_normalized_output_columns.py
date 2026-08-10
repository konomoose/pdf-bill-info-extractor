from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from src.bill_extractor.profile_loader import (
    ProfileError,
    load_profile,
)


def write_profile(
    path: Path,
    *,
    normalized_output_columns: list[str] | None,
) -> None:
    data = {
        "profile_id": "test_profile_v1",
        "display_name": "Test Profile",
        "profile_version": 1,
        "institution": "Test Bank",
        "document_type": "bank_account_statement",
        "parser": "rbc_chequing_account",
        "input_folder": "editable_input/test",
        "output_folder": "csv_output/test",
        "file_pattern": "*.pdf",
        "recursive": True,
        "preserve_subfolders": True,
        "required_headers": ["Date"],
        "excluded_page_phrases": [],
        "line_tolerance": 2.5,
        "continuation_gap": 18.0,
    }

    if normalized_output_columns is not None:
        data["normalized_output_columns"] = normalized_output_columns

    path.write_text(
        json.dumps(data),
        encoding="utf-8",
    )


class ProfileNormalizedOutputColumnsTest(unittest.TestCase):
    def test_missing_configuration_keeps_default_behavior(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            write_profile(
                path,
                normalized_output_columns=None,
            )

            profile = load_profile(path)

        self.assertIsNone(profile.normalized_output_columns)

    def test_valid_configuration_preserves_order(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            write_profile(
                path,
                normalized_output_columns=[
                    "transaction_date",
                    "description",
                    "withdrawal",
                    "deposit",
                    "balance",
                ],
            )

            profile = load_profile(path)

        self.assertEqual(
            profile.normalized_output_columns,
            (
                "transaction_date",
                "description",
                "withdrawal",
                "deposit",
                "balance",
            ),
        )

    def test_invalid_configured_column_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            write_profile(
                path,
                normalized_output_columns=[
                    "transaction_date",
                    "not_a_column",
                ],
            )

            with self.assertRaisesRegex(
                ProfileError,
                "unknown normalized column",
            ):
                load_profile(path)

    def test_duplicate_configured_column_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            write_profile(
                path,
                normalized_output_columns=[
                    "transaction_date",
                    "transaction_date",
                ],
            )

            with self.assertRaisesRegex(
                ProfileError,
                "duplicate column",
            ):
                load_profile(path)


if __name__ == "__main__":
    unittest.main()
