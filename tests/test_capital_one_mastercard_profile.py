from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

import pandas as pd

from src.bill_extractor.pdf_processor import (
    PDFProcessingError,
    VisaPDFProcessor,
)
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import (
    normalize_transactions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROFILE_PATH = (
    PROJECT_ROOT
    / "config"
    / "profiles"
    / "capital_one_mastercard.json"
)

# Private local integration corpus. These PDFs remain ignored by Git.
LOCAL_CORPUS_ROOT = (
    PROJECT_ROOT
    / "editable_input"
    / "capital_one"
)

LEGACY_CROSS_YEAR_PDF = (
    LOCAL_CORPUS_ROOT
    / "2021"
    / "Statement_012021_1362.pdf"
)

MODERN_CROSS_YEAR_PDF = (
    LOCAL_CORPUS_ROOT
    / "2025"
    / "Statement_012025_1362.pdf"
)

EXPECTED_COLUMNS = [
    "Transaction date",
    "Posting date",
    "Description",
    "Amount",
]

EXPECTED_PDF_COUNTS = {
    "2020": 2,
    "2021": 12,
    "2022": 12,
    "2023": 12,
    "2024": 11,
    "2025": 12,
    "2026": 8,
}

EXPECTED_ROW_COUNTS = {
    "2020": 16,
    "2021": 161,
    "2022": 88,
    "2023": 84,
    "2024": 36,
    "2025": 69,
    "2026": 55,
}


def local_corpus_is_complete() -> bool:
    return all(
        len(
            list(
                (LOCAL_CORPUS_ROOT / year).glob("*.pdf")
            )
        )
        == expected
        for year, expected in EXPECTED_PDF_COUNTS.items()
    )


class CapitalOneMastercardProfileTest(unittest.TestCase):
    def test_profile_identity_and_paths(self) -> None:
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(
            profile.profile_id,
            "capital_one_mastercard_v1",
        )
        self.assertEqual(
            profile.display_name,
            "Capital One Mastercard",
        )
        self.assertEqual(
            profile.institution,
            "Capital One",
        )
        self.assertEqual(
            profile.document_type,
            "credit_card_statement",
        )
        self.assertEqual(
            profile.parser,
            "capital_one_mastercard",
        )
        self.assertEqual(
            profile.input_folder,
            Path("editable_input/capital_one"),
        )
        self.assertEqual(
            profile.output_folder,
            Path("csv_output/capital_one"),
        )
        self.assertEqual(
            profile.required_headers,
            tuple(EXPECTED_COLUMNS),
        )

    def test_processor_accepts_profile(self) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH
        )

        self.assertEqual(
            processor.profile.parser,
            "capital_one_mastercard",
        )
        self.assertEqual(
            processor.required_headers,
            EXPECTED_COLUMNS,
        )

    def test_amount_normalization(self) -> None:
        normalize = (
            VisaPDFProcessor
            ._normalize_capital_one_amount
        )

        self.assertEqual(
            normalize("$1,234.56"),
            "1234.56",
        )
        self.assertEqual(
            normalize("12.34"),
            "12.34",
        )
        self.assertEqual(
            normalize("-12.34"),
            "-12.34",
        )
        self.assertEqual(
            normalize("(12.34)"),
            "-12.34",
        )
        self.assertEqual(
            normalize("12.34CR"),
            "-12.34",
        )
        self.assertEqual(
            normalize("12.34-"),
            "-12.34",
        )
        self.assertIsNone(
            normalize("not-an-amount")
        )

    def test_legacy_date_reconstruction(self) -> None:
        result = (
            VisaPDFProcessor
            ._parse_capital_one_legacy_date_tokens(
                "02",
                "MAR03",
                "APR",
            )
        )

        self.assertEqual(
            result,
            ("Mar 2", "Apr 3"),
        )

        invalid = (
            VisaPDFProcessor
            ._parse_capital_one_legacy_date_tokens(
                "40",
                "MAR03",
                "APR",
            )
        )

        self.assertIsNone(invalid)

    def test_balance_reconciliation_accepts_match(self) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH
        )

        transactions = pd.DataFrame(
            [
                {"Amount": "10.00"},
                {"Amount": "-5.00"},
            ]
        )

        processor._validate_capital_one_balance(
            transactions,
            Decimal("100.00"),
            Decimal("105.00"),
        )

    def test_balance_reconciliation_rejects_mismatch(self) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH
        )

        transactions = pd.DataFrame(
            [
                {"Amount": "10.00"},
            ]
        )

        with self.assertRaises(PDFProcessingError):
            processor._validate_capital_one_balance(
                transactions,
                Decimal("100.00"),
                Decimal("111.00"),
            )

    def test_legacy_cross_year_metadata_and_dates(
        self,
    ) -> None:
        if not LEGACY_CROSS_YEAR_PDF.is_file():
            self.skipTest(
                "Legacy Capital One regression "
                "statement is unavailable."
            )

        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH
        )
        result = processor.extract_transactions(
            LEGACY_CROSS_YEAR_PDF
        )

        self.assertIsNotNone(result.metadata)
        self.assertEqual(
            result.metadata.statement_start_date,
            date(2020, 12, 24),
        )
        self.assertEqual(
            result.metadata.statement_end_date,
            date(2021, 1, 23),
        )

        normalized = normalize_transactions(
            result.transactions,
            result.metadata,
        )

        self.assertEqual(
            normalized.iloc[0]["transaction_date"],
            "2020-12-27",
        )
        self.assertEqual(
            normalized.iloc[0]["posting_date"],
            "2020-12-28",
        )
        self.assertEqual(
            normalized.iloc[-1]["transaction_date"],
            "2021-01-22",
        )
        self.assertEqual(
            normalized.iloc[-1]["posting_date"],
            "2021-01-22",
        )

    def test_modern_cross_year_metadata_and_dates(
        self,
    ) -> None:
        if not MODERN_CROSS_YEAR_PDF.is_file():
            self.skipTest(
                "Modern Capital One regression "
                "statement is unavailable."
            )

        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH
        )
        result = processor.extract_transactions(
            MODERN_CROSS_YEAR_PDF
        )

        self.assertIsNotNone(result.metadata)
        self.assertEqual(
            result.metadata.statement_start_date,
            date(2024, 12, 24),
        )
        self.assertEqual(
            result.metadata.statement_end_date,
            date(2025, 1, 23),
        )

        normalized = normalize_transactions(
            result.transactions,
            result.metadata,
        )

        self.assertEqual(
            normalized.iloc[0]["transaction_date"],
            "2025-01-03",
        )
        self.assertEqual(
            normalized.iloc[0]["posting_date"],
            "2025-01-06",
        )
        self.assertEqual(
            normalized.iloc[-1]["transaction_date"],
            "2025-01-23",
        )
        self.assertEqual(
            normalized.iloc[-1]["posting_date"],
            "2025-01-23",
        )

    def test_complete_local_corpus(self) -> None:
        if not local_corpus_is_complete():
            self.skipTest(
                "Complete local Capital One corpus "
                "is unavailable."
            )

        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH
        )

        total_pdfs = 0
        total_rows = 0
        total_empty = 0

        for year, expected_pdf_count in (
            EXPECTED_PDF_COUNTS.items()
        ):
            paths = sorted(
                (LOCAL_CORPUS_ROOT / year).glob("*.pdf")
            )

            self.assertEqual(
                len(paths),
                expected_pdf_count,
            )

            year_rows = 0

            for path in paths:
                result = processor.extract_transactions(
                    path
                )

                self.assertIsNotNone(result.metadata)
                self.assertIsNotNone(
                    result.metadata.statement_start_date
                )
                self.assertIsNotNone(
                    result.metadata.statement_end_date
                )

                self.assertEqual(
                    list(result.transactions.columns),
                    EXPECTED_COLUMNS,
                )

                row_count = len(
                    result.transactions
                )

                if row_count == 0:
                    total_empty += 1

                    self.assertEqual(
                        result.source_pages,
                        tuple(),
                    )
                else:
                    self.assertTrue(
                        result.source_pages
                    )

                year_rows += row_count
                total_rows += row_count
                total_pdfs += 1

            self.assertEqual(
                year_rows,
                EXPECTED_ROW_COUNTS[year],
            )

        self.assertEqual(total_pdfs, 69)
        self.assertEqual(total_rows, 509)
        self.assertEqual(total_empty, 1)


if __name__ == "__main__":
    unittest.main()
