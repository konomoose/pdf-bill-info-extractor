import importlib.util
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from src.bill_extractor.institution_collector import (
    AccountCollectionResult,
)
from src.bill_extractor.pdf_redaction import (
    REPLACE_REDACTED_PDF_ERROR,
)
from src.bill_extractor.pdf_preparation import (
    PDFPreparationResult,
)
from src.bill_extractor.redaction_term_settings import (
    RedactionAccountSettings,
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
    *,
    account_key: str = "test_account",
    display_name: str = "Test Profile",
    institution: str = "Test Bank",
    profile_id: str = "test_v1",
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id=profile_id,
        display_name=display_name,
        profile_version=1,
        institution=institution,
        document_type="bank_account_statement",
        parser="rbc_chequing_account",
        input_folder=root / "editable_input" / account_key,
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


def make_account_profile(
    root: Path,
    *,
    account_key: str,
    display_name: str,
    institution: str,
    profile_id: str,
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id=profile_id,
        display_name=display_name,
        profile_version=1,
        institution=institution,
        document_type="bank_account_statement",
        parser="rbc_chequing_account",
        input_folder=root / "editable_input" / account_key,
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
    *,
    profiles: tuple[ExtractionProfile, ...] | None = None,
    redaction_settings: RedactionAccountSettings | None = None,
):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        test_case.skipTest(f"Tk display is not available: {exc}")

    root.withdraw()
    test_case.addCleanup(root.destroy)

    temporary_folder = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary_folder.cleanup)
    if profiles is None:
        profile = make_profile(Path(temporary_folder.name))
        profiles = (profile,)
    else:
        profile = profiles[0]

    settings = redaction_settings or RedactionAccountSettings()

    patches = [
        patch.object(
            GUI_MODULE,
            "discover_profiles",
            return_value=profiles,
        ),
        patch.object(
            GUI_MODULE,
            "preparation_account_suggestions",
            return_value=tuple(
                sorted(
                    filter(
                        None,
                        (
                            GUI_MODULE.profile_preparation_account_key(active)
                            for active in profiles
                        ),
                    ),
                    key=str.casefold,
                )
            ),
        ),
        patch.object(
            GUI_MODULE,
            "load_redaction_account_settings_for_institution",
            return_value=settings,
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


def widget_state(widget: tk.Widget) -> str:
    return str(widget.cget("state"))


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

    def test_extraction_input_and_output_are_separate_groups(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        self.assertIsNot(app.input_frame, app.output_frame)
        self.assertIs(app.input_frame.master, app.output_frame.master)
        self.assertIs(app.single_pdf_radio.master, app.input_frame)
        self.assertIs(app.pdf_file_entry.master, app.input_frame)
        self.assertIs(app.pdf_file_browse_btn.master, app.input_frame)
        self.assertIs(app.pdf_folder_radio.master, app.input_frame)
        self.assertIs(app.pdf_folder_entry.master, app.input_frame)
        self.assertIs(app.pdf_folder_browse_btn.master, app.input_frame)
        self.assertIs(app.output_folder_entry.master, app.output_frame)
        self.assertIs(app.output_folder_browse_btn.master, app.output_frame)

        self.assertEqual(
            app.single_pdf_radio.grid_info()["row"],
            app.pdf_file_entry.grid_info()["row"],
        )
        self.assertEqual(
            app.single_pdf_radio.grid_info()["row"],
            app.pdf_file_browse_btn.grid_info()["row"],
        )
        self.assertEqual(
            app.pdf_folder_radio.grid_info()["row"],
            app.pdf_folder_entry.grid_info()["row"],
        )
        self.assertEqual(
            app.pdf_folder_radio.grid_info()["row"],
            app.pdf_folder_browse_btn.grid_info()["row"],
        )

    def test_input_mode_enables_only_selected_input_row(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        app.pdf_file_var.set("C:/synthetic/file.pdf")
        app.pdf_folder_var.set("C:/synthetic/folder")

        app.input_mode_var.set("file")
        app._update_input_mode_controls()

        self.assertEqual(widget_state(app.pdf_file_entry), "normal")
        self.assertEqual(widget_state(app.pdf_file_browse_btn), "normal")
        self.assertEqual(widget_state(app.pdf_folder_entry), "disabled")
        self.assertEqual(widget_state(app.pdf_folder_browse_btn), "disabled")
        self.assertEqual(widget_state(app.output_folder_entry), "normal")
        self.assertEqual(widget_state(app.output_folder_browse_btn), "normal")

        app.input_mode_var.set("folder")
        app._update_input_mode_controls()

        self.assertEqual(widget_state(app.pdf_file_entry), "disabled")
        self.assertEqual(widget_state(app.pdf_file_browse_btn), "disabled")
        self.assertEqual(widget_state(app.pdf_folder_entry), "normal")
        self.assertEqual(widget_state(app.pdf_folder_browse_btn), "normal")
        self.assertEqual(app.pdf_file_var.get(), "C:/synthetic/file.pdf")
        self.assertEqual(app.pdf_folder_var.get(), "C:/synthetic/folder")
        self.assertEqual(widget_state(app.output_folder_entry), "normal")

    def test_results_append_as_session_log_with_separators(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        app._append_operation_header(
            "PDF PREPARATION",
            ("Account: test_account",),
        )
        app._append_result("Preparation complete\n")
        app._append_operation_header(
            "TRANSACTION EXTRACTION",
            ("Profile: Test Profile",),
        )
        app._append_result("Extraction complete\n")

        text = app.results_text.get("1.0", "end-1c")

        self.assertIn("=== PDF PREPARATION - ", text)
        self.assertIn("Preparation complete", text)
        self.assertIn("=== TRANSACTION EXTRACTION - ", text)
        self.assertIn("Extraction complete", text)
        self.assertIn("-" * 60, text)

    def test_result_clear_conditions_are_context_changes_only(
        self,
    ) -> None:
        temporary_folder = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_folder.cleanup)
        root_path = Path(temporary_folder.name)
        profiles = (
            make_account_profile(
                root_path,
                account_key="tangerine_chequing",
                display_name="Tangerine Chequing Account",
                institution="Tangerine Bank",
                profile_id="tangerine_chequing_account_v1",
            ),
            make_account_profile(
                root_path,
                account_key="tangerine_savings",
                display_name="Tangerine Savings Account",
                institution="Tangerine Bank",
                profile_id="tangerine_savings_account_v1",
            ),
        )
        _root, app = create_test_app(self, profiles=profiles)

        app._append_result("session log\n")
        app.input_mode_var.set("folder")
        app.pdf_file_var.set("C:/synthetic/file.pdf")
        app.pdf_folder_var.set("C:/synthetic/folder")
        app.output_folder_var.set("C:/synthetic/output")
        self.assertIn(
            "session log",
            app.results_text.get("1.0", "end-1c"),
        )

        app.preparation_account_var.set("manual_account")
        app._preparation_account_changed()
        self.assertEqual(app.results_text.get("1.0", "end-1c"), "")

        app._append_result("new context log\n")
        app.profile_var.set("Tangerine Savings Account")
        app._profile_selected()
        self.assertEqual(app.results_text.get("1.0", "end-1c"), "")

    def test_clear_results_button_clears_immediately(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        app._append_result("synthetic result\n")
        app.clear_results_btn.invoke()

        self.assertEqual(app.results_text.get("1.0", "end-1c"), "")

    def test_profile_change_synchronizes_preparation_account(
        self,
    ) -> None:
        temporary_folder = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_folder.cleanup)
        root_path = Path(temporary_folder.name)
        profiles = (
            make_account_profile(
                root_path,
                account_key="tangerine_chequing",
                display_name="Tangerine Chequing Account",
                institution="Tangerine Bank",
                profile_id="tangerine_chequing_account_v1",
            ),
            make_account_profile(
                root_path,
                account_key="tangerine_savings",
                display_name="Tangerine Savings Account",
                institution="Tangerine Bank",
                profile_id="tangerine_savings_account_v1",
            ),
        )
        _root, app = create_test_app(self, profiles=profiles)

        self.assertEqual(
            app.preparation_account_var.get(),
            "tangerine_chequing",
        )

        app.preparation_account_var.set("manual_account")
        app._preparation_account_changed()
        self.assertEqual(app.preparation_account_var.get(), "manual_account")

        app.profile_var.set("Tangerine Savings Account")
        app._profile_selected()
        self.assertEqual(
            app.preparation_account_var.get(),
            "tangerine_savings",
        )

        app.preparation_account_var.set("another_manual_account")
        app._preparation_account_changed()
        app.profile_var.set("Tangerine Chequing Account")
        app._profile_selected()
        self.assertEqual(
            app.preparation_account_var.get(),
            "tangerine_chequing",
        )

    def test_institution_mapping_groups_tangerine_accounts(
        self,
    ) -> None:
        temporary_folder = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_folder.cleanup)
        root_path = Path(temporary_folder.name)
        profiles = (
            make_account_profile(
                root_path,
                account_key="tangerine_chequing",
                display_name="Tangerine Chequing Account",
                institution="Tangerine Bank",
                profile_id="tangerine_chequing_account_v1",
            ),
            make_account_profile(
                root_path,
                account_key="tangerine_savings",
                display_name="Tangerine Savings Account",
                institution="Tangerine Bank",
                profile_id="tangerine_savings_account_v1",
            ),
            make_account_profile(
                root_path,
                account_key="other_account",
                display_name="Other Account",
                institution="Other Bank",
                profile_id="other_account_v1",
            ),
        )

        self.assertEqual(
            GUI_MODULE.preparation_account_institution_map(profiles),
            {
                "other_account": "Other Bank",
                "tangerine_chequing": "Tangerine Bank",
                "tangerine_savings": "Tangerine Bank",
            },
        )
        self.assertEqual(
            GUI_MODULE.institution_preparation_accounts(profiles),
            {
                "Other Bank": ("other_account",),
                "Tangerine Bank": (
                    "tangerine_chequing",
                    "tangerine_savings",
                ),
            },
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
        result = AccountCollectionResult(
            profile_id="test_profile_v1",
            profile_display_name="Test Account",
            account_slug="test_account",
            output_folder=Path("csv_output/test_account"),
            combined_csv_path=Path(
                "csv_output/test_account/test_account_all_transactions.csv"
            ),
            manifest_path=Path(
                "csv_output/test_account/.account_collection_manifest.json"
            ),
            collected_count=3,
            combined_transaction_count=12,
        )

        text = "\n".join(
            GUI_MODULE
            .account_collection_result_messages(result)
        )

        self.assertIn(
            "ACCOUNT COLLECTION RESULTS",
            text,
        )
        self.assertIn(
            "Profile/account: Test Account",
            text,
        )
        self.assertIn(
            "Output folder:",
            text,
        )
        self.assertIn(
            "selected profile's configured output folder",
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
            "Duplicate statement copies skipped: 0",
            text,
        )
        self.assertIn(
            "Combined CSV:",
            text,
        )

    def test_collection_button_uses_account_wording(
        self,
    ) -> None:
        _root, app = create_test_app(self)

        self.assertEqual(
            app.collect_btn.cget("text"),
            "Collect Account CSVs",
        )
        self.assertIn(
            "Collect Account CSVs",
            app.folder_mode_info_var.get(),
        )
        self.assertIn(
            "selected profile's configured output folder",
            app.folder_mode_info_var.get(),
        )

    def test_collection_worker_uses_selected_profile_account(
        self,
    ) -> None:
        _root, app = create_test_app(self)
        result = AccountCollectionResult(
            profile_id=app.active_profile.profile_id,
            profile_display_name=app.active_profile.display_name,
            account_slug="test_account",
            output_folder=Path("csv_output/test_account"),
            combined_csv_path=Path(
                "csv_output/test_account/test_account_all_transactions.csv"
            ),
            manifest_path=Path(
                "csv_output/test_account/.account_collection_manifest.json"
            ),
            collected_count=1,
            combined_transaction_count=2,
        )

        with patch.object(
            GUI_MODULE,
            "collect_profile_account_statement_csvs",
            return_value=result,
        ) as collect_account:
            with patch.object(
                app.root,
                "after",
                side_effect=lambda _delay, callback, *args: callback(*args),
            ):
                with patch.object(app, "_collection_succeeded") as succeeded:
                    app._collection_worker(app.active_profile)

        collect_account.assert_called_once_with(app.active_profile)
        succeeded.assert_called_once_with(result)

    def test_collection_result_appends_to_processing_results(
        self,
    ) -> None:
        _root, app = create_test_app(self)
        app._append_result("Existing preparation output\n")
        result = AccountCollectionResult(
            profile_id="test_profile_v1",
            profile_display_name="Test Account",
            account_slug="test_account",
            output_folder=Path("csv_output/test_account"),
            combined_csv_path=Path(
                "csv_output/test_account/test_account_all_transactions.csv"
            ),
            manifest_path=Path(
                "csv_output/test_account/.account_collection_manifest.json"
            ),
            collected_count=1,
            combined_transaction_count=2,
        )

        with patch.object(GUI_MODULE.messagebox, "showinfo"):
            app._collection_succeeded(result)

        text = app.results_text.get("1.0", "end-1c")
        self.assertIn(
            "Existing preparation output",
            text,
        )
        self.assertIn(
            "ACCOUNT COLLECTION RESULTS",
            text,
        )
        self.assertIn(
            "Profile/account: Test Account",
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
