import importlib.util
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from src.bill_extractor.institution_collector import (
    InstitutionCollectionResult,
)
from src.bill_extractor.pdf_redaction import (
    REPLACE_REDACTED_PDF_ERROR,
)
from src.bill_extractor.pdf_preparation import (
    PDFPreparationResult,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
)
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


def make_profile(
    root: Path,
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id="test_v1",
        display_name="Test Profile",
        profile_version=1,
        institution="Test Bank",
        document_type="bank_account_statement",
        parser="rbc_chequing_account",
        input_folder=root / "editable",
        output_folder=root / "csv",
        file_pattern="*.pdf",
        recursive=True,
        preserve_subfolders=True,
        required_headers=("Date",),
        excluded_page_phrases=(),
        line_tolerance=2.5,
        continuation_gap=18.0,
        source_path=Path("config/profiles/test.json"),
    )


def create_test_app(
    test_case: unittest.TestCase,
):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        test_case.skipTest(f"Tk display is not available: {exc}")

    root.withdraw()
    test_case.addCleanup(root.destroy)

    temporary_folder = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary_folder.cleanup)
    profile = make_profile(Path(temporary_folder.name))

    patches = [
        patch.object(
            GUI_MODULE,
            "discover_profiles",
            return_value=(profile,),
        ),
        patch.object(
            GUI_MODULE,
            "preparation_account_suggestions",
            return_value=("test_account",),
        ),
        patch.object(
            GUI_MODULE,
            "load_redaction_account_settings",
            side_effect=RuntimeError("synthetic settings unavailable"),
        ),
    ]

    for active_patch in patches:
        active_patch.start()
        test_case.addCleanup(active_patch.stop)

    app = GUI_MODULE.PDFBillExtractorApp(root)
    root.update_idletasks()
    return root, app


def is_descendant(
    widget: tk.Widget,
    ancestor: tk.Widget,
) -> bool:
    current = widget

    while current is not None:
        if current is ancestor:
            return True

        current = current.master

    return False


class GUIWorkflowTest(unittest.TestCase):
    def test_initial_window_geometry_is_capped_for_small_screens(
        self,
    ) -> None:
        self.assertEqual(
            GUI_MODULE.initial_window_geometry(1366, 768),
            "980x668",
        )
        self.assertEqual(
            GUI_MODULE.initial_window_geometry(1280, 720),
            "980x620",
        )

    def test_layout_uses_separate_control_and_result_panes(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        panes = tuple(app.main_paned.panes())

        self.assertEqual(len(panes), 2)
        self.assertEqual(
            panes[0],
            str(app.controls_scroll_container),
        )
        self.assertEqual(
            panes[1],
            str(app.results_frame),
        )
        self.assertIs(
            app.results_frame.master,
            app.main_paned,
        )
        self.assertTrue(
            is_descendant(
                app.results_text,
                app.results_frame,
            ),
        )
        self.assertFalse(
            is_descendant(
                app.results_text,
                app.controls_scroll_container,
            ),
        )
        self.assertFalse(
            is_descendant(
                app.results_text,
                app.controls_frame,
            ),
        )
        self.assertTrue(
            is_descendant(
                app.results_text.vbar,
                app.results_frame,
            ),
        )
        self.assertIsNot(
            app.results_frame,
            app.controls_scroll_container,
        )

    def test_controls_scroll_independently_from_results(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        self.assertIs(
            app.controls_canvas.master,
            app.controls_scroll_container,
        )
        self.assertIs(
            app.controls_scrollbar.master,
            app.controls_scroll_container,
        )
        self.assertIs(
            app.controls_frame.master,
            app.controls_canvas,
        )
        self.assertEqual(
            int(app.results_text.cget("height")),
            GUI_MODULE.RESULTS_TEXT_MIN_LINES,
        )
        self.assertIsNot(
            app.results_text.master,
            app.controls_scroll_container,
        )

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

    def test_collection_result_is_reported_privately(
        self,
    ) -> None:
        result = InstitutionCollectionResult(
            institution="Test Bank",
            institution_slug="test_bank",
            collection_root=Path(
                "csv_output/institution_collections/test_bank"
            ),
            all_statements_folder=Path(
                "csv_output/institution_collections/"
                "test_bank/all_statements"
            ),
            combined_csv_path=Path(
                "csv_output/institution_collections/"
                "test_bank/test_bank_all_transactions.csv"
            ),
            manifest_path=Path(
                "csv_output/institution_collections/"
                "test_bank/.collection_manifest.json"
            ),
            profile_ids=("test_profile_v1",),
            collected_count=3,
            combined_transaction_count=12,
            stale_removed_count=1,
        )

        text = "\n".join(
            GUI_MODULE
            .institution_collection_result_messages(result)
        )

        self.assertIn(
            "INSTITUTION COLLECTION RESULTS",
            text,
        )
        self.assertIn(
            "Institution: Test Bank",
            text,
        )
        self.assertIn(
            "Collection folder:",
            text,
        )
        self.assertIn(
            "configured output folders",
            text,
        )
        self.assertIn(
            "Normalized statement CSVs collected: 3",
            text,
        )
        self.assertIn(
            "Combined transactions: 12",
            text,
        )
        self.assertIn(
            "Combined CSV:",
            text,
        )
        self.assertIn(
            "Stale managed files removed: 1",
            text,
        )

    def test_preparation_result_is_aggregate_and_private(
        self,
    ) -> None:
        result = PDFPreparationResult(
            account_key="tangerine_chequing",
            profile_id=None,
            profile_display_name=None,
            source_folder=Path(
                "source_input/tangerine_chequing"
            ),
            editable_folder=Path(
                "editable_input/tangerine_chequing"
            ),
            redacted_folder=Path(
                "redacted_input/tangerine_chequing"
            ),
            source_pdf_count=3,
            security_created_count=2,
            security_skipped_count=1,
            security_password_required_count=0,
            security_failed_count=0,
            redaction_created_count=2,
            redaction_already_clean_count=1,
            redaction_skipped_count=0,
            redaction_failed_count=0,
        )

        text = "\n".join(
            GUI_MODULE
            .pdf_preparation_result_messages(result)
        )

        self.assertIn(
            "PDF PREPARATION RESULTS",
            text,
        )
        self.assertIn(
            "Preparation account: tangerine_chequing",
            text,
        )
        self.assertIn(
            "Source PDFs found: 3",
            text,
        )
        self.assertIn(
            "Security removal:",
            text,
        )
        self.assertIn(
            "Password required: 0",
            text,
        )
        self.assertIn(
            "Redaction:",
            text,
        )
        self.assertIn(
            "Already clean: 1",
            text,
        )
        self.assertIn(
            "Redacted folder:",
            text,
        )
        self.assertNotIn(
            "Jane Example",
            text,
        )
        self.assertNotIn(
            "secret-password",
            text,
        )

    def test_preparation_failure_reason_is_safe_and_actionable(
        self,
    ) -> None:
        result = PDFPreparationResult(
            account_key="tangerine_chequing",
            profile_id=None,
            profile_display_name=None,
            source_folder=Path(
                "source_input/tangerine_chequing"
            ),
            editable_folder=Path(
                "editable_input/tangerine_chequing"
            ),
            redacted_folder=Path(
                "redacted_input/tangerine_chequing"
            ),
            source_pdf_count=1,
            security_created_count=0,
            security_skipped_count=1,
            security_password_required_count=0,
            security_failed_count=0,
            redaction_created_count=0,
            redaction_already_clean_count=0,
            redaction_skipped_count=0,
            redaction_failed_count=1,
            redaction_failure_messages=(
                REPLACE_REDACTED_PDF_ERROR,
            ),
        )

        text = "\n".join(
            GUI_MODULE
            .pdf_preparation_result_messages(result)
        )

        self.assertIn(
            "Redaction failure details:",
            text,
        )
        self.assertIn(
            "Close the redacted PDF",
            text,
        )
        self.assertNotIn(
            "Jane Example",
            text,
        )
        self.assertNotIn(
            "secret-password",
            text,
        )

    def test_preparation_account_suggestions_come_from_profiles(
        self,
    ) -> None:
        profiles = (
            ExtractionProfile(
                profile_id="test_v1",
                display_name="Test Profile",
                profile_version=1,
                institution="Test Bank",
                document_type="bank_account_statement",
                parser="rbc_chequing_account",
                input_folder=Path(
                    "editable_input/test_account"
                ),
                output_folder=Path(
                    "csv_output/test_account"
                ),
                file_pattern="*.pdf",
                recursive=True,
                preserve_subfolders=True,
                required_headers=("Date",),
                excluded_page_phrases=(),
                line_tolerance=2.5,
                continuation_gap=18.0,
                source_path=Path(
                    "config/profiles/test.json"
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_folder:
            suggestions = (
                GUI_MODULE
                .preparation_account_suggestions(
                    profiles,
                    settings_path=(
                        Path(temporary_folder)
                        / "missing-preparation.local.json"
                    ),
                )
            )

        self.assertEqual(
            suggestions,
            ["test_account"],
        )
        self.assertNotIn(
            "tangerine_chequing",
            suggestions,
        )

    def test_preparation_account_suggestions_include_saved_keys(
        self,
    ) -> None:
        profiles = (
            ExtractionProfile(
                profile_id="test_v1",
                display_name="Test Profile",
                profile_version=1,
                institution="Test Bank",
                document_type="bank_account_statement",
                parser="rbc_chequing_account",
                input_folder=Path(
                    "editable_input/test_account"
                ),
                output_folder=Path(
                    "csv_output/test_account"
                ),
                file_pattern="*.pdf",
                recursive=True,
                preserve_subfolders=True,
                required_headers=("Date",),
                excluded_page_phrases=(),
                line_tolerance=2.5,
                continuation_gap=18.0,
                source_path=Path(
                    "config/profiles/test.json"
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_folder:
            settings_path = (
                Path(temporary_folder)
                / "preparation.local.json"
            )
            settings_path.write_text(
                (
                    "{\n"
                    '  "preparation_accounts": [\n'
                    '    "tangerine_chequing",\n'
                    '    "test_account"\n'
                    "  ]\n"
                    "}\n"
                ),
                encoding="utf-8",
            )

            suggestions = (
                GUI_MODULE
                .preparation_account_suggestions(
                    profiles,
                    settings_path=settings_path,
                )
            )

        self.assertEqual(
            suggestions,
            [
                "tangerine_chequing",
                "test_account",
            ],
        )


if __name__ == "__main__":
    unittest.main()
