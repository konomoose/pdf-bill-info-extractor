from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

import pandas as pd

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile

PROFILE_PATH = Path("config/profiles/cibc_credit_card.json")
KNOWN_PDF_NAME = "1onlineStatement_2024-03-23.pdf"
EXPECTED_COLUMNS = [
    "Trans date",
    "Post date",
    "Description",
    "Spend Categories",
    "Amount($)",
]
EXPECTED_CSV_SHA256 = "c08f7358ea072a0444086cc7f29d8752202652f1b88a6535d6581d99fd160ec4"


def find_local_test_pdf() -> Path | None:
    candidates = [
        os.environ.get("CIBC_TEST_PDF"),
        str(Path("tests/fixtures/statements") / KNOWN_PDF_NAME),
        str(Path("pdf_statements") / KNOWN_PDF_NAME),
    ]

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class CIBCCreditCardProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "CIBC regression PDF not found. Set CIBC_TEST_PDF or place "
                f"{KNOWN_PDF_NAME} in pdf_statements."
            )

        cls.profile = load_profile(PROFILE_PATH)
        cls.result = VisaPDFProcessor(profile=cls.profile).extract_transactions(
            cls.pdf_path
        )

    def test_profile_identity(self) -> None:
        self.assertEqual(self.profile.profile_id, "cibc_credit_card_v1")
        self.assertEqual(self.profile.profile_version, 1)
        self.assertEqual(self.profile.parser, "cibc_credit_card")

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
            index=False, lineterminator="\n"
        ).encode("utf-8")
        digest = hashlib.sha256(csv_bytes).hexdigest()
        self.assertEqual(digest, EXPECTED_CSV_SHA256)

    def test_creditsmart_report_is_not_included(self) -> None:
        combined = " ".join(
            self.result.transactions.astype(str).fillna("").to_numpy().ravel()
        ).lower()
        self.assertNotIn("creditsmart", combined)
        self.assertNotIn("year-to-date", combined)


if __name__ == "__main__":
    unittest.main()
