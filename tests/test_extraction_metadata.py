from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from src.bill_extractor.pdf_processor import (
    ExtractionResult,
    PDFProcessingError,
    VisaPDFProcessor,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    PROJECT_ROOT
    / "config"
    / "profiles"
    / "capital_one_mastercard.json"
)


class ExtractionMetadataTest(unittest.TestCase):
    def test_extraction_result_default_remains_compatible(
        self,
    ) -> None:
        result = ExtractionResult(
            transactions=pd.DataFrame(),
            source_pages=(),
            ghostscript_path=None,
        )

        self.assertIsNone(result.metadata)

    def test_processor_builds_profile_metadata(self) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )
        source_path = Path(
            "editable_input/capital_one/2025/statement.pdf"
        )

        metadata = processor._build_statement_metadata(
            source_path
        )

        self.assertEqual(
            metadata.source_file,
            source_path,
        )
        self.assertEqual(
            metadata.profile_id,
            processor.profile.profile_id,
        )
        self.assertEqual(
            metadata.institution,
            processor.profile.institution,
        )
        self.assertEqual(
            metadata.document_type,
            processor.profile.document_type,
        )
        self.assertIsNone(metadata.statement_start_date)
        self.assertIsNone(metadata.statement_end_date)

    def test_simplii_statement_period_is_parsed(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )

        start, end = (
            processor._extract_simplii_statement_period(
                "statement period: December 30, 2024 - "
                "January 29, 2025"
            )
        )

        self.assertEqual(
            start,
            date(2024, 12, 30),
        )
        self.assertEqual(
            end,
            date(2025, 1, 29),
        )

    def test_missing_simplii_period_returns_none(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )

        self.assertEqual(
            processor._extract_simplii_statement_period(
                "No statement period is present."
            ),
            (None, None),
        )

    def test_invalid_simplii_period_is_rejected(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )

        with self.assertRaisesRegex(
            PDFProcessingError,
            "Invalid Simplii statement-period date",
        ):
            processor._extract_simplii_statement_period(
                "statement period: February 30, 2025 - "
                "March 30, 2025"
            )

    def test_rbc_chequing_period_is_parsed(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )

        start, end = (
            processor._extract_rbc_chequing_statement_period(
                "Your opening balance on December 20, 2024 "
                "$100.00 Your closing balance on "
                "January 21, 2025 = $200.00"
            )
        )

        self.assertEqual(
            start,
            date(2024, 12, 20),
        )
        self.assertEqual(
            end,
            date(2025, 1, 21),
        )

    def test_missing_rbc_chequing_period_returns_none(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )

        self.assertEqual(
            processor._extract_rbc_chequing_statement_period(
                "No balance dates are present."
            ),
            (None, None),
        )

    def test_invalid_rbc_chequing_period_is_rejected(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile_path=PROFILE_PATH,
        )

        with self.assertRaisesRegex(
            PDFProcessingError,
            "Invalid RBC Chequing opening-balance date",
        ):
            processor._extract_rbc_chequing_statement_period(
                "Your opening balance on February 30, 2025 "
                "$100.00 Your closing balance on "
                "March 21, 2025 = $200.00"
            )


if __name__ == "__main__":
    unittest.main()
