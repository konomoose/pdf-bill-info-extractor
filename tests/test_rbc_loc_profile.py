from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import (
    normalize_transactions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROFILE_PATH = (
    PROJECT_ROOT
    / "config"
    / "profiles"
    / "rbc_loc.json"
)

INPUT_ROOT = PROJECT_ROOT / "editable_input" / "rbc_loc"

MODERN_PDF = (
    INPUT_ROOT
    / "2025"
    / "Credit Line Statement-0001 2025-04-21.pdf"
)

ANNUAL_PDF = (
    INPUT_ROOT
    / "2025"
    / "Credit Line Statement-0001 2025-01-13.pdf"
)

EXPECTED_COLUMNS = [
    "Date",
    "Description",
    "Interest/Fees/Insurance ($)",
    "Withdrawals ($)",
    "Payments ($)",
    "Balance owing ($)",
]


class RBCLocProfileTest(unittest.TestCase):
    def test_profile_identity_and_paths(self):
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(profile.profile_id, "rbc_loc_v1")
        self.assertEqual(profile.profile_version, 1)
        self.assertEqual(profile.parser, "rbc_loc")
        self.assertEqual(
            profile.document_type,
            "line_of_credit_statement",
        )
        self.assertEqual(
            profile.input_folder,
            Path("editable_input/rbc_loc"),
        )
        self.assertEqual(
            profile.output_folder,
            Path("csv_output/rbc_loc"),
        )
        self.assertEqual(
            list(profile.required_headers),
            EXPECTED_COLUMNS,
        )
        self.assertTrue(profile.recursive)
        self.assertTrue(profile.preserve_subfolders)


class RBCLocExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MODERN_PDF.exists():
            raise unittest.SkipTest(
                f"RBC LOC development statement not found: {MODERN_PDF}"
            )

        profile = load_profile(PROFILE_PATH)
        cls.processor = VisaPDFProcessor(profile=profile)
        cls.result = cls.processor.extract_transactions(MODERN_PDF)
        cls.transactions = cls.result.transactions

    def test_exact_columns(self):
        self.assertEqual(
            list(self.transactions.columns),
            EXPECTED_COLUMNS,
        )

    def test_known_statement_totals(self):
        withdrawals = sum(
            (
                self.processor._parse_amount(value)
                for value in self.transactions["Withdrawals ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        principal_payments = sum(
            (
                self.processor._parse_amount(value)
                for value in self.transactions["Payments ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        interest_fees = sum(
            (
                self.processor._parse_amount(value)
                for value in self.transactions[
                    "Interest/Fees/Insurance ($)"
                ]
                if value
            ),
            Decimal("0.00"),
        )

        self.assertEqual(withdrawals, Decimal("2709.24"))
        self.assertEqual(principal_payments, Decimal("2000.00"))
        self.assertEqual(interest_fees, Decimal("4.25"))

        # RBC's statement payment total includes the interest payment.
        self.assertEqual(
            principal_payments + interest_fees,
            Decimal("2004.25"),
        )

    def test_interest_payment_is_retained(self):
        matches = self.transactions[
            self.transactions["Description"] == "Interest Payment"
        ]

        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches.iloc[0]["Interest/Fees/Insurance ($)"],
            "4.25",
        )

    def test_footer_is_not_transaction_text(self):
        descriptions = " ".join(
            self.transactions["Description"].astype(str)
        ).lower()

        self.assertNotIn("please retain", descriptions)

    def test_known_transaction_count_and_pages(self):
        self.assertEqual(len(self.transactions), 10)
        self.assertEqual(self.result.source_pages, (1, 2))

    def test_statement_metadata_period(self):
        self.assertIsNotNone(self.result.metadata)
        self.assertEqual(
            self.result.metadata.statement_start_date,
            date(2025, 3, 19),
        )
        self.assertEqual(
            self.result.metadata.statement_end_date,
            date(2025, 4, 21),
        )

    def test_normalized_transaction_dates(self):
        normalized = normalize_transactions(
            self.result.transactions,
            self.result.metadata,
        )

        self.assertEqual(
            normalized.iloc[0]["transaction_date"],
            "2025-03-21",
        )
        self.assertEqual(
            normalized.iloc[-1]["transaction_date"],
            "2025-04-21",
        )


class RBCLocAnnualSummaryTest(unittest.TestCase):
    def test_annual_summary_is_recognized(self):
        if not ANNUAL_PDF.exists():
            self.skipTest(
                f"RBC LOC annual statement not found: {ANNUAL_PDF}"
            )

        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        self.assertTrue(
            processor._is_rbc_loc_annual_summary(ANNUAL_PDF)
        )


if __name__ == "__main__":
    unittest.main()
