from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz
import pandas as pd

from src.bill_extractor.pdf_processor import PDFProcessingError, VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "config" / "profiles" / "simplii_bank_account.json"
KNOWN_PDF_NAME = "01-jan-2025.pdf"
KNOWN_CSV_NAME = "01-jan-2025_transactions.csv"

TEST_INPUT_ROOT = PROJECT_ROOT / "tests" / "input" / "simplii"
NORMAL_INPUT_ROOT = PROJECT_ROOT / "input" / "simplii"
TEST_OUTPUT_ROOT = PROJECT_ROOT / "tests" / "output" / "simplii"
REFERENCE_CSV = (
    PROJECT_ROOT / "tests" / "reference_output" / "simplii" / KNOWN_CSV_NAME
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
    for root in (TEST_INPUT_ROOT, NORMAL_INPUT_ROOT):
        if not root.is_dir():
            continue
        matches = sorted(root.rglob(KNOWN_PDF_NAME))
        if matches:
            return matches[0]
    return None


def make_first_page_image_only(source_pdf: Path, output_pdf: Path) -> None:
    """Create a temporary copy whose first page is rasterized to one image."""
    with fitz.open(source_pdf) as source, fitz.open() as destination:
        for page_number, page in enumerate(source):
            if page_number == 0:
                image_page = destination.new_page(
                    width=page.rect.width,
                    height=page.rect.height,
                )
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
                image_page.insert_image(image_page.rect, pixmap=pixmap)
            else:
                destination.insert_pdf(
                    source,
                    from_page=page_number,
                    to_page=page_number,
                )
        destination.save(output_pdf)


class SimpliiChequingProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "Simplii regression PDF not found. Place "
                f"{KNOWN_PDF_NAME} under tests/input/simplii or input/simplii."
            )

        cls.profile = load_profile(PROFILE_PATH)
        cls.processor = VisaPDFProcessor(profile=cls.profile)
        cls.result = cls.processor.extract_transactions(cls.pdf_path)

        TEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        cls.output_csv = TEST_OUTPUT_ROOT / KNOWN_CSV_NAME
        cls.result.transactions.to_csv(
            cls.output_csv,
            index=False,
            lineterminator="\n",
        )

    def test_profile_identity_and_paths(self) -> None:
        self.assertEqual(
            self.profile.profile_id,
            "simplii_chequing_account_v1",
        )
        self.assertEqual(self.profile.profile_version, 1)
        self.assertEqual(self.profile.parser, "simplii_chequing_account")
        self.assertEqual(
            self.profile.resolve_input_folder(),
            (PROJECT_ROOT / "input" / "simplii").resolve(),
        )
        self.assertEqual(
            self.profile.resolve_output_folder(),
            (PROJECT_ROOT / "output" / "simplii").resolve(),
        )
        self.assertTrue(self.profile.recursive)
        self.assertTrue(self.profile.preserve_subfolders)

    def test_exact_columns_rows_and_pages(self) -> None:
        transactions = self.result.transactions
        self.assertEqual(list(transactions.columns), EXPECTED_COLUMNS)
        self.assertEqual(len(transactions), 46)
        self.assertEqual(self.result.source_pages, (1, 2, 3, 4))
        self.assertTrue(transactions["Balance"].ne("").all())

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

        self.assertAlmostEqual(float(funds_out), 6957.11, places=2)
        self.assertAlmostEqual(float(funds_in), 9641.03, places=2)

    def test_first_and_last_transactions(self) -> None:
        first = self.result.transactions.iloc[0].to_dict()
        last = self.result.transactions.iloc[-1].to_dict()

        self.assertEqual(first["Trans. date"], "Dec 30")
        self.assertEqual(first["Eff. date"], "Dec 30")
        self.assertEqual(first["Transaction"], "MOORES CLOTHING")
        self.assertEqual(first["Funds out"], "263.13")
        self.assertEqual(first["Funds in"], "")
        self.assertEqual(first["Balance"].replace(",", ""), "4480.74")

        self.assertEqual(last["Trans. date"], "Jan 29")
        self.assertEqual(last["Eff. date"], "Jan 30")
        self.assertEqual(last["Transaction"], "INTEREST")
        self.assertEqual(last["Funds out"], "")
        self.assertEqual(last["Funds in"], "0.03")
        self.assertEqual(last["Balance"].replace(",", ""), "7427.79")

    def test_candidate_csv_is_created(self) -> None:
        self.assertTrue(self.output_csv.is_file())

    def test_reference_output_matches_when_available(self) -> None:
        if not REFERENCE_CSV.is_file():
            self.skipTest(
                "No verified Simplii reference CSV found under "
                "tests/reference_output/simplii."
            )

        reference = pd.read_csv(
            REFERENCE_CSV,
            dtype=str,
            keep_default_na=False,
        )
        if list(reference.columns) != EXPECTED_COLUMNS:
            self.skipTest(
                "The local Simplii reference CSV uses the older schema without "
                "Balance. Replace it after verifying the new candidate CSV."
            )

        actual = self.result.transactions.astype(str).reset_index(drop=True)
        pd.testing.assert_frame_equal(
            actual,
            reference.reset_index(drop=True),
            check_dtype=False,
        )


class SimpliiIncompletePDFProtectionTest(unittest.TestCase):
    def test_image_only_first_transaction_page_is_rejected(self) -> None:
        source_pdf = find_local_test_pdf()
        if source_pdf is None:
            self.fail(
                "The full-text Simplii regression PDF is required for the "
                "partial-image protection test."
            )

        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with tempfile.TemporaryDirectory() as temporary_folder:
            partial_pdf = Path(temporary_folder) / "simplii_page1_image_only.pdf"
            make_first_page_image_only(source_pdf, partial_pdf)

            with fitz.open(partial_pdf) as document:
                self.assertEqual(len(document[0].get_text("text").strip()), 0)
                self.assertGreater(len(document[1].get_text("text").strip()), 0)

            with self.assertRaisesRegex(
                PDFProcessingError,
                "totals do not match",
            ):
                processor.extract_transactions(partial_pdf)


if __name__ == "__main__":
    unittest.main()
