from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz
import pandas as pd

from src.bill_extractor.pdf_processor import PDFProcessingError, VisaPDFProcessor
from src.bill_extractor.profile_loader import load_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = PROJECT_ROOT / "config" / "profiles" / "td_visa_credit_card.json"
KNOWN_PDF_NAME = "tk-td-visa.pdf"
KNOWN_CSV_NAME = "tk-td-visa_transactions.csv"

TEST_INPUT_ROOT = PROJECT_ROOT / "tests" / "input" / "td_visa"
NORMAL_INPUT_ROOT = PROJECT_ROOT / "input" / "td_visa"
TEST_OUTPUT_ROOT = PROJECT_ROOT / "tests" / "output" / "td_visa"
REFERENCE_CSV = (
    PROJECT_ROOT / "tests" / "reference_output" / "td_visa" / KNOWN_CSV_NAME
)

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


class _TDLayoutPage:
    """Minimal page object reproducing TD's three-line table heading."""

    number = 0

    def __init__(self) -> None:
        self._words: list[tuple] = []

        def add(x0: float, y0: float, x1: float, y1: float, value: str) -> None:
            self._words.append((x0, y0, x1, y1, value, 0, 0, len(self._words)))

        # Three-line heading with unrelated rewards-panel content to the right.
        add(49.13, 176.13, 92.39, 184.12, "TRANSACTION")
        add(97.48, 176.13, 123.20, 184.12, "POSTING")
        add(362.01, 178.35, 392.44, 188.89, "Previous")
        add(395.67, 178.35, 423.53, 188.89, "Balance")
        add(49.28, 183.10, 64.89, 191.09, "DATE")
        add(97.48, 183.10, 113.09, 191.09, "DATE")
        add(140.40, 183.10, 167.97, 191.09, "ACTIVITY")
        add(170.44, 183.10, 210.12, 191.09, "DESCRIPTION")
        add(309.76, 182.86, 345.12, 190.85, "AMOUNT($)")

        # Previous balance row.
        add(140.71, 193.09, 180.58, 203.69, "PREVIOUS")
        add(183.47, 193.09, 231.67, 203.69, "STATEMENT")
        add(234.47, 193.09, 272.51, 203.69, "BALANCE")
        add(301.99, 192.74, 345.71, 203.48, "$12,849.30")

        # Four transactions. The first posting date is deliberately compact.
        add(47.59, 212.41, 60.83, 222.28, "SEP")
        add(63.26, 212.41, 66.96, 222.28, "3")
        add(95.53, 212.41, 114.81, 222.28, "SEP4")
        add(140.85, 213.25, 176.96, 222.05, "MANULIFE")
        add(178.95, 213.25, 216.10, 222.05, "FfNANCIAL")
        add(218.46, 213.25, 254.01, 222.05, "OAKVILLE")
        add(323.11, 212.54, 344.74, 222.55, "$68.00")
        add(361.31, 212.35, 407.99, 224.85, "RIGHT-PANEL")

        add(47.58, 231.53, 60.77, 241.54, "SEP")
        add(63.81, 231.53, 71.08, 241.54, "14")
        add(95.54, 231.53, 108.73, 241.54, "SEP")
        add(111.77, 231.53, 119.04, 241.54, "16")
        add(140.57, 232.48, 156.48, 241.28, "CIBC")
        add(158.97, 232.48, 192.03, 241.28, "TORONTO")
        add(316.40, 231.29, 344.71, 241.30, "-$350.00")
        add(361.64, 235.52, 393.19, 246.12, "Balance")

        add(47.58, 250.53, 60.83, 260.54, "SEP")
        add(63.82, 250.53, 70.83, 260.54, "16")
        add(95.54, 250.53, 108.79, 260.54, "SEP")
        add(111.78, 250.64, 118.79, 260.51, "18")
        add(140.57, 251.48, 156.48, 260.28, "CIBC")
        add(158.97, 251.48, 192.03, 260.28, "TORONTO")
        add(316.40, 250.53, 344.71, 260.54, "-$300.00")
        add(361.74, 250.35, 407.59, 261.71, "PAYMENT")

        add(47.58, 269.52, 60.83, 279.53, "SEP")
        add(63.82, 269.52, 70.83, 279.53, "19")
        add(95.54, 269.52, 108.79, 279.53, "SEP")
        add(111.77, 269.52, 119.04, 279.53, "19")
        add(140.37, 270.47, 165.09, 279.27, "RETAIL")
        add(167.15, 270.47, 199.97, 279.27, "INTEREST")
        add(319.26, 269.52, 344.49, 279.53, "$287.93")
        add(362.01, 275.11, 393.91, 285.65, "Payment")

        add(140.58, 288.15, 169.21, 299.86, "TOTAL")
        add(172.54, 288.15, 193.63, 299.86, "NEW")
        add(196.59, 288.15, 239.59, 299.86, "BALANCE")
        add(301.03, 288.66, 344.51, 299.40, "$12,555.23")

    def get_text(self, kind: str):
        if kind == "words":
            return self._words
        if kind == "text":
            return (
                "PREVIOUS STATEMENT BALANCE $12,849.30\n"
                "TOTAL NEW BALANCE $12,555.23\n"
            )
        raise ValueError(kind)


class TDVisaLayoutEncodingTest(unittest.TestCase):
    def test_three_line_header_and_right_panel_are_handled(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)
        rows = processor._extract_td_page_transactions(_TDLayoutPage())

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["Transaction date"], "Sep 3")
        self.assertEqual(rows[0]["Posting date"], "Sep 4")
        self.assertEqual(
            rows[0]["Activity description"],
            "MANULIFE FINANCIAL OAKVILLE",
        )
        self.assertEqual(rows[0]["Amount($)"], "68.00")
        self.assertEqual(rows[-1]["Activity description"], "RETAIL INTEREST")
        self.assertNotIn("RIGHT-PANEL", rows[0]["Activity description"])


class TDVisaProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = find_local_test_pdf()
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                "TD Visa regression PDF not found. Place the full-text statement "
                "under tests/input/td_visa/full-text-test."
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
        self.assertEqual(self.profile.profile_id, "td_visa_credit_card_v1")
        self.assertEqual(self.profile.profile_version, 1)
        self.assertEqual(self.profile.parser, "td_visa_credit_card")
        self.assertEqual(
            self.profile.resolve_input_folder(),
            (PROJECT_ROOT / "input" / "td_visa").resolve(),
        )
        self.assertEqual(
            self.profile.resolve_output_folder(),
            (PROJECT_ROOT / "output" / "td_visa").resolve(),
        )
        self.assertTrue(self.profile.recursive)
        self.assertTrue(self.profile.preserve_subfolders)

    def test_exact_columns_rows_and_pages(self) -> None:
        transactions = self.result.transactions
        self.assertEqual(list(transactions.columns), EXPECTED_COLUMNS)
        self.assertEqual(len(transactions), 4)
        self.assertEqual(self.result.source_pages, (1,))

    def test_statement_balance_reconciliation(self) -> None:
        total = pd.to_numeric(
            self.result.transactions["Amount($)"].str.replace(
                ",", "", regex=False
            )
        ).sum()
        self.assertAlmostEqual(float(total), -294.07, places=2)
        self.assertAlmostEqual(12849.30 + float(total), 12555.23, places=2)

    def test_first_and_last_transactions(self) -> None:
        first = self.result.transactions.iloc[0].to_dict()
        last = self.result.transactions.iloc[-1].to_dict()

        self.assertEqual(first["Transaction date"], "Sep 3")
        self.assertEqual(first["Posting date"], "Sep 4")
        self.assertEqual(
            first["Activity description"],
            "MANULIFE FINANCIAL OAKVILLE",
        )
        self.assertEqual(first["Amount($)"], "68.00")

        self.assertEqual(last["Transaction date"], "Sep 19")
        self.assertEqual(last["Posting date"], "Sep 19")
        self.assertEqual(last["Activity description"], "RETAIL INTEREST")
        self.assertEqual(last["Amount($)"], "287.93")

    def test_candidate_csv_is_created(self) -> None:
        self.assertTrue(self.output_csv.is_file())

    def test_reference_output_matches_when_available(self) -> None:
        if not REFERENCE_CSV.is_file():
            self.skipTest(
                "No verified TD Visa reference CSV found under "
                "tests/reference_output/td_visa."
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


class TDVisaIncompletePDFProtectionTest(unittest.TestCase):
    def test_image_only_transaction_page_is_rejected(self) -> None:
        source_pdf = find_local_test_pdf()
        if source_pdf is None:
            self.fail(
                "The full-text TD Visa regression PDF is required for the "
                "partial-image protection test."
            )

        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with tempfile.TemporaryDirectory() as temporary_folder:
            partial_pdf = Path(temporary_folder) / "td_page1_image_only.pdf"
            make_first_page_image_only(source_pdf, partial_pdf)

            with fitz.open(partial_pdf) as document:
                self.assertEqual(len(document[0].get_text("text").strip()), 0)
                self.assertGreater(len(document[1].get_text("text").strip()), 0)

            with self.assertRaisesRegex(
                PDFProcessingError,
                "transaction page may be image-only",
            ):
                processor.extract_transactions(partial_pdf)


if __name__ == "__main__":
    unittest.main()
