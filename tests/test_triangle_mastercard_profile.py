from datetime import date
from pathlib import Path
import unittest

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import (
    normalize_transactions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "config" / "profiles" / "triangle_mastercard.json"

KNOWN_PDF = (
    PROJECT_ROOT
    / "editable_input"
    / "triangle_mastercard"
    / "2023"
    / "2023-06-12-Triangle-Mastercard.pdf"
)

EXPECTED_COLUMNS = [
    "Transaction date",
    "Posting date",
    "Activity description",
    "Amount($)",
]


class TriangleMastercardProfileTest(unittest.TestCase):
    def test_profile_identity_and_paths(self):
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(profile.profile_id, "triangle_mastercard_v1")
        self.assertEqual(profile.profile_version, 1)
        self.assertEqual(profile.institution, "Canadian Tire Bank")
        self.assertEqual(profile.document_type, "credit_card_statement")
        self.assertEqual(profile.parser, "triangle_mastercard")
        self.assertEqual(
            profile.input_folder,
            Path("editable_input/triangle_mastercard"),
        )
        self.assertEqual(
            profile.output_folder,
            Path("csv_output/triangle_mastercard"),
        )
        self.assertTrue(profile.recursive)
        self.assertTrue(profile.preserve_subfolders)
        self.assertEqual(
            list(profile.required_headers),
            EXPECTED_COLUMNS,
        )

    @unittest.skipUnless(
        KNOWN_PDF.is_file(),
        "Local Triangle Mastercard regression statement is unavailable.",
    )
    def test_known_multisection_statement(self):
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        result = processor.extract_transactions(KNOWN_PDF)

        self.assertEqual(
            list(result.transactions.columns),
            EXPECTED_COLUMNS,
        )
        self.assertEqual(len(result.transactions), 11)
        self.assertEqual(result.source_pages, (2, 3))

        self.assertTrue(
            result.transactions["Transaction date"].str.strip().ne("").all()
        )
        self.assertTrue(
            result.transactions["Posting date"].str.strip().ne("").all()
        )
        self.assertTrue(
            result.transactions["Activity description"].str.strip().ne("").all()
        )
        self.assertTrue(
            result.transactions["Amount($)"].str.strip().ne("").all()
        )

    @unittest.skipUnless(
        KNOWN_PDF.is_file(),
        "Local Triangle Mastercard regression statement is unavailable.",
    )
    def test_statement_metadata_period(self):
        profile = load_profile(PROFILE_PATH)
        result = VisaPDFProcessor(
            profile=profile
        ).extract_transactions(KNOWN_PDF)

        self.assertIsNotNone(result.metadata)
        self.assertEqual(
            result.metadata.statement_start_date,
            date(2023, 5, 13),
        )
        self.assertEqual(
            result.metadata.statement_end_date,
            date(2023, 6, 12),
        )

    @unittest.skipUnless(
        KNOWN_PDF.is_file(),
        "Local Triangle Mastercard regression statement is unavailable.",
    )
    def test_normalized_transaction_dates(self):
        profile = load_profile(PROFILE_PATH)
        result = VisaPDFProcessor(
            profile=profile
        ).extract_transactions(KNOWN_PDF)

        normalized = normalize_transactions(
            result.transactions,
            result.metadata,
        )

        self.assertEqual(
            normalized.iloc[0]["transaction_date"],
            "2023-05-16",
        )
        self.assertEqual(
            normalized.iloc[0]["posting_date"],
            "2023-05-17",
        )
        self.assertEqual(
            normalized.iloc[-1]["transaction_date"],
            "2023-05-26",
        )
        self.assertEqual(
            normalized.iloc[-1]["posting_date"],
            "2023-05-29",
        )


if __name__ == "__main__":
    unittest.main()
