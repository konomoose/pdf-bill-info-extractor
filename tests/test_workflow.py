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
from src.bill_extractor.transaction_normalizer import (
    NORMALIZED_COLUMNS,
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
    *,
    profile_id: str,
    display_name: str,
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id=profile_id,
        display_name=display_name,
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
        required_headers=tuple(RAW_COLUMNS),
        excluded_page_phrases=(),
        line_tolerance=2.5,
        continuation_gap=18.0,
        source_path=Path(
            "config/profiles/test.json"
        ),
    )


def make_result(
    pdf_file: Path,
    rows: list[dict[str, str]],
    *,
    start: date,
    end: date,
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
        statement_start_date=start,
        statement_end_date=end,
    )

    return ExtractionResult(
        transactions=pd.DataFrame(
            rows,
            columns=RAW_COLUMNS,
        ),
        source_pages=(1,),
        ghostscript_path=None,
        metadata=metadata,
    )


def make_processor_factory(
    outcomes: dict[
        Path,
        ExtractionResult | Exception,
    ],
):
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
            return None

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

        def save_transactions(
            self,
            pdf_file: Path,
            output_folder: Path,
            result: ExtractionResult,
        ) -> Path:
            output_folder.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path = (
                output_folder
                / (
                    f"{pdf_file.stem}"
                    "_transactions.csv"
                )
            )

            result.transactions.to_csv(
                output_path,
                index=False,
            )

            return output_path

    return FakeProcessor


class WorkflowTest(unittest.TestCase):
    def test_workflow_writes_statement_and_yearly_csvs(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = (
                root / "csv_output" / "test_bank"
            )
            summary_root = (
                root / "csv_output"
            )

            pdf_file = (
                input_root
                / "2024"
                / "statement.pdf"
            )
            pdf_file.parent.mkdir(
                parents=True
            )
            pdf_file.touch()

            profile = make_profile(
                input_root,
                output_root,
                profile_id=(
                    "capital_one_mastercard_v1"
                ),
                display_name="Test Card",
            )

            extraction = make_result(
                pdf_file,
                [
                    {
                        "Transaction date": "Dec 31",
                        "Posting date": "Dec 31",
                        "Description": "First",
                        "Amount": "10.00",
                    },
                    {
                        "Transaction date": "Jan 1",
                        "Posting date": "Jan 2",
                        "Description": "Second",
                        "Amount": "20.00",
                    },
                ],
                start=date(2024, 12, 24),
                end=date(2025, 1, 23),
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
                        }
                    )
                ),
            )

            raw_csv = (
                output_root
                / "2024"
                / "statement_transactions.csv"
            )
            normalized_csv = (
                output_root
                / "2024"
                / (
                    "statement_"
                    "normalized_transactions.csv"
                )
            )
            yearly_2024 = (
                output_root
                / "2024"
                / (
                    "test_bank_2024_"
                    "transactions.csv"
                )
            )
            yearly_2025 = (
                output_root
                / "2025"
                / (
                    "test_bank_2025_"
                    "transactions.csv"
                )
            )

            self.assertTrue(raw_csv.is_file())
            self.assertTrue(
                normalized_csv.is_file()
            )
            self.assertTrue(
                yearly_2024.is_file()
            )
            self.assertTrue(
                yearly_2025.is_file()
            )
            self.assertTrue(
                result.summary_csv.is_file()
            )

            normalized = pd.read_csv(
                normalized_csv,
                dtype=str,
                keep_default_na=False,
            )

            self.assertEqual(
                list(normalized.columns),
                list(NORMALIZED_COLUMNS),
            )
            self.assertEqual(
                list(
                    normalized[
                        "transaction_date"
                    ]
                ),
                [
                    "2024-12-31",
                    "2025-01-01",
                ],
            )
            self.assertEqual(
                result.successful_count,
                1,
            )
            self.assertEqual(
                result.transaction_count,
                2,
            )
            self.assertEqual(
                {
                    item.year
                    for item in (
                        result.yearly_outputs
                    )
                },
                {2024, 2025},
            )

    def test_normalization_failure_preserves_existing_outputs(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = (
                root / "csv_output" / "test_bank"
            )

            pdf_file = (
                input_root
                / "2025"
                / "statement.pdf"
            )
            pdf_file.parent.mkdir(
                parents=True
            )
            pdf_file.touch()

            profile = make_profile(
                input_root,
                output_root,
                profile_id=(
                    "capital_one_mastercard_v1"
                ),
                display_name="Test Card",
            )

            extraction = make_result(
                pdf_file,
                [
                    {
                        "Transaction date": "Mar 14",
                        "Posting date": "Mar 15",
                        "Description": "",
                        "Amount": "10.00",
                    }
                ],
                start=date(2025, 3, 1),
                end=date(2025, 3, 31),
            )

            destination = (
                output_root / "2025"
            )
            destination.mkdir(
                parents=True
            )

            raw_csv = (
                destination
                / "statement_transactions.csv"
            )
            normalized_csv = (
                destination
                / (
                    "statement_"
                    "normalized_transactions.csv"
                )
            )

            raw_csv.write_text(
                "existing raw output\n",
                encoding="utf-8",
            )
            normalized_csv.write_text(
                "existing normalized output\n",
                encoding="utf-8",
            )

            result = run_extraction_workflow(
                [profile],
                summary_root=(
                    root / "csv_output"
                ),
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

            self.assertEqual(
                result.failed_count,
                1,
            )
            self.assertEqual(
                raw_csv.read_text(
                    encoding="utf-8"
                ),
                "existing raw output\n",
            )
            self.assertEqual(
                normalized_csv.read_text(
                    encoding="utf-8"
                ),
                "existing normalized output\n",
            )

    def test_shared_output_root_is_combined_once(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = (
                root / "csv_output" / "test_bank"
            )

            first_input = root / "first"
            second_input = root / "second"

            first_pdf = (
                first_input
                / "2025"
                / "first.pdf"
            )
            second_pdf = (
                second_input
                / "2025"
                / "second.pdf"
            )

            first_pdf.parent.mkdir(
                parents=True
            )
            second_pdf.parent.mkdir(
                parents=True
            )
            first_pdf.touch()
            second_pdf.touch()

            first_profile = make_profile(
                first_input,
                output_root,
                profile_id=(
                    "capital_one_mastercard_v1"
                ),
                display_name="First Profile",
            )
            second_profile = make_profile(
                second_input,
                output_root,
                profile_id=(
                    "capital_one_mastercard_v1"
                ),
                display_name="Second Profile",
            )

            outcomes = {
                first_pdf.resolve(): make_result(
                    first_pdf,
                    [
                        {
                            "Transaction date": (
                                "Mar 1"
                            ),
                            "Posting date": "Mar 2",
                            "Description": "First",
                            "Amount": "10.00",
                        }
                    ],
                    start=date(2025, 3, 1),
                    end=date(2025, 3, 31),
                ),
                second_pdf.resolve(): make_result(
                    second_pdf,
                    [
                        {
                            "Transaction date": (
                                "Mar 3"
                            ),
                            "Posting date": "Mar 4",
                            "Description": "Second",
                            "Amount": "20.00",
                        }
                    ],
                    start=date(2025, 3, 1),
                    end=date(2025, 3, 31),
                ),
            }

            result = run_extraction_workflow(
                [
                    first_profile,
                    second_profile,
                ],
                summary_root=(
                    root / "csv_output"
                ),
                processor_factory=(
                    make_processor_factory(
                        outcomes
                    )
                ),
            )

            yearly_path = (
                output_root
                / "2025"
                / (
                    "test_bank_2025_"
                    "transactions.csv"
                )
            )

            yearly = pd.read_csv(
                yearly_path,
                dtype=str,
                keep_default_na=False,
            )

            self.assertEqual(len(yearly), 2)
            self.assertEqual(
                list(yearly["description"]),
                ["First", "Second"],
            )
            self.assertEqual(
                len(result.yearly_outputs),
                1,
            )
            self.assertEqual(
                result.yearly_outputs[
                    0
                ].transaction_count,
                2,
            )

    def test_failure_does_not_stop_other_files(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = (
                root / "csv_output" / "test_bank"
            )

            good_pdf = (
                input_root / "good.pdf"
            )
            bad_pdf = (
                input_root / "bad.pdf"
            )

            input_root.mkdir(parents=True)
            good_pdf.touch()
            bad_pdf.touch()

            profile = make_profile(
                input_root,
                output_root,
                profile_id=(
                    "capital_one_mastercard_v1"
                ),
                display_name="Test Card",
            )

            outcomes = {
                good_pdf.resolve(): make_result(
                    good_pdf,
                    [
                        {
                            "Transaction date": (
                                "Mar 1"
                            ),
                            "Posting date": "Mar 2",
                            "Description": "Good",
                            "Amount": "10.00",
                        }
                    ],
                    start=date(2025, 3, 1),
                    end=date(2025, 3, 31),
                ),
                bad_pdf.resolve(): (
                    PDFProcessingError(
                        "Unreadable statement."
                    )
                ),
            }

            result = run_extraction_workflow(
                [profile],
                summary_root=(
                    root / "csv_output"
                ),
                processor_factory=(
                    make_processor_factory(
                        outcomes
                    )
                ),
            )

            self.assertEqual(
                result.successful_count,
                1,
            )
            self.assertEqual(
                result.failed_count,
                1,
            )
            self.assertEqual(
                result.transaction_count,
                1,
            )

            failures = [
                item
                for item in result.files
                if item.status == "Failed"
            ]

            self.assertEqual(
                failures[0].error,
                "Unreadable statement.",
            )


if __name__ == "__main__":
    unittest.main()
