import importlib.util
from pathlib import Path
import sys
import unittest

from src.bill_extractor.workflow import (
    WorkflowFileResult,
    WorkflowResult,
    YearlyOutput,
)


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

GUI_PATH = (
    PROJECT_ROOT
    / "visa_pdf_extractor-v3.py"
)

SPEC = importlib.util.spec_from_file_location(
    "visa_pdf_extractor_v3",
    GUI_PATH,
)

if SPEC is None or SPEC.loader is None:
    raise RuntimeError(
        "Could not load GUI module."
    )

GUI_MODULE = importlib.util.module_from_spec(
    SPEC
)
sys.modules[SPEC.name] = GUI_MODULE
SPEC.loader.exec_module(GUI_MODULE)


class GUIWorkflowTest(unittest.TestCase):
    def test_workflow_messages_include_all_outputs(
        self,
    ) -> None:
        root = Path("csv_output/test_bank")

        result = WorkflowResult(
            files=(
                WorkflowFileResult(
                    profile_id="test_v1",
                    profile_name="Test Profile",
                    pdf_file=Path(
                        "input/statement.pdf"
                    ),
                    status="Success",
                    transaction_count=4,
                    source_pages=(1, 2),
                    raw_csv=(
                        root
                        / "statement_transactions.csv"
                    ),
                    normalized_csv=(
                        root
                        / (
                            "statement_"
                            "normalized_transactions.csv"
                        )
                    ),
                    error=None,
                ),
                WorkflowFileResult(
                    profile_id="test_v1",
                    profile_name="Test Profile",
                    pdf_file=Path(
                        "input/annual.pdf"
                    ),
                    status="Skipped",
                    transaction_count=0,
                    source_pages=(),
                    raw_csv=None,
                    normalized_csv=None,
                    error=(
                        "Annual summary; "
                        "no transaction table."
                    ),
                ),
            ),
            yearly_outputs=(
                YearlyOutput(
                    output_root=root,
                    institution_slug=(
                        "test_bank"
                    ),
                    year=2025,
                    csv_path=(
                        root
                        / "2025"
                        / (
                            "test_bank_2025_"
                            "transactions.csv"
                        )
                    ),
                    transaction_count=4,
                ),
            ),
            summary_csv=(
                root
                / "workflow_summary.csv"
            ),
        )

        messages = (
            GUI_MODULE
            .workflow_result_messages(result)
        )
        text = "\n".join(messages)

        self.assertIn(
            "SUCCESS: statement.pdf",
            text,
        )
        self.assertIn(
            "Raw CSV:",
            text,
        )
        self.assertIn(
            "Normalized CSV:",
            text,
        )
        self.assertIn(
            "SKIPPED: annual.pdf",
            text,
        )
        self.assertIn(
            "2025: 4 transactions",
            text,
        )
        self.assertIn(
            "Successful: 1",
            text,
        )
        self.assertIn(
            "Skipped: 1",
            text,
        )
        self.assertIn(
            "Failed: 0",
            text,
        )
        self.assertIn(
            "Workflow summary CSV:",
            text,
        )

    def test_failed_result_is_reported(
        self,
    ) -> None:
        result = WorkflowResult(
            files=(
                WorkflowFileResult(
                    profile_id="test_v1",
                    profile_name="Test Profile",
                    pdf_file=Path(
                        "input/bad.pdf"
                    ),
                    status="Failed",
                    transaction_count=0,
                    source_pages=(),
                    raw_csv=None,
                    normalized_csv=None,
                    error="Unreadable statement.",
                ),
            ),
            yearly_outputs=(),
            summary_csv=Path(
                "csv_output/workflow_summary.csv"
            ),
        )

        text = "\n".join(
            GUI_MODULE
            .workflow_result_messages(result)
        )

        self.assertIn(
            "FAILED:  bad.pdf",
            text,
        )
        self.assertIn(
            "Unreadable statement.",
            text,
        )
        self.assertIn(
            "Failed: 1",
            text,
        )
        self.assertIn(
            "No yearly CSVs were created.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
