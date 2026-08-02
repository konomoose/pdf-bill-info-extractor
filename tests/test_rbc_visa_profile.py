from __future__ import annotations

import re
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import (
    normalize_transactions,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "config" / "profiles" / "rbc_visa_credit_card.json"

KNOWN_PDF_NAME = "Visa Statement-7498 2025-01-09_unsecured_Redacted.pdf"

TEST_INPUT_ROOT = PROJECT_ROOT / "tests" / "input" / "rbc_visa"
NORMAL_INPUT_ROOT = PROJECT_ROOT / "editable_input" / "rbc_visa"
TEST_OUTPUT_ROOT = PROJECT_ROOT / "tests" / "output" / "rbc_visa"

EXPECTED_COLUMNS = [
    "Transaction date",
    "Posting date",
    "Activity description",
    "Amount($)",
]


def find_local_test_pdf() -> Path | None:
    preferred_locations = [
        TEST_INPUT_ROOT / "full-text-test" / KNOWN_PDF_NAME,
        NORMAL_INPUT_ROOT / KNOWN_PDF_NAME,
    ]

    for path in preferred_locations:
        if path.is_file():
            return path

    for root in (TEST_INPUT_ROOT / "full-text-test", NORMAL_INPUT_ROOT):
        if not root.is_dir():
            continue

        matches = sorted(root.rglob("*.pdf"))
        if matches:
            return matches[0]

    return None


class RBCVisaProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "RBC Visa regression PDF not found. Place the redacted full-text "
                "statement under tests/input/rbc_visa/full-text-test."
            )

        cls.profile = load_profile(PROFILE_PATH)
        cls.processor = VisaPDFProcessor(profile=cls.profile)
        cls.result = cls.processor.extract_transactions(cls.pdf_path)

        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        cls.output_csv = TEST_OUTPUT_ROOT / "rbc-visa-2025-01_transactions.csv"
        cls.result.transactions.to_csv(
            cls.output_csv,
            index=False,
            lineterminator="\n",
        )

    def test_profile_identity_and_paths(self) -> None:
        self.assertEqual(self.profile.profile_id, "rbc_visa_credit_card_v1")
        self.assertEqual(self.profile.profile_version, 1)
        self.assertEqual(self.profile.parser, "rbc_visa_credit_card")

        self.assertEqual(
            self.profile.resolve_input_folder(),
            (PROJECT_ROOT / "editable_input" / "rbc_visa").resolve(),
        )
        self.assertEqual(
            self.profile.resolve_output_folder(),
            (PROJECT_ROOT / "csv_output" / "rbc_visa").resolve(),
        )

        self.assertTrue(self.profile.recursive)
        self.assertTrue(self.profile.preserve_subfolders)

    def test_exact_columns_rows_and_pages(self) -> None:
        transactions = self.result.transactions

        self.assertEqual(list(transactions.columns), EXPECTED_COLUMNS)
        self.assertEqual(len(transactions), 22)
        self.assertEqual(self.result.source_pages, (1, 2))

    def test_statement_balance_reconciliation(self) -> None:
        total = pd.to_numeric(
            self.result.transactions["Amount($)"].str.replace(
                ",", "", regex=False
            )
        ).sum()

        self.assertAlmostEqual(float(total), 82.67, places=2)
        self.assertAlmostEqual(732.42 + float(total), 815.09, places=2)

    def test_first_and_last_transactions(self) -> None:
        first = self.result.transactions.iloc[0].to_dict()
        last = self.result.transactions.iloc[-1].to_dict()

        self.assertEqual(first["Transaction date"], "Dec 14")
        self.assertEqual(first["Posting date"], "Dec 16")
        self.assertEqual(
            first["Activity description"],
            "AMZN MKTP CA*Z15YP5GU2 WWW.AMAZON.CAON",
        )
        self.assertEqual(first["Amount($)"], "123.18")

        self.assertEqual(last["Transaction date"], "Jan 4")
        self.assertEqual(last["Posting date"], "Jan 6")
        self.assertEqual(
            last["Activity description"],
            "INDIGO PARK VANCOUVER BC",
        )
        self.assertEqual(last["Amount($)"], "21.50")

    def test_credit_and_interest_are_retained(self) -> None:
        transactions = self.result.transactions

        credit = transactions[
            transactions["Activity description"] == "CIBC TORONTO"
        ].iloc[0]

        self.assertEqual(credit["Amount($)"], "-1,000.00")

        interest = transactions[
            transactions["Activity description"] == "PURCHASE INTEREST 20.99%"
        ].iloc[0]

        self.assertEqual(interest["Amount($)"], "1.16")

    def test_reference_numbers_are_not_descriptions(self) -> None:
        reference_number = re.compile(r"\b\d{23}\b")

        for description in self.result.transactions["Activity description"]:
            self.assertIsNone(reference_number.search(description))

    def test_statement_metadata_period(self) -> None:
        self.assertIsNotNone(self.result.metadata)
        self.assertEqual(
            self.result.metadata.statement_start_date,
            date(2024, 12, 10),
        )
        self.assertEqual(
            self.result.metadata.statement_end_date,
            date(2025, 1, 9),
        )

    def test_normalized_transaction_dates(self) -> None:
        normalized = normalize_transactions(
            self.result.transactions,
            self.result.metadata,
        )

        self.assertEqual(
            normalized.iloc[0]["transaction_date"],
            "2024-12-14",
        )
        self.assertEqual(
            normalized.iloc[0]["posting_date"],
            "2024-12-16",
        )
        self.assertEqual(
            normalized.iloc[-1]["transaction_date"],
            "2025-01-04",
        )
        self.assertEqual(
            normalized.iloc[-1]["posting_date"],
            "2025-01-06",
        )

    def test_candidate_csv_is_created(self) -> None:
        self.assertTrue(self.output_csv.is_file())


if __name__ == "__main__":
    unittest.main()
