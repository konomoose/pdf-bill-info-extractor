from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.bill_extractor.pdf_processor import (
    ExtractionResult,
    PDFProcessingError,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
)
from src.bill_extractor.statement_metadata import (
    StatementMetadata,
)
from src.bill_extractor.workflow import (
    run_extraction_workflow,
)


RAW_COLUMNS = [
    "Transaction date",
    "Posting date",
    "Description",
    "Amount",
]


def make_profile(
    input_root: Path,
    output_root: Path,
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id=(
            "capital_one_mastercard_v1"
        ),
        display_name="Test Card",
        profile_version=1,
        institution="Test Bank",
        document_type=(
            "credit_card_statement"
        ),
        parser="capital_one_mastercard",
        input_folder=input_root,
        output_folder=output_root,
        file_pattern="*.pdf",
        recursive=True,
        preserve_subfolders=True,
        required_headers=tuple(
            RAW_COLUMNS
        ),
        excluded_page_phrases=(),
        line_tolerance=2.5,
        continuation_gap=18.0,
        source_path=Path(
            "config/profiles/test.json"
        ),
    )


def make_result(
    pdf_file: Path,
    description: str,
) -> ExtractionResult:
    metadata = StatementMetadata(
        source_file=pdf_file,
        profile_id=(
            "capital_one_mastercard_v1"
        ),
        institution="Test Bank",
        document_type=(
            "credit_card_statement"
        ),
        statement_start_date=(
            date(2025, 3, 1)
        ),
        statement_end_date=(
            date(2025, 3, 31)
        ),
    )

    transactions = pd.DataFrame(
        [
            {
                "Transaction date": "Mar 14",
                "Posting date": "Mar 15",
                "Description": description,
                "Amount": "10.00",
            }
        ],
        columns=RAW_COLUMNS,
    )

    return ExtractionResult(
        transactions=transactions,
        source_pages=(1,),
        ghostscript_path=None,
        metadata=metadata,
    )


def make_processor_factory(
    outcomes: dict[
        Path,
        ExtractionResult | Exception,
    ],
    *,
    skip_reasons: dict[
        Path,
        str,
    ] | None = None,
):
    reasons = skip_reasons or {}

    class FakeProcessor:
        def __init__(
            self,
            *,
            profile: ExtractionProfile,
        ) -> None:
            self.profile = profile

        def non_transaction_document_reason(
            self,
            pdf_file: Path,
        ) -> str | None:
            return reasons.get(
                pdf_file.resolve()
            )

        def extract_transactions(
            self,
            pdf_file: Path,
        ) -> ExtractionResult:
            outcome = outcomes[
                pdf_file.resolve()
            ]

            if isinstance(
                outcome,
                Exception,
            ):
                raise outcome

            return outcome

    return FakeProcessor


class StatementOutputLifecycleTest(
    unittest.TestCase
):
    def test_complete_scan_removes_deleted_pdf_outputs(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = (
                root
                / "csv_output"
                / "test_bank"
            )
            summary_root = root / "summaries"

            input_root.mkdir(
                parents=True
            )

            first_pdf = input_root / "first.pdf"
            second_pdf = input_root / "second.pdf"

            first_pdf.touch()
            second_pdf.touch()

            profile = make_profile(
                input_root,
                output_root,
            )

            first_result = make_result(
                first_pdf,
                "First",
            )
            second_result = make_result(
                second_pdf,
                "Second",
            )

            run_extraction_workflow(
                [profile],
                summary_root=summary_root,
                processor_factory=(
                    make_processor_factory(
                        {
                            first_pdf.resolve(): (
                                first_result
                            ),
                            second_pdf.resolve(): (
                                second_result
                            ),
                        }
                    )
                ),
            )

            first_raw = (
                output_root
                / "first_transactions.csv"
            )
            first_normalized = (
                output_root
                / (
                    "first_"
                    "normalized_transactions.csv"
                )
            )

            second_raw = (
                output_root
                / "second_transactions.csv"
            )
            second_normalized = (
                output_root
                / (
                    "second_"
                    "normalized_transactions.csv"
                )
            )

            unrelated = (
                output_root / "manual_notes.csv"
            )
            unrelated.write_text(
                "preserve me\n",
                encoding="utf-8",
            )

            first_pdf.unlink()

            result = run_extraction_workflow(
                [profile],
                summary_root=summary_root,
                processor_factory=(
                    make_processor_factory(
                        {
                            second_pdf.resolve(): (
                                second_result
                            )
                        }
                    )
                ),
            )

            self.assertEqual(
                result.successful_count,
                1,
            )
            self.assertFalse(
                first_raw.exists()
            )
            self.assertFalse(
                first_normalized.exists()
            )
            self.assertTrue(
                second_raw.is_file()
            )
            self.assertTrue(
                second_normalized.is_file()
            )
            self.assertTrue(
                unrelated.is_file()
            )

            yearly = pd.read_csv(
                output_root
                / "2025"
                / (
                    "test_bank_2025_"
                    "transactions.csv"
                ),
                dtype=str,
                keep_default_na=False,
            )

            self.assertEqual(
                list(yearly["description"]),
                ["Second"],
            )
            self.assertEqual(
                list(output_root.rglob("*.tmp")),
                [],
            )
            self.assertEqual(
                list(output_root.rglob("*.bak")),
                [],
            )

    def test_current_failure_preserves_old_outputs(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = (
                root
                / "csv_output"
                / "test_bank"
            )
            summary_root = root / "summaries"

            input_root.mkdir(
                parents=True
            )

            pdf_file = input_root / "statement.pdf"
            pdf_file.touch()

            profile = make_profile(
                input_root,
                output_root,
            )

            extraction = make_result(
                pdf_file,
                "Original",
            )

            run_extraction_workflow(
                [profile],
                summary_root=summary_root,
                processor_factory=(
                    make_processor_factory(
                        {
                            pdf_file.resolve(): (
                                extraction
                            )
                        }
                    )
                ),
            )

            raw_csv = (
                output_root
                / "statement_transactions.csv"
            )
            normalized_csv = (
                output_root
                / (
                    "statement_"
                    "normalized_transactions.csv"
                )
            )
            yearly_csv = (
                output_root
                / "2025"
                / (
                    "test_bank_2025_"
                    "transactions.csv"
                )
            )

            original_raw = raw_csv.read_bytes()
            original_normalized = (
                normalized_csv.read_bytes()
            )
            original_yearly = (
                yearly_csv.read_bytes()
            )

            result = run_extraction_workflow(
                [profile],
                summary_root=summary_root,
                processor_factory=(
                    make_processor_factory(
                        {
                            pdf_file.resolve(): (
                                PDFProcessingError(
                                    "Temporary failure."
                                )
                            )
                        }
                    )
                ),
            )

            self.assertEqual(
                result.failed_count,
                1,
            )
            self.assertEqual(
                raw_csv.read_bytes(),
                original_raw,
            )
            self.assertEqual(
                normalized_csv.read_bytes(),
                original_normalized,
            )
            self.assertEqual(
                yearly_csv.read_bytes(),
                original_yearly,
            )

    def test_new_skip_removes_previous_outputs(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = (
                root
                / "csv_output"
                / "test_bank"
            )
            summary_root = root / "summaries"

            input_root.mkdir(
                parents=True
            )

            pdf_file = input_root / "statement.pdf"
            pdf_file.touch()

            profile = make_profile(
                input_root,
                output_root,
            )

            extraction = make_result(
                pdf_file,
                "Original",
            )

            run_extraction_workflow(
                [profile],
                summary_root=summary_root,
                processor_factory=(
                    make_processor_factory(
                        {
                            pdf_file.resolve(): (
                                extraction
                            )
                        }
                    )
                ),
            )

            raw_csv = (
                output_root
                / "statement_transactions.csv"
            )
            normalized_csv = (
                output_root
                / (
                    "statement_"
                    "normalized_transactions.csv"
                )
            )
            yearly_csv = (
                output_root
                / "2025"
                / (
                    "test_bank_2025_"
                    "transactions.csv"
                )
            )

            result = run_extraction_workflow(
                [profile],
                summary_root=summary_root,
                processor_factory=(
                    make_processor_factory(
                        {
                            pdf_file.resolve(): (
                                extraction
                            )
                        },
                        skip_reasons={
                            pdf_file.resolve(): (
                                "Annual summary; "
                                "no transaction table."
                            )
                        },
                    )
                ),
            )

            self.assertEqual(
                result.skipped_count,
                1,
            )
            self.assertFalse(
                raw_csv.exists()
            )
            self.assertFalse(
                normalized_csv.exists()
            )
            self.assertFalse(
                yearly_csv.exists()
            )
            self.assertEqual(
                list(output_root.rglob("*.tmp")),
                [],
            )
            self.assertEqual(
                list(output_root.rglob("*.bak")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
