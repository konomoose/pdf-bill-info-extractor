from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from src.bill_extractor.pdf_redaction import (
    PDFRedactionError,
    normalize_redaction_terms,
    redact_pdf,
    verify_redacted_output,
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_private_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Customer: Jane Example")
        page.insert_text((72, 92), "Account: 1234-5678")
        page.insert_text((72, 112), "Merchant: Coffee Shop")
        document.set_metadata(
            {
                "author": "Jane Example",
                "title": "Private bank statement",
            }
        )
        document.embfile_add(
            "private-note.txt",
            b"Synthetic embedded content",
        )
        document.save(path)


class PDFRedactionModuleTest(unittest.TestCase):
    def test_exact_terms_removed_and_structure_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "2025" / "statement.pdf"

            make_private_pdf(source)
            source_hash = file_hash(source)

            result = redact_pdf(
                source,
                editable,
                redacted,
                ("Jane Example", "1234-5678"),
            )

            destination = redacted / "account" / "2025" / "statement.pdf"

            self.assertEqual(result.status, "created")
            self.assertEqual(result.destination, destination.resolve())
            self.assertEqual(file_hash(source), source_hash)
            self.assertNotIn("Jane Example", repr(result))
            self.assertNotIn("1234-5678", repr(result))

            with fitz.open(destination) as document:
                self.assertEqual(document.page_count, 1)
                self.assertFalse(document.needs_pass)
                self.assertFalse(document.is_encrypted)

                text = document[0].get_text("text")

                self.assertNotIn("Jane Example", text)
                self.assertNotIn("1234-5678", text)
                self.assertIn("Coffee Shop", text)
                self.assertEqual(document[0].search_for("Jane Example"), [])
                self.assertEqual(document[0].search_for("1234-5678"), [])

                metadata = document.metadata or {}
                self.assertIn(
                    metadata.get("author"),
                    (None, "", "none"),
                )
                self.assertIn(
                    metadata.get("title"),
                    (None, "", "none"),
                )
                self.assertEqual(document.embfile_count(), 0)

    def test_no_matching_terms_creates_sanitized_copy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_private_pdf(source)

            result = redact_pdf(
                source,
                editable,
                redacted,
                ("TERM THAT IS NOT PRESENT",),
            )

            self.assertEqual(result.status, "already_clean")
            self.assertEqual(result.redaction_count, 0)
            self.assertTrue(result.destination.is_file())

            with fitz.open(result.destination) as document:
                self.assertIn(
                    "Coffee Shop",
                    document[0].get_text("text"),
                )
                self.assertEqual(document.embfile_count(), 0)

    def test_existing_destination_is_skipped(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_private_pdf(source)

            first = redact_pdf(
                source,
                editable,
                redacted,
                ("Jane Example",),
            )
            first_hash = file_hash(first.destination)

            second = redact_pdf(
                source,
                editable,
                redacted,
                ("Jane Example",),
            )

            self.assertEqual(second.status, "skipped")
            self.assertEqual(
                file_hash(second.destination),
                first_hash,
            )

    def test_term_normalization_strips_blanks_and_deduplicates(
        self,
    ) -> None:
        self.assertEqual(
            normalize_redaction_terms(
                [
                    " Jane Example ",
                    "",
                    "jane example",
                    "Account",
                ]
            ),
            (
                "Jane Example",
                "Account",
            ),
        )

    def test_private_term_not_in_exception_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            source = Path(temporary_folder) / "statement.pdf"
            make_private_pdf(source)

            with self.assertRaises(PDFRedactionError) as context:
                verify_redacted_output(
                    source,
                    expected_page_count=1,
                    terms=("Jane Example",),
                )

            self.assertNotIn(
                "Jane Example",
                str(context.exception),
            )

    def test_failure_cleans_temporary_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"
            temporary = (
                redacted
                / "account"
                / ".statement.pdf.redacted.tmp.pdf"
            )

            make_private_pdf(source)

            with patch(
                "src.bill_extractor.pdf_redaction.verify_redacted_output",
                side_effect=PDFRedactionError("Synthetic verification failure."),
            ):
                with self.assertRaises(PDFRedactionError):
                    redact_pdf(
                        source,
                        editable,
                        redacted,
                        ("Jane Example",),
                    )

            self.assertFalse(temporary.exists())


if __name__ == "__main__":
    unittest.main()
