from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
import pandas as pd

from src.bill_extractor.pdf_processor import (
    PDFProcessingError,
    SUPPORTED_PARSERS,
    VisaPDFProcessor,
)
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import (
    NORMALIZED_COLUMNS,
    normalize_transactions,
)
from src.bill_extractor.workflow import run_extraction_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    PROJECT_ROOT
    / "config"
    / "profiles"
    / "eq_bank_account.json"
)

EXPECTED_COLUMNS = [
    "Date",
    "Description",
    "Withdrawals",
    "Deposits",
    "Balance",
]
EXPECTED_NORMALIZED_OUTPUT_COLUMNS = [
    "transaction_date",
    "description",
    "withdrawal",
    "deposit",
    "balance",
]


def add_headers(page: fitz.Page, y: float) -> None:
    page.insert_text((50, y), "Date")
    page.insert_text((130, y), "Description")
    page.insert_text((360, y), "Withdrawals")
    page.insert_text((450, y), "Deposits")
    page.insert_text((535, y), "Balance")


def add_summary(
    page: fitz.Page,
    *,
    opening: str,
    deposits: str,
    withdrawals: str,
    closing: str,
    omitted_value: str | None = None,
    duplicate_value: str | None = None,
    label_y_offset: float = 0.0,
    value_y_offset: float = 0.0,
    value_x_offset: float = 0.0,
) -> None:
    rows = {
        "opening": (60, "", opening, "Opening balance"),
        "deposits": (76, "+", deposits, "Total deposits"),
        "withdrawals": (92, "-", withdrawals, "Total withdrawals"),
        "closing": (108, "=", closing, "Closing balance"),
    }

    page.insert_text((50, 45), "Your activity summary")

    for key, (y, operator, amount, label) in rows.items():
        label_y = y + label_y_offset
        value_y = y + value_y_offset
        value_x = 238 + value_x_offset

        page.insert_text((50, label_y), label)

        if key != omitted_value:
            if operator:
                page.insert_text((224 + value_x_offset, value_y), operator)

            page.insert_text((value_x, value_y), f"${amount}")

            if key == duplicate_value:
                page.insert_text((value_x + 45, value_y), "$1.00")

        if key == "closing":
            page.insert_text((320, label_y), "Total interest earned")
            page.insert_text((445, value_y), "$0.40")


def add_transaction_row(
    page: fitz.Page,
    y: float,
    row_date: str,
    description_lines: list[str],
    withdrawal: str,
    deposit: str,
    balance: str,
    *,
    line_gap: float = 12.0,
) -> float:
    page.insert_text((50, y), row_date)
    page.insert_text((130, y), description_lines[0])

    for offset, line in enumerate(description_lines[1:], start=1):
        page.insert_text((130, y + (offset * line_gap)), line)

    if withdrawal:
        page.insert_text((360, y), f"${withdrawal}")
    if deposit:
        page.insert_text((450, y), f"${deposit}")
    page.insert_text((535, y), f"${balance}")

    return y + 25 + ((len(description_lines) - 1) * line_gap)


def write_eq_pdf(
    path: Path,
    *,
    period: str = "January 1, 2026 to January 31, 2026",
    opening: str = "250.00",
    deposits: str = "125.40",
    withdrawals: str = "50.00",
    closing: str = "325.40",
    rows: tuple[dict[str, object], ...] | None = None,
    footer: bool = True,
    omitted_summary_value: str | None = None,
    duplicate_summary_value: str | None = None,
    summary_label_y_offset: float = 0.0,
    summary_value_y_offset: float = 0.0,
    summary_value_x_offset: float = 0.0,
) -> None:
    if rows is None:
        rows = (
            {
                "date": "Jan 05",
                "description": ["Synthetic deposit description"],
                "withdrawal": "",
                "deposit": "100.00",
                "balance": "350.00",
            },
            {
                "date": "Jan 10",
                "description": ["Synthetic withdrawal"],
                "withdrawal": "25.00",
                "deposit": "",
                "balance": "325.00",
            },
            {
                "date": "Jan 10",
                "description": ["Wrapped synthetic description", "continued"],
                "withdrawal": "25.00",
                "deposit": "",
                "balance": "300.00",
            },
            {
                "date": "Jan 31",
                "description": ["Interest received"],
                "withdrawal": "",
                "deposit": "25.40",
                "balance": "325.40",
            },
        )

    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((50, 25), period)
        add_summary(
            page,
            opening=opening,
            deposits=deposits,
            withdrawals=withdrawals,
            closing=closing,
            omitted_value=omitted_summary_value,
            duplicate_value=duplicate_summary_value,
            label_y_offset=summary_label_y_offset,
            value_y_offset=summary_value_y_offset,
            value_x_offset=summary_value_x_offset,
        )
        add_headers(page, 140)

        y = 170
        for row in rows:
            y = add_transaction_row(
                page,
                y,
                str(row["date"]),
                list(row["description"]),
                str(row["withdrawal"]),
                str(row["deposit"]),
                str(row["balance"]),
            )

        if footer:
            page.insert_text((130, y + 5), "Synthetic Footer")
            page.insert_text((50, y + 21), "Page 1 of 1")

        document.save(path)


class EQBankProfileTest(unittest.TestCase):
    def test_profile_identity_paths_and_parser_registration(self) -> None:
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(profile.profile_id, "eq_bank_account_v1")
        self.assertEqual(profile.display_name, "EQ Bank Account")
        self.assertEqual(profile.institution, "EQ Bank")
        self.assertEqual(profile.document_type, "bank_account_statement")
        self.assertEqual(profile.parser, "eq_bank_account")
        self.assertIn(profile.parser, SUPPORTED_PARSERS)
        self.assertEqual(
            profile.resolve_input_folder(),
            (PROJECT_ROOT / "editable_input" / "eq_bank").resolve(),
        )
        self.assertEqual(
            profile.resolve_output_folder(),
            (PROJECT_ROOT / "csv_output" / "eq_bank").resolve(),
        )
        self.assertEqual(list(profile.required_headers), EXPECTED_COLUMNS)
        self.assertEqual(
            list(profile.normalized_output_columns or ()),
            EXPECTED_NORMALIZED_OUTPUT_COLUMNS,
        )
        self.assertTrue(profile.recursive)
        self.assertTrue(profile.preserve_subfolders)

    def test_synthetic_statement_extracts_and_reconciles(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "statement.pdf"
            write_eq_pdf(pdf_path)

            first = processor.extract_transactions(pdf_path)
            second = processor.extract_transactions(pdf_path)

        transactions = first.transactions
        pd.testing.assert_frame_equal(transactions, second.transactions)
        self.assertEqual(list(transactions.columns), EXPECTED_COLUMNS)
        self.assertEqual(first.source_pages, (1,))
        self.assertEqual(len(transactions), 4)
        self.assertEqual(first.metadata.statement_start_date, date(2026, 1, 1))
        self.assertEqual(first.metadata.statement_end_date, date(2026, 1, 31))

        descriptions = list(transactions["Description"])
        self.assertIn("Synthetic deposit description", descriptions)
        self.assertIn("Synthetic withdrawal", descriptions)
        self.assertIn("Wrapped synthetic description continued", descriptions)
        self.assertIn("Interest received", descriptions)
        self.assertNotIn("Opening balance", " ".join(descriptions))
        self.assertNotIn("Closing balance", " ".join(descriptions))
        self.assertNotIn("Synthetic Footer", " ".join(descriptions))
        self.assertEqual(transactions.iloc[0]["Date"], "2026-01-05")
        self.assertEqual(transactions.iloc[0]["Deposits"], "100.00")
        self.assertEqual(transactions.iloc[1]["Withdrawals"], "25.00")
        self.assertEqual(transactions.iloc[2]["Date"], "2026-01-10")
        self.assertEqual(transactions.iloc[3]["Deposits"], "25.40")

    def test_positioned_activity_summary_values_are_associated_by_column(
        self,
    ) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)
        rows = (
            {
                "date": "Jan 05",
                "description": ["Synthetic deposit description"],
                "withdrawal": "",
                "deposit": "100.00",
                "balance": "350.00",
            },
            {
                "date": "Jan 10",
                "description": ["Synthetic withdrawal"],
                "withdrawal": "25.00",
                "deposit": "",
                "balance": "325.00",
            },
        )

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "summary-columns.pdf"
            write_eq_pdf(
                pdf_path,
                deposits="100.00",
                withdrawals="25.00",
                closing="325.00",
                rows=rows,
            )

            with fitz.open(pdf_path) as document:
                summary = processor._extract_eq_summary(document[0])

            result = processor.extract_transactions(pdf_path)

        self.assertEqual(summary["opening"], processor._parse_amount("250.00"))
        self.assertEqual(summary["deposits"], processor._parse_amount("100.00"))
        self.assertEqual(summary["withdrawals"], processor._parse_amount("25.00"))
        self.assertEqual(summary["closing"], processor._parse_amount("325.00"))
        self.assertEqual(len(result.transactions), 2)

    def test_positioned_activity_summary_handles_zero_withdrawals(
        self,
    ) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)
        rows = (
            {
                "date": "Jan 05",
                "description": ["Synthetic deposit description"],
                "withdrawal": "",
                "deposit": "100.00",
                "balance": "350.00",
            },
        )

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "summary-zero-withdrawals.pdf"
            write_eq_pdf(
                pdf_path,
                deposits="100.00",
                withdrawals="0.00",
                closing="350.00",
                rows=rows,
            )

            with fitz.open(pdf_path) as document:
                summary = processor._extract_eq_summary(document[0])

            result = processor.extract_transactions(pdf_path)

        self.assertEqual(summary["withdrawals"], processor._parse_amount("0.00"))
        self.assertEqual(len(result.transactions), 1)

    def test_positioned_activity_summary_allows_small_vertical_offsets(
        self,
    ) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "summary-offsets.pdf"
            write_eq_pdf(
                pdf_path,
                summary_label_y_offset=1.2,
                summary_value_y_offset=-1.1,
            )

            result = processor.extract_transactions(pdf_path)

        self.assertEqual(len(result.transactions), 4)

    def test_positioned_activity_summary_accepts_main_value_near_boundary(
        self,
    ) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "summary-main-boundary.pdf"
            write_eq_pdf(
                pdf_path,
                summary_value_x_offset=35.0,
            )

            with fitz.open(pdf_path) as document:
                summary = processor._extract_eq_summary(document[0])

        self.assertEqual(summary["closing"], processor._parse_amount("325.40"))

    def test_ambiguous_or_missing_summary_value_fails_conservatively(
        self,
    ) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        cases = (
            {"omitted_summary_value": "opening"},
            {"duplicate_summary_value": "closing"},
        )

        for options in cases:
            with self.subTest(options=options):
                with TemporaryDirectory() as directory:
                    pdf_path = Path(directory) / "bad-summary.pdf"
                    write_eq_pdf(pdf_path, **options)

                    with self.assertRaisesRegex(
                        PDFProcessingError,
                        "summary value could not be associated unambiguously",
                    ):
                        processor.extract_transactions(pdf_path)

    def test_cross_year_transaction_dates_resolve_conservatively(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        rows = (
            {
                "date": "Dec 31",
                "description": ["Synthetic year-end deposit"],
                "withdrawal": "",
                "deposit": "10.00",
                "balance": "260.00",
            },
            {
                "date": "Jan 05",
                "description": ["Synthetic new-year withdrawal"],
                "withdrawal": "5.00",
                "deposit": "",
                "balance": "255.00",
            },
        )

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "cross-year.pdf"
            write_eq_pdf(
                pdf_path,
                period="December 15, 2025 to January 15, 2026",
                opening="250.00",
                deposits="10.00",
                withdrawals="5.00",
                closing="255.00",
                rows=rows,
            )

            result = processor.extract_transactions(pdf_path)

        self.assertEqual(list(result.transactions["Date"]), [
            "2025-12-31",
            "2026-01-05",
        ])

    def test_normalized_schema_uses_canonical_internal_columns(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "statement.pdf"
            write_eq_pdf(pdf_path)

            result = processor.extract_transactions(pdf_path)
            normalized = normalize_transactions(
                result.transactions,
                result.metadata,
            )

        self.assertEqual(list(normalized.columns), list(NORMALIZED_COLUMNS))
        self.assertEqual(normalized.iloc[0]["transaction_date"], "2026-01-05")
        self.assertEqual(normalized.iloc[0]["deposit"], "100.00")
        self.assertEqual(normalized.iloc[1]["withdrawal"], "25.00")
        self.assertEqual(normalized.iloc[0]["profile_id"], "eq_bank_account_v1")

    def test_workflow_exports_projected_statement_and_yearly_csvs(self) -> None:
        loaded_profile = load_profile(PROFILE_PATH)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = root / "csv_output" / "eq_bank"
            pdf_path = input_root / "2026" / "statement.pdf"
            pdf_path.parent.mkdir(parents=True)
            write_eq_pdf(pdf_path)

            profile = replace(
                loaded_profile,
                input_folder=input_root,
                output_folder=output_root,
            )
            result = run_extraction_workflow(
                [profile],
                summary_root=root / "csv_output",
            )

            statement = pd.read_csv(
                output_root
                / "2026"
                / "statement_normalized_transactions.csv",
                dtype=str,
                keep_default_na=False,
            )
            yearly = pd.read_csv(
                output_root
                / "2026"
                / "eq_bank_2026_transactions.csv",
                dtype=str,
                keep_default_na=False,
            )

        self.assertEqual(result.successful_count, 1)
        self.assertEqual(result.transaction_count, 4)
        self.assertEqual(
            list(statement.columns),
            EXPECTED_NORMALIZED_OUTPUT_COLUMNS,
        )
        self.assertEqual(
            list(yearly.columns),
            EXPECTED_NORMALIZED_OUTPUT_COLUMNS,
        )

    def test_date_outside_statement_period_fails(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        rows = (
            {
                "date": "Feb 01",
                "description": ["Synthetic outside date"],
                "withdrawal": "",
                "deposit": "1.00",
                "balance": "251.00",
            },
        )

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "outside-period.pdf"
            write_eq_pdf(
                pdf_path,
                deposits="1.00",
                withdrawals="0.00",
                closing="251.00",
                rows=rows,
            )

            with self.assertRaisesRegex(
                PDFProcessingError,
                "could not be resolved uniquely",
            ):
                processor.extract_transactions(pdf_path)

    def test_invalid_direction_columns_fail(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        cases = (
            (
                "both.pdf",
                {
                    "date": "Jan 05",
                    "description": ["Synthetic invalid both"],
                    "withdrawal": "1.00",
                    "deposit": "1.00",
                    "balance": "250.00",
                },
                "both withdrawal and deposit",
            ),
            (
                "neither.pdf",
                {
                    "date": "Jan 05",
                    "description": ["Synthetic invalid neither"],
                    "withdrawal": "",
                    "deposit": "",
                    "balance": "250.00",
                },
                "neither withdrawal nor deposit",
            ),
        )

        for filename, row, message in cases:
            with self.subTest(filename=filename):
                with TemporaryDirectory() as directory:
                    pdf_path = Path(directory) / filename
                    write_eq_pdf(
                        pdf_path,
                        deposits="1.00",
                        withdrawals="1.00",
                        rows=(row,),
                    )

                    with self.assertRaisesRegex(PDFProcessingError, message):
                        processor.extract_transactions(pdf_path)

    def test_reconciliation_failures_are_rejected(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        cases = (
            ("balance-mismatch.pdf", "125.40", "50.00", "325.40", "326.40"),
            ("deposit-total.pdf", "126.40", "50.00", "325.40", "325.40"),
            ("withdrawal-total.pdf", "125.40", "51.00", "325.40", "325.40"),
            ("closing-total.pdf", "125.40", "50.00", "326.40", "325.40"),
        )

        for filename, deposits, withdrawals, closing, last_balance in cases:
            with self.subTest(filename=filename):
                with TemporaryDirectory() as directory:
                    pdf_path = Path(directory) / filename
                    rows = (
                        {
                            "date": "Jan 05",
                            "description": ["Synthetic deposit description"],
                            "withdrawal": "",
                            "deposit": "100.00",
                            "balance": "350.00",
                        },
                        {
                            "date": "Jan 10",
                            "description": ["Synthetic withdrawal"],
                            "withdrawal": "25.00",
                            "deposit": "",
                            "balance": "325.00",
                        },
                        {
                            "date": "Jan 10",
                            "description": ["Wrapped synthetic description"],
                            "withdrawal": "25.00",
                            "deposit": "",
                            "balance": "300.00",
                        },
                        {
                            "date": "Jan 31",
                            "description": ["Interest received"],
                            "withdrawal": "",
                            "deposit": "25.40",
                            "balance": last_balance,
                        },
                    )
                    write_eq_pdf(
                        pdf_path,
                        deposits=deposits,
                        withdrawals=withdrawals,
                        closing=closing,
                        rows=rows,
                    )

                    with self.assertRaises(PDFProcessingError):
                        processor.extract_transactions(pdf_path)


if __name__ == "__main__":
    unittest.main()
