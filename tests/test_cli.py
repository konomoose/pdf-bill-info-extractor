from io import StringIO
from pathlib import Path
from unittest.mock import patch
import unittest

from src.bill_extractor.cli import (
    main,
    select_profiles,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
)
from src.bill_extractor.workflow import (
    WorkflowFileResult,
    WorkflowResult,
)


def make_profile(
    profile_id: str,
    display_name: str,
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id=profile_id,
        display_name=display_name,
        profile_version=1,
        institution="Test Bank",
        document_type="credit_card_statement",
        parser="capital_one_mastercard",
        input_folder=Path(
            f"editable_input/{profile_id}"
        ),
        output_folder=Path(
            f"csv_output/{profile_id}"
        ),
        file_pattern="*.pdf",
        recursive=True,
        preserve_subfolders=True,
        required_headers=(
            "Transaction date",
            "Posting date",
            "Description",
            "Amount",
        ),
        excluded_page_phrases=(),
        line_tolerance=2.5,
        continuation_gap=18.0,
        source_path=Path(
            f"config/profiles/{profile_id}.json"
        ),
    )


def make_workflow_result(
    *,
    failed: bool = False,
) -> WorkflowResult:
    file_result = WorkflowFileResult(
        profile_id="test_profile_v1",
        profile_name="Test Profile",
        pdf_file=Path("statement.pdf"),
        status=(
            "Failed"
            if failed
            else "Success"
        ),
        transaction_count=(
            0
            if failed
            else 3
        ),
        source_pages=(
            ()
            if failed
            else (1,)
        ),
        raw_csv=None,
        normalized_csv=None,
        error=(
            "Unreadable statement."
            if failed
            else None
        ),
    )

    return WorkflowResult(
        files=(file_result,),
        yearly_outputs=(),
        summary_csv=Path(
            "csv_output/workflow_summary.csv"
        ),
    )


class CLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.first = make_profile(
            "first_profile_v1",
            "First Profile",
        )
        self.second = make_profile(
            "second_profile_v1",
            "Second Profile",
        )
        self.profiles = (
            self.first,
            self.second,
        )

    def test_list_profiles_does_not_run_workflow(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch(
                "src.bill_extractor.cli.discover_profiles",
                return_value=self.profiles,
            ),
            patch(
                "src.bill_extractor.cli.run_extraction_workflow"
            ) as run_workflow,
        ):
            exit_code = main(
                ["--list-profiles"],
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "first_profile_v1 | First Profile",
            stdout.getvalue(),
        )
        self.assertIn(
            "second_profile_v1 | Second Profile",
            stdout.getvalue(),
        )
        self.assertEqual(
            stderr.getvalue(),
            "",
        )
        run_workflow.assert_not_called()

    def test_selected_profile_is_processed(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        workflow_result = (
            make_workflow_result()
        )

        with (
            patch(
                "src.bill_extractor.cli.discover_profiles",
                return_value=self.profiles,
            ),
            patch(
                "src.bill_extractor.cli.run_extraction_workflow",
                return_value=workflow_result,
            ) as run_workflow,
        ):
            exit_code = main(
                [
                    "--profile",
                    "second_profile_v1",
                ],
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(exit_code, 0)
        run_workflow.assert_called_once()

        selected = (
            run_workflow.call_args.args[0]
        )

        self.assertEqual(
            selected,
            (self.second,),
        )
        self.assertIn(
            "Successful:   1",
            stdout.getvalue(),
        )
        self.assertEqual(
            stderr.getvalue(),
            "",
        )

    def test_all_profiles_are_processed(
        self,
    ) -> None:
        stdout = StringIO()

        with (
            patch(
                "src.bill_extractor.cli.discover_profiles",
                return_value=self.profiles,
            ),
            patch(
                "src.bill_extractor.cli.run_extraction_workflow",
                return_value=make_workflow_result(),
            ) as run_workflow,
        ):
            exit_code = main(
                ["--all"],
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(exit_code, 0)

        selected = (
            run_workflow.call_args.args[0]
        )

        self.assertEqual(
            selected,
            self.profiles,
        )

    def test_unknown_profile_is_rejected(
        self,
    ) -> None:
        stderr = StringIO()

        with (
            patch(
                "src.bill_extractor.cli.discover_profiles",
                return_value=self.profiles,
            ),
            patch(
                "src.bill_extractor.cli.run_extraction_workflow"
            ) as run_workflow,
        ):
            exit_code = main(
                [
                    "--profile",
                    "missing_profile_v1",
                ],
                stdout=StringIO(),
                stderr=stderr,
            )

        self.assertEqual(exit_code, 2)
        self.assertIn(
            "Unknown profile ID",
            stderr.getvalue(),
        )
        run_workflow.assert_not_called()

    def test_workflow_failures_return_nonzero(
        self,
    ) -> None:
        stdout = StringIO()

        with (
            patch(
                "src.bill_extractor.cli.discover_profiles",
                return_value=self.profiles,
            ),
            patch(
                "src.bill_extractor.cli.run_extraction_workflow",
                return_value=make_workflow_result(
                    failed=True
                ),
            ),
        ):
            exit_code = main(
                [
                    "--profile",
                    "first_profile_v1",
                ],
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(exit_code, 1)
        self.assertIn(
            "Failed:       1",
            stdout.getvalue(),
        )
        self.assertIn(
            "Unreadable statement.",
            stdout.getvalue(),
        )

    def test_no_selection_is_rejected(
        self,
    ) -> None:
        stderr = StringIO()

        with patch(
            "src.bill_extractor.cli.discover_profiles",
            return_value=self.profiles,
        ):
            exit_code = main(
                [],
                stdout=StringIO(),
                stderr=stderr,
            )

        self.assertEqual(exit_code, 2)
        self.assertIn(
            "Choose --list-profiles",
            stderr.getvalue(),
        )

    def test_duplicate_requested_profiles_are_removed(
        self,
    ) -> None:
        selected = select_profiles(
            self.profiles,
            process_all=False,
            requested_ids=[
                "first_profile_v1",
                "first_profile_v1",
                "second_profile_v1",
            ],
        )

        self.assertEqual(
            selected,
            self.profiles,
        )


if __name__ == "__main__":
    unittest.main()
