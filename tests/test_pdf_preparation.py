from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from src.bill_extractor.pdf_redaction import PDFRedactionError
from src.bill_extractor.pdf_preparation import (
    PDFPreparationError,
    account_key_for_profile,
    preparation_folders_for_account,
    preparation_folders_for_profile,
    prepare_account_pdfs,
    prepare_profile_pdfs,
)
from src.bill_extractor.profile_loader import ExtractionProfile


RAW_COLUMNS = (
    "Date",
    "Description",
    "Withdrawals ($)",
    "Deposits ($)",
    "Balance ($)",
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_profile(
    root: Path,
    account_key: str = "test_account",
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id="test_account_v1",
        display_name="Test Account",
        profile_version=1,
        institution="Test Bank",
        document_type="bank_account_statement",
        parser="rbc_chequing_account",
        input_folder=Path("editable_input") / account_key,
        output_folder=Path("csv_output") / account_key,
        file_pattern="*.pdf",
        recursive=True,
        preserve_subfolders=True,
        required_headers=RAW_COLUMNS,
        excluded_page_phrases=(),
        line_tolerance=2.5,
        continuation_gap=18.0,
        source_path=root / "config" / "profiles" / "test.json",
    )


def make_text_pdf(
    path: Path,
    text: str,
    *,
    password: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), text)

        if password is None:
            document.save(path)
        else:
            document.save(
                path,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw=password,
                user_pw=password,
                permissions=-1,
            )


def make_image_only_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        document.new_page()
        document.save(path)


class PDFPreparationTest(unittest.TestCase):
    def test_account_key_mapping_supports_new_account_without_profile(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            folders = preparation_folders_for_account(
                "tangerine_chequing",
                project_root=root,
            )

            self.assertEqual(
                folders.account_key,
                Path("tangerine_chequing"),
            )
            self.assertEqual(
                folders.source_folder,
                (root / "source_input" / "tangerine_chequing").resolve(),
            )
            self.assertEqual(
                folders.editable_folder,
                (root / "editable_input" / "tangerine_chequing").resolve(),
            )
            self.assertEqual(
                folders.redacted_folder,
                (root / "redacted_input" / "tangerine_chequing").resolve(),
            )

    def test_profile_folder_mapping_supports_nested_account_key(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            profile = make_profile(
                root,
                "group/account",
            )

            folders = preparation_folders_for_profile(
                profile,
                project_root=root,
            )
            account_key = account_key_for_profile(
                profile,
                project_root=root,
            )

            self.assertEqual(
                account_key,
                Path("group") / "account",
            )
            self.assertEqual(
                folders.account_key,
                Path("group") / "account",
            )
            self.assertEqual(
                folders.source_folder,
                (root / "source_input" / "group" / "account").resolve(),
            )
            self.assertEqual(
                folders.editable_folder,
                (root / "editable_input" / "group" / "account").resolve(),
            )
            self.assertEqual(
                folders.redacted_folder,
                (root / "redacted_input" / "group" / "account").resolve(),
            )

    def test_mapping_rejects_input_outside_editable_input(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            profile = make_profile(root)
            profile = ExtractionProfile(
                **{
                    **profile.__dict__,
                    "input_folder": Path("raw_input/test_account"),
                }
            )

            with self.assertRaises(PDFPreparationError):
                preparation_folders_for_profile(
                    profile,
                    project_root=root,
                )

    def test_invalid_account_keys_are_rejected(
        self,
    ) -> None:
        cases = [
            "",
            Path.cwd(),
            "../escape",
            "account/../escape",
            ".",
        ]

        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            for account_key in cases:
                with self.subTest(account_key=account_key):
                    with self.assertRaises(PDFPreparationError):
                        preparation_folders_for_account(
                            account_key,
                            project_root=root,
                        )

    def test_missing_source_account_folder_fails_clearly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            with self.assertRaisesRegex(
                PDFPreparationError,
                "Source folder does not exist",
            ):
                prepare_account_pdfs(
                    "tangerine_chequing",
                    ["Jane Secret"],
                    project_root=root,
                )

    def test_successful_account_pipeline_preserves_folders_and_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source = (
                root
                / "source_input"
                / "tangerine_chequing"
                / "2025"
                / "statement.pdf"
            )

            make_text_pdf(
                source,
                "Customer: Jane Secret\nMerchant: Coffee Shop",
            )
            source_hash = file_hash(source)

            result = prepare_account_pdfs(
                "tangerine_chequing",
                ["Jane Secret"],
                project_root=root,
            )

            editable = (
                root
                / "editable_input"
                / "tangerine_chequing"
                / "2025"
                / "statement.pdf"
            )
            redacted = (
                root
                / "redacted_input"
                / "tangerine_chequing"
                / "2025"
                / "statement.pdf"
            )

            self.assertEqual(
                result.account_key,
                "tangerine_chequing",
            )
            self.assertIsNone(result.profile_id)
            self.assertIsNone(result.profile_display_name)
            self.assertEqual(result.source_pdf_count, 1)
            self.assertEqual(result.security_created_count, 1)
            self.assertEqual(result.redaction_created_count, 1)
            self.assertEqual(file_hash(source), source_hash)
            self.assertTrue(editable.is_file())
            self.assertTrue(redacted.is_file())

            with fitz.open(editable) as document:
                self.assertIn(
                    "Jane Secret",
                    document[0].get_text("text"),
                )

            with fitz.open(redacted) as document:
                text = document[0].get_text("text")
                self.assertNotIn("Jane Secret", text)
                self.assertIn("Coffee Shop", text)

    def test_security_failure_prevents_redaction_for_that_pdf(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source = (
                root
                / "source_input"
                / "test_account"
                / "bad.pdf"
            )

            make_image_only_pdf(source)

            result = prepare_account_pdfs(
                "test_account",
                ["Jane Secret"],
                project_root=root,
            )

            self.assertEqual(result.source_pdf_count, 1)
            self.assertEqual(result.security_failed_count, 1)
            self.assertEqual(result.redaction_created_count, 0)
            self.assertFalse(
                (
                    root
                    / "redacted_input"
                    / "test_account"
                    / "bad.pdf"
                ).exists()
            )

    def test_existing_redacted_output_is_rebuilt_with_current_terms(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source = (
                root
                / "source_input"
                / "test_account"
                / "statement.pdf"
            )

            make_text_pdf(
                source,
                (
                    "Customer: Jane Secret\n"
                    "Account: Token Delta\n"
                    "Merchant: Coffee Shop"
                ),
            )

            first = prepare_account_pdfs(
                "test_account",
                ["Jane Secret"],
                project_root=root,
            )

            editable = (
                root
                / "editable_input"
                / "test_account"
                / "statement.pdf"
            )
            redacted = (
                root
                / "redacted_input"
                / "test_account"
                / "statement.pdf"
            )
            editable_hash = file_hash(editable)

            self.assertEqual(first.security_created_count, 1)
            self.assertEqual(first.redaction_created_count, 1)
            self.assertTrue(redacted.is_file())

            with fitz.open(redacted) as document:
                first_text = document[0].get_text("text")
                self.assertNotIn("Jane Secret", first_text)
                self.assertIn("Token Delta", first_text)

            second = prepare_account_pdfs(
                "test_account",
                ["Token Delta"],
                project_root=root,
            )

            self.assertEqual(second.security_created_count, 0)
            self.assertEqual(second.security_skipped_count, 1)
            self.assertEqual(second.redaction_created_count, 1)
            self.assertEqual(second.redaction_skipped_count, 0)
            self.assertEqual(file_hash(editable), editable_hash)
            self.assertNotIn("Jane Secret", repr(second))
            self.assertNotIn("Token Delta", repr(second))

            with fitz.open(redacted) as document:
                second_text = document[0].get_text("text")
                self.assertIn("Jane Secret", second_text)
                self.assertNotIn("Token Delta", second_text)
                self.assertIn("Coffee Shop", second_text)

    def test_failed_redacted_rebuild_preserves_previous_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source = (
                root
                / "source_input"
                / "test_account"
                / "statement.pdf"
            )

            make_text_pdf(
                source,
                "Customer: Jane Secret\nAccount: Token Delta",
            )

            first = prepare_account_pdfs(
                "test_account",
                ["Jane Secret"],
                project_root=root,
            )

            editable = (
                root
                / "editable_input"
                / "test_account"
                / "statement.pdf"
            )
            redacted = (
                root
                / "redacted_input"
                / "test_account"
                / "statement.pdf"
            )
            editable_hash = file_hash(editable)
            prior_redacted_hash = file_hash(redacted)

            self.assertEqual(first.redaction_created_count, 1)

            with patch(
                "src.bill_extractor.pdf_redaction.verify_redacted_output",
                side_effect=PDFRedactionError(
                    "Synthetic verification failure."
                ),
            ):
                second = prepare_account_pdfs(
                    "test_account",
                    ["Token Delta"],
                    project_root=root,
                )

            self.assertEqual(second.security_created_count, 0)
            self.assertEqual(second.security_skipped_count, 1)
            self.assertEqual(second.redaction_created_count, 0)
            self.assertEqual(second.redaction_failed_count, 1)
            self.assertEqual(file_hash(editable), editable_hash)
            self.assertEqual(file_hash(redacted), prior_redacted_hash)
            self.assertNotIn("Jane Secret", repr(second))
            self.assertNotIn("Token Delta", repr(second))
            self.assertNotIn(
                "Synthetic verification failure",
                repr(second),
            )

            with fitz.open(redacted) as document:
                text = document[0].get_text("text")
                self.assertNotIn("Jane Secret", text)
                self.assertIn("Token Delta", text)

    def test_password_required_is_aggregate_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source = (
                root
                / "source_input"
                / "test_account"
                / "statement.pdf"
            )

            make_text_pdf(
                source,
                "Customer: Jane Secret",
                password="secret-password",
            )

            result = prepare_account_pdfs(
                "test_account",
                ["Jane Secret"],
                project_root=root,
            )

            self.assertEqual(result.source_pdf_count, 1)
            self.assertEqual(
                result.security_password_required_count,
                1,
            )
            self.assertEqual(result.redaction_created_count, 0)
            self.assertNotIn("secret-password", repr(result))
            self.assertNotIn("Jane Secret", repr(result))

    def test_only_selected_account_folder_is_processed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            selected = (
                root
                / "source_input"
                / "test_account"
                / "statement.pdf"
            )
            other = (
                root
                / "source_input"
                / "other_account"
                / "statement.pdf"
            )

            make_text_pdf(selected, "Customer: Jane Secret")
            make_text_pdf(other, "Customer: Jane Secret")

            result = prepare_account_pdfs(
                "test_account",
                ["Jane Secret"],
                project_root=root,
            )

            self.assertEqual(result.source_pdf_count, 1)
            self.assertTrue(
                (
                    root
                    / "redacted_input"
                    / "test_account"
                    / "statement.pdf"
                ).is_file()
            )
            self.assertFalse(
                (
                    root
                    / "redacted_input"
                    / "other_account"
                    / "statement.pdf"
                ).exists()
            )

    def test_profile_wrapper_retains_profile_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            profile = make_profile(root)
            source = (
                root
                / "source_input"
                / "test_account"
                / "statement.pdf"
            )

            make_text_pdf(source, "Customer: Jane Secret")

            result = prepare_profile_pdfs(
                profile,
                ["Jane Secret"],
                project_root=root,
            )

            self.assertEqual(result.account_key, "test_account")
            self.assertEqual(result.profile_id, "test_account_v1")
            self.assertEqual(result.profile_display_name, "Test Account")
            self.assertEqual(result.redaction_created_count, 1)

    def test_destination_folders_are_created_as_needed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source = (
                root
                / "source_input"
                / "test_account"
                / "statement.pdf"
            )

            make_text_pdf(source, "Customer: Jane Secret")

            self.assertFalse(
                (root / "editable_input").exists()
            )
            self.assertFalse(
                (root / "redacted_input").exists()
            )

            prepare_account_pdfs(
                "test_account",
                ["Jane Secret"],
                project_root=root,
            )

            self.assertTrue(
                (
                    root
                    / "editable_input"
                    / "test_account"
                ).is_dir()
            )
            self.assertTrue(
                (
                    root
                    / "redacted_input"
                    / "test_account"
                ).is_dir()
            )

    def test_empty_terms_are_rejected_and_extraction_not_imported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            profile = make_profile(root)

            with self.assertRaises(PDFPreparationError):
                prepare_account_pdfs(
                    "test_account",
                    [" ", ""],
                    project_root=root,
                )

        import src.bill_extractor.pdf_preparation as module

        self.assertFalse(
            hasattr(module, "run_selected_extraction_workflow")
        )


if __name__ == "__main__":
    unittest.main()
