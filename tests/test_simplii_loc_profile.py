from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROFILE_PATH = (
    PROJECT_ROOT / "config" / "profiles" / "simplii_loc.json"
)

TEST_PDF = (
    PROJECT_ROOT
    / "tests"
    / "input"
    / "simplii_loc"
    / "full-text-test"
    / "01-jan-2025_Redacted.pdf"
)

NORMAL_PDF = (
    PROJECT_ROOT
    / "editable_input"
    / "simplii_loc"
    / "2025"
    / "01-jan-2025.pdf"
)

TEST_OUTPUT_ROOT = (
    PROJECT_ROOT / "tests" / "output" / "simplii_loc"
)

EXPECTED_COLUMNS = [
    "Trans. date",
    "Eff. date",
    "Transaction",
    "Funds out",
    "Funds in",
    "Balance",
]


def find_local_test_pdf() -> Path | None:
    for path in (TEST_PDF, NORMAL_PDF):
        if path.is_file():
            return path
    return None


class SimpliiLOCProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()

        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "Simplii LOC regression PDF not found. Place "
                "01-jan-2025_Redacted.pdf under "
                "tests/input/simplii_loc/full-text-test."
            )

        cls.profile = load_profile(PROFILE_PATH)
        cls.processor = VisaPDFProcessor(profile=cls.profile)
        cls.result = cls.processor.extract_transactions(cls.pdf_path)

        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        cls.output_csv = (
            TEST_OUTPUT_ROOT / "01-jan-2025_transactions.csv"
        )

        cls.result.transactions.to_csv(
            cls.output_csv,
            index=False,
            lineterminator="\n",
        )

    def test_profile_identity_and_paths(self) -> None:
        self.assertEqual(
            self.profile.profile_id,
            "simplii_loc_v1",
        )
        self.assertEqual(self.profile.profile_version, 1)

        # LOC intentionally reuses the existing Simplii statement parser.
        self.assertEqual(
            self.profile.parser,
            "simplii_chequing_account",
        )

        self.assertEqual(
            self.profile.resolve_input_folder(),
            (PROJECT_ROOT / "editable_input" / "simplii_loc").resolve(),
        )

        self.assertEqual(
            self.profile.resolve_output_folder(),
            (PROJECT_ROOT / "csv_output" / "simplii_loc").resolve(),
        )

        self.assertTrue(self.profile.recursive)
        self.assertTrue(self.profile.preserve_subfolders)

    def test_exact_columns_rows_and_pages(self) -> None:
        transactions = self.result.transactions

        self.assertEqual(
            list(transactions.columns),
            EXPECTED_COLUMNS,
        )
        self.assertEqual(len(transactions), 2)
        self.assertEqual(self.result.source_pages, (1,))

    def test_statement_totals(self) -> None:
        transactions = self.result.transactions

        funds_out = pd.to_numeric(
            transactions["Funds out"]
            .replace("", "0")
            .str.replace(",", "", regex=False)
        ).sum()

        funds_in = pd.to_numeric(
            transactions["Funds in"]
            .replace("", "0")
            .str.replace(",", "", regex=False)
        ).sum()

        self.assertAlmostEqual(
            float(funds_out),
            183.97,
            places=2,
        )

        self.assertAlmostEqual(
            float(funds_in),
            750.00,
            places=2,
        )

    def test_exact_transactions(self) -> None:
        first = self.result.transactions.iloc[0].to_dict()
        last = self.result.transactions.iloc[-1].to_dict()

        self.assertEqual(first["Trans. date"], "Jan 16")
        self.assertEqual(first["Eff. date"], "Jan 16")
        self.assertEqual(first["Transaction"], "TRANSFER IN")
        self.assertEqual(first["Funds out"], "")
        self.assertEqual(first["Funds in"], "750.00")
        self.assertEqual(
            first["Balance"].replace(",", ""),
            "-20337.29",
        )

        self.assertEqual(last["Trans. date"], "Jan 29")
        self.assertEqual(last["Eff. date"], "Jan 30")
        self.assertEqual(last["Transaction"], "INTEREST CHARGE")
        self.assertEqual(last["Funds out"], "183.97")
        self.assertEqual(last["Funds in"], "")
        self.assertEqual(
            last["Balance"].replace(",", ""),
            "-20521.26",
        )

    def test_balance_forward_is_not_a_transaction(self) -> None:
        descriptions = self.result.transactions["Transaction"].tolist()

        self.assertNotIn(
            "BALANCE FORWARD",
            descriptions,
        )

    def test_closing_balance(self) -> None:
        closing_balance = (
            self.result.transactions.iloc[-1]["Balance"]
            .replace(",", "")
        )

        self.assertEqual(closing_balance, "-20521.26")

    def test_candidate_csv_is_created(self) -> None:
        self.assertTrue(self.output_csv.is_file())


if __name__ == "__main__":
    unittest.main()
