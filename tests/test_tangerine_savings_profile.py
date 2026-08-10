from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
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
    / "tangerine_savings_account.json"
)

EXPECTED_COLUMNS = [
    "Date",
    "Description",
    "Withdrawals ($)",
    "Deposits ($)",
    "Balance ($)",
]
EXPECTED_NORMALIZED_OUTPUT_COLUMNS = [
    "transaction_date",
    "description",
    "withdrawal",
    "deposit",
    "balance",
]


def add_headers(page: fitz.Page, y: float) -> None:
    page.insert_text((50, y), "Transaction Date")
    page.insert_text((170, y), "Transaction Description")
    page.insert_text((410, y), "Amount($)")
    page.insert_text((510, y), "Balance($)")


def add_balance_row(
    page: fitz.Page,
    y: float,
    transaction_date: str,
    label: str,
    balance: str,
) -> None:
    page.insert_text((50, y), transaction_date)
    page.insert_text((170, y), label)
    page.insert_text((410, y), "0.00")
    page.insert_text((510, y), balance)


def add_transaction_row(
    page: fitz.Page,
    y: float,
    transaction_date: str,
    description_lines: list[str],
    amount: str,
    balance: str,
    *,
    anchor_offset: float = 0.0,
    line_gap: float = 13.0,
) -> float:
    anchor_y = y + anchor_offset
    page.insert_text((50, anchor_y), transaction_date)
    page.insert_text((170, y), description_lines[0])
    page.insert_text((410, anchor_y), amount)
    page.insert_text((510, anchor_y), balance)

    for offset, line in enumerate(description_lines[1:], start=1):
        page.insert_text((170, y + (offset * line_gap)), line)

    last_y = max(
        anchor_y,
        y + ((len(description_lines) - 1) * line_gap),
    )
    return last_y + 22


def write_tangerine_savings_pdf(
    path: Path,
    *,
    mismatch: bool = False,
    outside_period: bool = False,
    final_transaction: bool = False,
) -> None:
    with fitz.open() as document:
        intro = document.new_page(width=612, height=792)
        intro.insert_text(
            (50, 40),
            "January 01, 2026 To January 31, 2026",
        )
        intro.insert_text((50, 80), "Synthetic notice page")

        page = document.new_page(width=612, height=792)
        add_headers(page, 70)

        y = 100
        add_balance_row(
            page,
            y,
            "01 Jan 2026",
            "Opening Balance",
            "1,000.00",
        )
        y += 24
        y = add_transaction_row(
            page,
            y,
            "10 Jan 2026" if not outside_period else "01 Feb 2026",
            ["Same Baseline Purchase"],
            "25.00",
            "975.00" if not mismatch else "976.00",
        )
        y = add_transaction_row(
            page,
            y,
            "14 Jan 2026",
            ["Interest Credit"],
            "1.25",
            "976.25",
        )
        y = add_transaction_row(
            page,
            y,
            "14 Jan 2026",
            ["Transfer From Chequing"],
            "100.00",
            "1,076.25",
        )
        y = add_transaction_row(
            page,
            y,
            "15 Jan 2026",
            ["Offset Wrapped Description", "Second Line", "Third Line"],
            "10.00",
            "1,066.25",
            anchor_offset=5.4,
            line_gap=10.8,
        )
        y = add_transaction_row(
            page,
            y,
            "16 Jan 2026",
            ["Description Above Anchor"],
            "20.00",
            "1,046.25",
            anchor_offset=5.4,
        )

        if final_transaction:
            y = add_transaction_row(
                page,
                y,
                "31 Jan 2026",
                ["Final Interest"],
                "0.75",
                "1,047.00",
            )
            add_balance_row(
                page,
                y,
                "31 Jan 2026",
                "Closing Balance",
                "1,047.00",
            )
        else:
            add_balance_row(
                page,
                y,
                "31 Jan 2026",
                "Closing Balance",
                "1,046.25",
            )

        page.insert_text((170, y + 18), "Synthetic Footer")

        document.save(path)


class TangerineSavingsProfileTest(unittest.TestCase):
    def test_profile_identity_paths_and_parser_registration(self) -> None:
        profile = load_profile(PROFILE_PATH)

        self.assertEqual(
            profile.profile_id,
            "tangerine_savings_account_v1",
        )
        self.assertEqual(
            profile.display_name,
            "Tangerine Savings Account",
        )
        self.assertEqual(profile.institution, "Tangerine Bank")
        self.assertEqual(
            profile.document_type,
            "bank_account_statement",
        )
        self.assertEqual(profile.parser, "tangerine_savings_account")
        self.assertIn(profile.parser, SUPPORTED_PARSERS)
        self.assertEqual(
            profile.resolve_input_folder(),
            (
                PROJECT_ROOT
                / "editable_input"
                / "tangerine_savings"
            ).resolve(),
        )
        self.assertEqual(
            profile.resolve_output_folder(),
            (
                PROJECT_ROOT
                / "csv_output"
                / "tangerine_savings"
            ).resolve(),
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
            write_tangerine_savings_pdf(pdf_path)

            first = processor.extract_transactions(pdf_path)
            second = processor.extract_transactions(pdf_path)

        transactions = first.transactions
        pd.testing.assert_frame_equal(transactions, second.transactions)
        self.assertEqual(list(transactions.columns), EXPECTED_COLUMNS)
        self.assertEqual(first.source_pages, (2,))
        self.assertEqual(len(transactions), 5)

        self.assertEqual(
            first.metadata.statement_start_date,
            date(2026, 1, 1),
        )
        self.assertEqual(
            first.metadata.statement_end_date,
            date(2026, 1, 31),
        )

        descriptions = list(transactions["Description"])
        self.assertNotIn("Opening Balance", descriptions)
        self.assertNotIn("Closing Balance", descriptions)
        self.assertNotIn(
            "Synthetic Footer",
            " ".join(descriptions),
        )
        self.assertIn(
            "Offset Wrapped Description Second Line Third Line",
            descriptions,
        )
        self.assertIn("Description Above Anchor", descriptions)

        self.assertEqual(transactions.iloc[0]["Date"], "2026-01-10")
        self.assertEqual(transactions.iloc[0]["Withdrawals ($)"], "25.00")
        self.assertEqual(transactions.iloc[0]["Deposits ($)"], "")
        self.assertEqual(transactions.iloc[1]["Description"], "Interest Credit")
        self.assertEqual(transactions.iloc[1]["Withdrawals ($)"], "")
        self.assertEqual(transactions.iloc[1]["Deposits ($)"], "1.25")
        self.assertEqual(transactions.iloc[2]["Date"], "2026-01-14")
        self.assertEqual(transactions.iloc[2]["Deposits ($)"], "100.00")
        self.assertEqual(transactions.iloc[3]["Withdrawals ($)"], "10.00")
        self.assertEqual(transactions.iloc[4]["Withdrawals ($)"], "20.00")

    def test_normalized_schema_uses_canonical_internal_columns(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "statement.pdf"
            write_tangerine_savings_pdf(pdf_path)

            result = processor.extract_transactions(pdf_path)
            normalized = normalize_transactions(
                result.transactions,
                result.metadata,
            )

        self.assertEqual(
            normalized.iloc[0]["transaction_date"],
            "2026-01-10",
        )
        self.assertEqual(normalized.iloc[0]["withdrawal"], "25.00")
        self.assertEqual(normalized.iloc[1]["deposit"], "1.25")
        self.assertEqual(
            normalized.iloc[0]["profile_id"],
            "tangerine_savings_account_v1",
        )
        self.assertEqual(
            list(normalized.columns),
            list(NORMALIZED_COLUMNS),
        )

    def test_workflow_exports_projected_statement_and_yearly_csvs(
        self,
    ) -> None:
        loaded_profile = load_profile(PROFILE_PATH)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = root / "csv_output" / "tangerine_savings"
            pdf_path = input_root / "2026" / "statement.pdf"
            pdf_path.parent.mkdir(parents=True)
            write_tangerine_savings_pdf(pdf_path)

            profile = replace(
                loaded_profile,
                input_folder=input_root,
                output_folder=output_root,
            )

            result = run_extraction_workflow(
                [profile],
                summary_root=root / "csv_output",
            )

            self.assertEqual(result.successful_count, 1)
            self.assertEqual(result.transaction_count, 5)

            normalized_csv = (
                output_root
                / "2026"
                / "statement_normalized_transactions.csv"
            )
            yearly_csv = (
                output_root
                / "2026"
                / "tangerine_savings_2026_transactions.csv"
            )

            statement = pd.read_csv(
                normalized_csv,
                dtype=str,
                keep_default_na=False,
            )
            yearly = pd.read_csv(
                yearly_csv,
                dtype=str,
                keep_default_na=False,
            )

        self.assertEqual(
            list(statement.columns),
            EXPECTED_NORMALIZED_OUTPUT_COLUMNS,
        )
        self.assertEqual(
            list(yearly.columns),
            EXPECTED_NORMALIZED_OUTPUT_COLUMNS,
        )
        self.assertEqual(
            list(statement["description"]),
            [
                "Same Baseline Purchase",
                "Interest Credit",
                "Transfer From Chequing",
                "Offset Wrapped Description Second Line Third Line",
                "Description Above Anchor",
            ],
        )

    def test_final_transaction_before_closing_balance_is_included(
        self,
    ) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "final-statement.pdf"
            write_tangerine_savings_pdf(pdf_path, final_transaction=True)

            result = processor.extract_transactions(pdf_path)

        self.assertEqual(len(result.transactions), 6)
        self.assertEqual(
            result.transactions.iloc[-1]["Description"],
            "Final Interest",
        )
        self.assertEqual(result.transactions.iloc[-1]["Deposits ($)"], "0.75")
        self.assertEqual(result.transactions.iloc[-1]["Balance ($)"], "1,047.00")

    def test_balance_mismatch_fails(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "bad-statement.pdf"
            write_tangerine_savings_pdf(pdf_path, mismatch=True)

            with self.assertRaisesRegex(
                PDFProcessingError,
                "does not reconcile",
            ):
                processor.extract_transactions(pdf_path)

    def test_row_date_outside_statement_period_fails(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "outside-period.pdf"
            write_tangerine_savings_pdf(pdf_path, outside_period=True)

            with self.assertRaisesRegex(
                PDFProcessingError,
                "outside the statement period",
            ):
                processor.extract_transactions(pdf_path)

    def test_decimal_reconciliation_is_exact(self) -> None:
        profile = load_profile(PROFILE_PATH)
        processor = VisaPDFProcessor(profile=profile)

        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "decimal-statement.pdf"
            with fitz.open() as document:
                page = document.new_page(width=612, height=792)
                page.insert_text(
                    (50, 40),
                    "January 01, 2026 To January 31, 2026",
                )
                add_headers(page, 70)
                add_balance_row(
                    page,
                    100,
                    "01 Jan 2026",
                    "Opening Balance",
                    "10.00",
                )
                y = add_transaction_row(
                    page,
                    124,
                    "02 Jan 2026",
                    ["Small Debit"],
                    "0.10",
                    "9.90",
                )
                y = add_transaction_row(
                    page,
                    y,
                    "03 Jan 2026",
                    ["Small Credit"],
                    "0.20",
                    "10.10",
                )
                add_balance_row(
                    page,
                    y,
                    "31 Jan 2026",
                    "Closing Balance",
                    "10.10",
                )
                document.save(pdf_path)

            result = processor.extract_transactions(pdf_path)

        self.assertEqual(len(result.transactions), 2)
        ending_balance = processor._parse_amount(
            result.transactions.iloc[-1]["Balance ($)"]
        )
        self.assertEqual(ending_balance, Decimal("10.10"))


if __name__ == "__main__":
    unittest.main()
