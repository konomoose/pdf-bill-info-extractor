from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from src.bill_extractor.transaction_normalizer import (
    NORMALIZED_COLUMNS,
)
from src.bill_extractor.yearly_consolidator import (
    write_yearly_transaction_csvs,
)


def make_row(
    transaction_date: str,
    description: str,
    source_file: str,
) -> dict[str, str]:
    row = {
        column: ""
        for column in NORMALIZED_COLUMNS
    }

    row.update(
        {
            "transaction_date": (
                transaction_date
            ),
            "description": description,
            "amount": "10.00",
            "institution": "Test Bank",
            "account_type": (
                "credit_card_statement"
            ),
            "profile_id": "test_profile_v1",
            "source_file": source_file,
        }
    )

    return row


def make_frame(
    rows: list[dict[str, str]],
) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=list(NORMALIZED_COLUMNS),
    )


class YearlyOutputLifecycleTest(
    unittest.TestCase
):
    def test_repeated_write_is_idempotent(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            output_root = (
                Path(directory) / "test_bank"
            )

            frame = make_frame(
                [
                    make_row(
                        "2025-03-14",
                        "Purchase",
                        "statement.pdf",
                    )
                ]
            )

            first = write_yearly_transaction_csvs(
                [frame],
                output_root=output_root,
                institution_slug="test_bank",
            )

            first_content = (
                first[2025].read_bytes()
            )

            second = write_yearly_transaction_csvs(
                [frame],
                output_root=output_root,
                institution_slug="test_bank",
            )

            yearly = pd.read_csv(
                second[2025],
                dtype=str,
                keep_default_na=False,
            )

            self.assertEqual(len(yearly), 1)
            self.assertEqual(
                first_content,
                second[2025].read_bytes(),
            )
            self.assertEqual(
                list(output_root.rglob("*.tmp")),
                [],
            )
            self.assertEqual(
                list(output_root.rglob("*.bak")),
                [],
            )

    def test_stale_managed_year_is_removed(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            output_root = (
                Path(directory) / "test_bank"
            )

            first_frame = make_frame(
                [
                    make_row(
                        "2024-12-31",
                        "Old year",
                        "old.pdf",
                    ),
                    make_row(
                        "2025-01-01",
                        "Current year",
                        "current.pdf",
                    ),
                ]
            )

            first = write_yearly_transaction_csvs(
                [first_frame],
                output_root=output_root,
                institution_slug="test_bank",
            )

            stale_path = first[2024]

            unrelated = (
                output_root
                / "2024"
                / "manual_notes.csv"
            )
            unrelated.write_text(
                "preserve me\n",
                encoding="utf-8",
            )

            other_institution = (
                output_root
                / "2024"
                / (
                    "other_bank_2024_"
                    "transactions.csv"
                )
            )
            other_institution.write_text(
                "preserve me too\n",
                encoding="utf-8",
            )

            second_frame = make_frame(
                [
                    make_row(
                        "2025-01-01",
                        "Current year",
                        "current.pdf",
                    )
                ]
            )

            written = (
                write_yearly_transaction_csvs(
                    [second_frame],
                    output_root=output_root,
                    institution_slug="test_bank",
                )
            )

            self.assertEqual(
                set(written),
                {2025},
            )
            self.assertFalse(
                stale_path.exists()
            )
            self.assertTrue(
                unrelated.is_file()
            )
            self.assertTrue(
                other_institution.is_file()
            )
            self.assertEqual(
                list(output_root.rglob("*.tmp")),
                [],
            )
            self.assertEqual(
                list(output_root.rglob("*.bak")),
                [],
            )

    def test_staging_failure_preserves_existing_output(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            output_root = (
                Path(directory) / "test_bank"
            )

            original_frame = make_frame(
                [
                    make_row(
                        "2025-03-14",
                        "Original",
                        "original.pdf",
                    )
                ]
            )

            written = write_yearly_transaction_csvs(
                [original_frame],
                output_root=output_root,
                institution_slug="test_bank",
            )

            yearly_path = written[2025]
            original_content = (
                yearly_path.read_bytes()
            )

            replacement_frame = make_frame(
                [
                    make_row(
                        "2025-04-01",
                        "Replacement",
                        "replacement.pdf",
                    )
                ]
            )

            with patch.object(
                pd.DataFrame,
                "to_csv",
                side_effect=OSError(
                    "Simulated disk failure."
                ),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "Simulated disk failure",
                ):
                    write_yearly_transaction_csvs(
                        [replacement_frame],
                        output_root=output_root,
                        institution_slug=(
                            "test_bank"
                        ),
                    )

            self.assertEqual(
                yearly_path.read_bytes(),
                original_content,
            )
            self.assertEqual(
                list(output_root.rglob("*.tmp")),
                [],
            )
            self.assertEqual(
                list(output_root.rglob("*.bak")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
