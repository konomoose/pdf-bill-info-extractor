from pathlib import Path
import unittest

import pandas as pd

from src.bill_extractor.pdf_processor import (
    ExtractionResult,
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


if __name__ == "__main__":
    unittest.main()
