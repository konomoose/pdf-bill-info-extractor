from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import fitz

from src.bill_extractor.pdf_security import (
    PDFPasswordRequiredError,
    PDFSecurityError,
    remove_pdf_security,
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


class PDFSecurityModuleTest(unittest.TestCase):
    def test_recursive_relative_structure_source_preserved_and_unencrypted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source_root = root / "source_input"
            output_root = root / "editable_input"
            source = source_root / "account" / "2025" / "jan.pdf"

            make_text_pdf(source, "Synthetic statement text")
            source_hash = file_hash(source)

            result = remove_pdf_security(
                source,
                source_root,
                output_root,
            )

            destination = output_root / "account" / "2025" / "jan.pdf"

            self.assertEqual(result.status, "created")
            self.assertEqual(result.destination, destination.resolve())
            self.assertEqual(file_hash(source), source_hash)
            self.assertTrue(destination.is_file())

            with fitz.open(destination) as document:
                self.assertEqual(document.page_count, 1)
                self.assertFalse(document.needs_pass)
                self.assertFalse(document.is_encrypted)
                self.assertEqual(
                    document[0].get_text("text").strip(),
                    "Synthetic statement text",
                )

    def test_encrypted_pdf_becomes_unencrypted_with_supplied_password(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source_root = root / "source_input"
            output_root = root / "editable_input"
            source = source_root / "account" / "statement.pdf"

            make_text_pdf(
                source,
                "Password protected text",
                password="secret-pass",
            )

            result = remove_pdf_security(
                source,
                source_root,
                output_root,
                password="secret-pass",
            )

            self.assertEqual(result.status, "created")
            self.assertNotIn("secret-pass", repr(result))

            with fitz.open(result.destination) as document:
                self.assertFalse(document.needs_pass)
                self.assertFalse(document.is_encrypted)
                self.assertEqual(
                    document[0].get_text("text").strip(),
                    "Password protected text",
                )

    def test_existing_verified_output_is_skipped(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source_root = root / "source_input"
            output_root = root / "editable_input"
            source = source_root / "account" / "statement.pdf"

            make_text_pdf(source, "Statement text")

            first = remove_pdf_security(
                source,
                source_root,
                output_root,
            )
            first_hash = file_hash(first.destination)

            second = remove_pdf_security(
                source,
                source_root,
                output_root,
            )

            self.assertEqual(second.status, "skipped")
            self.assertEqual(
                file_hash(second.destination),
                first_hash,
            )

    def test_unverifiable_existing_output_fails_safely(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source_root = root / "source_input"
            output_root = root / "editable_input"
            source = source_root / "account" / "statement.pdf"
            destination = output_root / "account" / "statement.pdf"

            make_text_pdf(source, "Source text")
            make_text_pdf(destination, "Different text")
            destination_hash = file_hash(destination)

            with self.assertRaises(PDFSecurityError):
                remove_pdf_security(
                    source,
                    source_root,
                    output_root,
                )

            self.assertEqual(
                file_hash(destination),
                destination_hash,
            )

    def test_failure_cleans_temporary_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source_root = root / "source_input"
            output_root = root / "editable_input"
            source = source_root / "account" / "statement.pdf"

            make_image_only_pdf(source)

            with self.assertRaises(PDFSecurityError):
                remove_pdf_security(
                    source,
                    source_root,
                    output_root,
                )

            self.assertFalse(
                (
                    output_root
                    / "account"
                    / ".statement.pdf.unsecured.tmp.pdf"
                ).exists()
            )

    def test_password_not_exposed_in_error_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            source_root = root / "source_input"
            output_root = root / "editable_input"
            source = source_root / "account" / "statement.pdf"

            make_text_pdf(
                source,
                "Password protected text",
                password="real-secret",
            )

            with self.assertRaises(PDFPasswordRequiredError) as context:
                remove_pdf_security(
                    source,
                    source_root,
                    output_root,
                    password="wrong-secret",
                )

            self.assertNotIn(
                "wrong-secret",
                str(context.exception),
            )
            self.assertNotIn(
                "real-secret",
                str(context.exception),
            )


if __name__ == "__main__":
    unittest.main()
