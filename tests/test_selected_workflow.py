from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.bill_extractor.pdf_processor import (
    ExtractionResult,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
)
from src.bill_extractor.statement_metadata import (
    StatementMetadata,
)
from src.bill_extractor.workflow import (
    run_selected_extraction_workflow,
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
    transaction_date: str,
    posting_date: str,
    amount: str,
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

    transactions = pd.DataFrame(
        [
            {
                "Transaction date": (
                    transaction_date
                ),
                "Posting date": posting_date,
                "Description": description,
                "Amount": amount,
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
        ExtractionResult,
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
            return outcomes[
                pdf_file.resolve()
            ]

    return FakeProcessor


class SelectedWorkflowTest(
    unittest.TestCase
):
    def test_single_pdf_keeps_complete_yearly_csv(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            configured_input = (
                root / "configured_input"
            )
            configured_output = (
                root / "configured_output"
            )
            selected_output = (
                root
                / "exports"
                / "selected_bank"
            )

            first_pdf = (
                root
                / "first_selection"
                / "first.pdf"
            )
            second_pdf = (
                root
                / "second_selection"
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

            profile = make_profile(
                configured_input,
                configured_output,
            )

            outcomes = {
                first_pdf.resolve(): make_result(
                    first_pdf,
                    "First",
                    "Mar 1",
                    "Mar 2",
                    "10.00",
                    start=date(2025, 3, 1),
                    end=date(2025, 3, 31),
                ),
                second_pdf.resolve(): make_result(
                    second_pdf,
                    "Second",
                    "Apr 1",
                    "Apr 2",
                    "20.00",
                    start=date(2025, 4, 1),
                    end=date(2025, 4, 30),
                ),
            }

            factory = make_processor_factory(
                outcomes
            )

            run_selected_extraction_workflow(
                profile,
                first_pdf,
                selected_output,
                processor_factory=factory,
            )

            result = (
                run_selected_extraction_workflow(
                    profile,
                    second_pdf,
                    selected_output,
                    processor_factory=factory,
                )
            )

            yearly_path = (
                selected_output
                / "2025"
                / (
                    "selected_bank_2025_"
                    "transactions.csv"
                )
            )

            yearly = pd.read_csv(
                yearly_path,
                dtype=str,
                keep_default_na=False,
            )

            self.assertEqual(
                list(yearly["description"]),
                ["First", "Second"],
            )
            self.assertTrue(
                (
                    selected_output
                    / (
                        "first_"
                        "normalized_transactions.csv"
                    )
                ).is_file()
            )
            self.assertTrue(
                (
                    selected_output
                    / (
                        "second_"
                        "normalized_transactions.csv"
                    )
                ).is_file()
            )
            self.assertEqual(
                result.successful_count,
                1,
            )
            self.assertEqual(
                result.files[0].pdf_file,
                second_pdf.resolve(),
            )
            self.assertEqual(
                result.summary_csv.parent,
                selected_output.resolve(),
            )

    def test_selected_folder_preserves_subfolders(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            configured_input = (
                root / "configured_input"
            )
            configured_output = (
                root / "configured_output"
            )
            selected_input = (
                root / "selected_input"
            )
            selected_output = (
                root
                / "exports"
                / "selected_bank"
            )

            pdf_file = (
                selected_input
                / "2025"
                / "statement.pdf"
            )

            pdf_file.parent.mkdir(
                parents=True
            )
            pdf_file.touch()

            profile = make_profile(
                configured_input,
                configured_output,
            )

            extraction = make_result(
                pdf_file,
                "Folder",
                "May 1",
                "May 2",
                "30.00",
                start=date(2025, 5, 1),
                end=date(2025, 5, 31),
            )

            result = (
                run_selected_extraction_workflow(
                    profile,
                    selected_input,
                    selected_output,
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
            )

            self.assertEqual(
                result.successful_count,
                1,
            )
            self.assertTrue(
                (
                    selected_output
                    / "2025"
                    / (
                        "statement_"
                        "transactions.csv"
                    )
                ).is_file()
            )
            self.assertTrue(
                (
                    selected_output
                    / "2025"
                    / (
                        "statement_"
                        "normalized_transactions.csv"
                    )
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
