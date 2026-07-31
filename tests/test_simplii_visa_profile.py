from __future__ import annotations

import unittest
from pathlib import Path

from src.bill_extractor.pdf_processor import VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROFILE_PATH = (
    PROJECT_ROOT / "config" / "profiles" / "simplii_visa.json"
)

OLD_LAYOUT_PDF = (
    PROJECT_ROOT
    / "editable_input"
    / "simplii_visa"
    / "2024"
    / "onlineStatement-1.pdf"
)

EXPECTED_COLUMNS = [
    "Trans date",
    "Post date",
    "Description",
    "Spend Categories",
    "Amount($)",
]


class SimpliiVisaProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_profile(PROFILE_PATH)

    def test_profile_identity_and_paths(self) -> None:
        self.assertEqual(self.profile.profile_id, "simplii_visa_v1")
        self.assertEqual(self.profile.profile_version, 1)
        self.assertEqual(self.profile.institution, "Simplii Financial")

        # Simplii Visa intentionally reuses the CIBC-style card parser.
        self.assertEqual(self.profile.parser, "cibc_credit_card")

        self.assertEqual(
            self.profile.resolve_input_folder(),
            (PROJECT_ROOT / "editable_input" / "simplii_visa").resolve(),
        )

        self.assertEqual(
            self.profile.resolve_output_folder(),
            (PROJECT_ROOT / "csv_output" / "simplii_visa").resolve(),
        )

        self.assertTrue(self.profile.recursive)
        self.assertTrue(self.profile.preserve_subfolders)

    def test_old_layout_without_spend_categories(self) -> None:
        if not OLD_LAYOUT_PDF.is_file():
            self.skipTest(
                "Local Simplii Visa 2024 regression statement not available."
            )

        result = VisaPDFProcessor(
            profile=self.profile
        ).extract_transactions(OLD_LAYOUT_PDF)

        self.assertEqual(
            list(result.transactions.columns),
            EXPECTED_COLUMNS,
        )
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.source_pages, (2,))

        # Older Simplii Visa layout has no Spend Categories column.
        self.assertEqual(
            result.transactions.iloc[0]["Spend Categories"],
            "",
        )


if __name__ == "__main__":
    unittest.main()
