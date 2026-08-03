from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.bill_extractor.transaction_normalizer import (
    NORMALIZED_COLUMNS,
)
from src.bill_extractor.yearly_consolidator import (
    ConsolidationError,
    consolidate_normalized_transactions,
    write_yearly_transaction_csvs,
)


def make_row(
    transaction_date: str,
    description: str,
    source_file: str,
    *,
    posting_date: str = "",
    effective_date: str = "",
    amount: str = "",
) -> dict[str, str]:
    row = {
        column: ""
        for column in NORMALIZED_COLUMNS
    }

    row.update(
        {
            "transaction_date": transaction_date,
            "posting_date": posting_date,
            "effective_date": effective_date,
            "description": description,
            "amount": amount,
            "institution": "Test Bank",
            "account_type": "credit_card_statement",
            "profile_id": "test_profile_v1",
            "source_file": source_file,
        }
    )

    return row


class YearlyConsolidatorTest(unittest.TestCase):
    def test_transactions_are_split_by_actual_year(
        self,
    ) -> None:
        frame = pd.DataFrame(
            [
                make_row(
                    "2025-03-02",
                    "Third",
                    "statement-3.pdf",
                ),
                make_row(
                    "2024-12-31",
                    "First",
                    "statement-1.pdf",
                ),
                make_row(
                    "2025-01-01",
                    "Second",
                    "statement-2.pdf",
                ),
            ],
            columns=NORMALIZED_COLUMNS,
        )

        result = consolidate_normalized_transactions(
            [frame]
        )

        self.assertEqual(
            list(result),
            [2024, 2025],
        )
        self.assertEqual(
            list(result[2024]["description"]),
            ["First"],
        )
        self.assertEqual(
            list(result[2025]["description"]),
            ["Second", "Third"],
        )

    def test_transactions_are_sorted_chronologically(
        self,
    ) -> None:
        frame = pd.DataFrame(
            [
                make_row(
                    "2025-04-01",
                    "Later posting",
                    "b.pdf",
                    posting_date="2025-04-03",
                ),
                make_row(
                    "2025-03-31",
                    "Earlier date",
                    "a.pdf",
                    posting_date="2025-04-01",
                ),
                make_row(
                    "2025-04-01",
                    "Earlier posting",
                    "c.pdf",
                    posting_date="2025-04-02",
                ),
            ],
            columns=NORMALIZED_COLUMNS,
        )

        result = consolidate_normalized_transactions(
            [frame]
        )[2025]

        self.assertEqual(
            list(result["description"]),
            [
                "Earlier date",
                "Earlier posting",
                "Later posting",
            ],
        )

    def test_exact_duplicates_are_removed(
        self,
    ) -> None:
        duplicate = make_row(
            "2025-03-14",
            "Purchase",
            "statement.pdf",
            posting_date="2025-03-15",
            amount="10.00",
        )

        first = pd.DataFrame(
            [duplicate],
            columns=NORMALIZED_COLUMNS,
        )
        second = pd.DataFrame(
            [duplicate.copy()],
            columns=NORMALIZED_COLUMNS,
        )

        result = consolidate_normalized_transactions(
            [first, second]
        )[2025]

        self.assertEqual(len(result), 1)

    def test_matching_transactions_from_different_sources_remain(
        self,
    ) -> None:
        first = make_row(
            "2025-03-14",
            "Purchase",
            "statement-a.pdf",
            amount="10.00",
        )
        second = make_row(
            "2025-03-14",
            "Purchase",
            "statement-b.pdf",
            amount="10.00",
        )

        result = consolidate_normalized_transactions(
            [
                pd.DataFrame(
                    [first, second],
                    columns=NORMALIZED_COLUMNS,
                )
            ]
        )[2025]

        self.assertEqual(len(result), 2)
        self.assertEqual(
            set(result["source_file"]),
            {
                "statement-a.pdf",
                "statement-b.pdf",
            },
        )

    def test_missing_normalized_column_is_rejected(
        self,
    ) -> None:
        frame = pd.DataFrame(
            [
                {
                    "transaction_date": "2025-03-14",
                    "description": "Purchase",
                }
            ]
        )

        with self.assertRaisesRegex(
            ConsolidationError,
            "missing required columns",
        ):
            consolidate_normalized_transactions(
                [frame]
            )

    def test_invalid_transaction_date_is_rejected(
        self,
    ) -> None:
        frame = pd.DataFrame(
            [
                make_row(
                    "2025-02-30",
                    "Impossible date",
                    "statement.pdf",
                )
            ],
            columns=NORMALIZED_COLUMNS,
        )

        with self.assertRaisesRegex(
            ConsolidationError,
            "invalid calendar date",
        ):
            consolidate_normalized_transactions(
                [frame]
            )

    def test_empty_inputs_return_no_years(
        self,
    ) -> None:
        empty = pd.DataFrame(
            columns=NORMALIZED_COLUMNS
        )

        self.assertEqual(
            consolidate_normalized_transactions(
                [empty]
            ),
            {},
        )

    def test_yearly_csv_is_written_without_changing_statement_csv(
        self,
    ) -> None:
        frame = pd.DataFrame(
            [
                make_row(
                    "2025-03-14",
                    "Purchase",
                    "statement.pdf",
                    amount="10.00",
                )
            ],
            columns=NORMALIZED_COLUMNS,
        )

        with TemporaryDirectory() as directory:
            output_root = Path(directory) / "test_bank"
            year_folder = output_root / "2025"
            year_folder.mkdir(parents=True)

            statement_csv = (
                year_folder
                / "individual_statement.csv"
            )
            statement_csv.write_text(
                "original statement output\n",
                encoding="utf-8",
            )

            written = write_yearly_transaction_csvs(
                [frame],
                output_root=output_root,
                institution_slug="test_bank",
            )

            expected_path = (
                year_folder
                / "test_bank_2025_transactions.csv"
            )

            self.assertEqual(
                written,
                {2025: expected_path},
            )
            self.assertTrue(expected_path.is_file())
            self.assertEqual(
                statement_csv.read_text(
                    encoding="utf-8"
                ),
                "original statement output\n",
            )

            saved = pd.read_csv(
                expected_path,
                dtype=str,
                keep_default_na=False,
            )

            self.assertEqual(
                list(saved.columns),
                list(NORMALIZED_COLUMNS),
            )
            self.assertEqual(
                saved.loc[0, "description"],
                "Purchase",
            )


if __name__ == "__main__":
    unittest.main()
