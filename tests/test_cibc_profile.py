from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

import pandas as pd

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "config" / "profiles" / "cibc_credit_card.json"
KNOWN_PDF_NAME = "1onlineStatement_2024-03-23.pdf"
KNOWN_CSV_NAME = "1onlineStatement_2024-03-23_transactions.csv"

TEST_INPUT_ROOT = PROJECT_ROOT / "tests" / "input" / "cibc"
NORMAL_INPUT_ROOT = PROJECT_ROOT / "input" / "cibc"
REFERENCE_CSV = (
    PROJECT_ROOT / "tests" / "reference_output" / "cibc" / KNOWN_CSV_NAME
)
TEST_OUTPUT_CSV = PROJECT_ROOT / "tests" / "output" / "cibc" / KNOWN_CSV_NAME

EXPECTED_COLUMNS = [
    "Trans date",
    "Post date",
    "Description",
    "Spend Categories",
    "Amount($)",
]
EXPECTED_CSV_SHA256 = (
    "c08f7358ea072a0444086cc7f29d8752202652f1b88a6535d6581d99fd160ec4"
)


def find_local_test_pdf() -> Path | None:
    environment_path = os.environ.get("CIBC_TEST_PDF")
    if environment_path and Path(environment_path).is_file():
        return Path(environment_path)

    for root in (TEST_INPUT_ROOT, NORMAL_INPUT_ROOT):
        if not root.is_dir():
            continue
        matches = sorted(root.rglob(KNOWN_PDF_NAME))
        if matches:
            return matches[0]

    return None


class CIBCCreditCardProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "CIBC regression PDF not found. Set CIBC_TEST_PDF, or place "
                f"{KNOWN_PDF_NAME} under tests/input/cibc or input/cibc."
            )

        cls.profile = load_profile(PROFILE_PATH)
        cls.result = VisaPDFProcessor(profile=cls.profile).extract_transactions(
            cls.pdf_path
        )

        TEST_OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        cls.result.transactions.to_csv(
            TEST_OUTPUT_CSV,
            index=False,
            lineterminator="\n",
        )

    def test_profile_identity(self) -> None:
        self.assertEqual(self.profile.profile_id, "cibc_credit_card_v1")
        self.assertEqual(self.profile.profile_version, 1)
        self.assertEqual(self.profile.parser, "cibc_credit_card")

    def test_profile_paths_and_folder_processing(self) -> None:
        self.assertEqual(
            self.profile.resolve_input_folder(),
            (PROJECT_ROOT / "input" / "cibc").resolve(),
        )
        self.assertEqual(
            self.profile.resolve_output_folder(),
            (PROJECT_ROOT / "output" / "cibc").resolve(),
        )
        self.assertTrue(self.profile.recursive)
        self.assertTrue(self.profile.preserve_subfolders)

    def test_exact_columns_rows_pages_and_total(self) -> None:
        transactions = self.result.transactions
        self.assertEqual(list(transactions.columns), EXPECTED_COLUMNS)
        self.assertEqual(len(transactions), 17)
        self.assertEqual(self.result.source_pages, (2, 3))

        total = pd.to_numeric(
            transactions["Amount($)"].str.replace(",", "", regex=False)
        ).sum()
        self.assertAlmostEqual(float(total), 1225.48, places=2)

    def test_exact_extracted_data_is_locked(self) -> None:
        csv_bytes = self.result.transactions.to_csv(
            index=False,
            lineterminator="\n",
        ).encode("utf-8")
        digest = hashlib.sha256(csv_bytes).hexdigest()
        self.assertEqual(digest, EXPECTED_CSV_SHA256)

    def test_reference_output_matches_when_available(self) -> None:
        if not REFERENCE_CSV.is_file():
            self.skipTest(
                "No local reference CSV found under tests/reference_output/cibc."
            )

        reference = pd.read_csv(
            REFERENCE_CSV,
            dtype=str,
            keep_default_na=False,
        )
        actual = self.result.transactions.astype(str).reset_index(drop=True)
        pd.testing.assert_frame_equal(
            actual,
            reference.reset_index(drop=True),
            check_dtype=False,
        )

    def test_creditsmart_report_is_not_included(self) -> None:
        combined = " ".join(
            self.result.transactions.astype(str).fillna("").to_numpy().ravel()
        ).lower()
        self.assertNotIn("creditsmart", combined)
        self.assertNotIn("year-to-date", combined)


if __name__ == "__main__":
    unittest.main()
