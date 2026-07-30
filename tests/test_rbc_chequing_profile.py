from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    PROJECT_ROOT
    / "config"
    / "profiles"
    / "rbc_chequing_account.json"
)

TEST_INPUT_ROOT = PROJECT_ROOT / "tests" / "input" / "rbc"
NORMAL_INPUT_ROOT = PROJECT_ROOT / "editable_input" / "rbc"
TEST_OUTPUT_ROOT = PROJECT_ROOT / "tests" / "output" / "rbc"

STATEMENT_DATE = "2025-01-21"

EXPECTED_COLUMNS = [
    "Date",
    "Description",
    "Withdrawals ($)",
    "Deposits ($)",
    "Balance ($)",
]

EXPECTED_OPENING_BALANCE = Decimal("2760.94")
EXPECTED_TOTAL_DEPOSITS = Decimal("1700.00")
EXPECTED_TOTAL_WITHDRAWALS = Decimal("3958.86")
EXPECTED_CLOSING_BALANCE = Decimal("502.08")


def find_local_test_pdf() -> Path | None:
    roots = [
        TEST_INPUT_ROOT / "full-text-test",
        NORMAL_INPUT_ROOT / "2025",
        NORMAL_INPUT_ROOT,
    ]

    for root in roots:
        if not root.is_dir():
            continue

        matches = sorted(root.rglob(f"*{STATEMENT_DATE}*.pdf"))
        if matches:
            return matches[0]

    return None


class RBCChqProfileIdentityTest(unittest.TestCase):
    def test_profile_identity_and_paths(self) -> None:
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(profile.profile_id, "rbc_chequing_account_v1")
        self.assertEqual(profile.profile_version, 1)
        self.assertEqual(profile.parser, "rbc_chequing_account")
        self.assertEqual(profile.document_type, "bank_account_statement")

        self.assertEqual(
            profile.resolve_input_folder(),
            (PROJECT_ROOT / "editable_input" / "rbc").resolve(),
        )
        self.assertEqual(
            profile.resolve_output_folder(),
            (PROJECT_ROOT / "csv_output" / "rbc").resolve(),
        )

        self.assertEqual(list(profile.required_headers), EXPECTED_COLUMNS)
        self.assertTrue(profile.recursive)
        self.assertTrue(profile.preserve_subfolders)


class RBCChqExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "RBC Chequing regression PDF not found. Place a redacted "
                f"{STATEMENT_DATE} statement under "
                "tests/input/rbc/full-text-test or keep the local statement "
                "under editable_input/rbc."
            )

        cls.profile = load_profile(PROFILE_PATH)
        cls.processor = VisaPDFProcessor(profile=cls.profile)
        cls.result = cls.processor.extract_transactions(cls.pdf_path)

        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        cls.output_csv = (
            TEST_OUTPUT_ROOT
            / "rbc-chequing-2025-01_transactions.csv"
        )
        cls.result.transactions.to_csv(
            cls.output_csv,
            index=False,
            lineterminator="\n",
        )

    def test_exact_columns_and_pages(self) -> None:
        self.assertEqual(
            list(self.result.transactions.columns),
            EXPECTED_COLUMNS,
        )
        self.assertEqual(self.result.source_pages, (1, 2, 3))

    def test_statement_totals_reconcile(self) -> None:
        transactions = self.result.transactions

        withdrawals = sum(
            (
                self.processor._parse_amount(value)
                for value in transactions["Withdrawals ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        deposits = sum(
            (
                self.processor._parse_amount(value)
                for value in transactions["Deposits ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        self.assertEqual(withdrawals, EXPECTED_TOTAL_WITHDRAWALS)
        self.assertEqual(deposits, EXPECTED_TOTAL_DEPOSITS)

        calculated_closing = (
            EXPECTED_OPENING_BALANCE
            + deposits
            - withdrawals
        )

        self.assertEqual(
            calculated_closing,
            EXPECTED_CLOSING_BALANCE,
        )

    def test_every_transaction_has_a_date(self) -> None:
        dates = self.result.transactions["Date"]

        self.assertGreater(len(dates), 0)
        self.assertTrue(all(value.strip() for value in dates))

    def test_balance_rows_are_not_transactions(self) -> None:
        descriptions = {
            value.strip().lower()
            for value in self.result.transactions["Description"]
        }

        self.assertNotIn("opening balance", descriptions)
        self.assertNotIn("closing balance", descriptions)

    def test_each_transaction_has_one_direction(self) -> None:
        for _, row in self.result.transactions.iterrows():
            has_withdrawal = bool(row["Withdrawals ($)"])
            has_deposit = bool(row["Deposits ($)"])

            self.assertNotEqual(
                has_withdrawal,
                has_deposit,
                msg=f"Invalid transaction direction: {row.to_dict()}",
            )

    def test_candidate_csv_is_created(self) -> None:
        self.assertTrue(self.output_csv.is_file())


if __name__ == "__main__":
    unittest.main()
