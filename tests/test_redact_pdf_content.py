from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tests" / "tools" / "redact_pdf_content.py"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_test_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page()

        page.insert_text(
            (72, 72),
            "Customer: Jane Example",
        )
        page.insert_text(
            (72, 92),
            "Account: 1234-5678",
        )
        page.insert_text(
            (72, 112),
            "Merchant: Coffee Shop",
        )

        document.set_metadata(
            {
                "author": "Jane Example",
                "title": "Private bank statement",
            }
        )

        document.embfile_add(
            "private-note.txt",
            b"Sensitive embedded content",
        )

        document.save(path)


def write_rules(
    path: Path,
    *,
    global_terms: list[str] | None = None,
    institution_terms: dict[str, list[str]] | None = None,
) -> None:
    data = {
        "global_terms": global_terms or [],
        "institution_terms": institution_terms or {},
    }

    path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def run_tool(
    source_root: Path,
    output_root: Path,
    rules_path: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(source_root),
            str(output_root),
            "--rules",
            str(rules_path),
            *extra,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class PDFRedactionWorkflowTest(unittest.TestCase):
    def test_recursive_redaction_preserves_source_and_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            editable = root / "editable_input"
            redacted = root / "redacted_input"
            rules_path = root / "rules.json"

            source = (
                editable
                / "rbc"
                / "2025"
                / "statement.pdf"
            )

            make_test_pdf(source)

            source_hash = file_hash(source)

            write_rules(
                rules_path,
                global_terms=["Jane Example"],
                institution_terms={
                    "rbc": ["1234-5678"],
                },
            )

            result = run_tool(
                editable,
                redacted,
                rules_path,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=result.stdout + result.stderr,
            )

            destination = (
                redacted
                / "rbc"
                / "2025"
                / "statement.pdf"
            )

            self.assertTrue(destination.is_file())

            # The editable source must remain unchanged.
            self.assertEqual(
                file_hash(source),
                source_hash,
            )

            with fitz.open(destination) as document:
                self.assertEqual(document.page_count, 1)
                self.assertFalse(document.needs_pass)
                self.assertFalse(document.is_encrypted)

                page = document[0]
                text = page.get_text("text")

                self.assertNotIn(
                    "Jane Example",
                    text,
                )
                self.assertNotIn(
                    "1234-5678",
                    text,
                )
                self.assertIn(
                    "Coffee Shop",
                    text,
                )

                self.assertEqual(
                    page.search_for("Jane Example"),
                    [],
                )
                self.assertEqual(
                    page.search_for("1234-5678"),
                    [],
                )

                metadata = document.metadata or {}

                self.assertIn(
                    metadata.get("author"),
                    (None, "", "none"),
                )
                self.assertIn(
                    metadata.get("title"),
                    (None, "", "none"),
                )

                self.assertEqual(
                    document.embfile_count(),
                    0,
                )

            self.assertIn(
                "PDF files found: 1",
                result.stdout,
            )
            self.assertIn(
                "Created: 1",
                result.stdout,
            )
            self.assertIn(
                "Skipped existing: 0",
                result.stdout,
            )
            self.assertIn(
                "Failed: 0",
                result.stdout,
            )

    def test_existing_destination_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            editable = root / "editable_input"
            redacted = root / "redacted_input"
            rules_path = root / "rules.json"

            source = editable / "rbc" / "statement.pdf"

            make_test_pdf(source)

            write_rules(
                rules_path,
                global_terms=["Jane Example"],
                institution_terms={
                    "rbc": ["1234-5678"],
                },
            )

            first = run_tool(
                editable,
                redacted,
                rules_path,
            )

            self.assertEqual(
                first.returncode,
                0,
                msg=first.stdout + first.stderr,
            )

            destination = redacted / "rbc" / "statement.pdf"
            first_hash = file_hash(destination)

            second = run_tool(
                editable,
                redacted,
                rules_path,
            )

            self.assertEqual(
                second.returncode,
                0,
                msg=second.stdout + second.stderr,
            )

            self.assertEqual(
                file_hash(destination),
                first_hash,
            )

            self.assertIn(
                "Created: 0",
                second.stdout,
            )
            self.assertIn(
                "Skipped existing: 1",
                second.stdout,
            )
            self.assertIn(
                "Failed: 0",
                second.stdout,
            )

    def test_no_matching_terms_creates_sanitized_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            editable = root / "editable_input"
            redacted = root / "redacted_input"
            rules_path = root / "rules.json"

            source = editable / "cibc" / "statement.pdf"

            make_test_pdf(source)
            source_hash = file_hash(source)

            write_rules(
                rules_path,
                institution_terms={
                    "cibc": ["TERM THAT IS NOT PRESENT"],
                },
            )

            result = run_tool(
                editable,
                redacted,
                rules_path,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=result.stdout + result.stderr,
            )

            destination = redacted / "cibc" / "statement.pdf"

            # An already-clean input still receives a sanitized output copy.
            self.assertTrue(destination.is_file())

            # The editable source must remain unchanged.
            self.assertEqual(
                file_hash(source),
                source_hash,
            )

            with fitz.open(destination) as document:
                self.assertEqual(document.page_count, 1)
                self.assertFalse(document.needs_pass)
                self.assertFalse(document.is_encrypted)

                # No configured page text matched, so visible content remains.
                text = document[0].get_text("text")
                self.assertIn("Jane Example", text)
                self.assertIn("1234-5678", text)
                self.assertIn("Coffee Shop", text)

                # Document-level private material is still scrubbed.
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

            self.assertIn(
                "Created: 0",
                result.stdout,
            )
            self.assertIn(
                "Already clean: 1",
                result.stdout,
            )
            self.assertIn(
                "No rules: 0",
                result.stdout,
            )
            self.assertIn(
                "Skipped existing: 0",
                result.stdout,
            )
            self.assertIn(
                "Failed: 0",
                result.stdout,
            )

    def test_source_and_output_roots_cannot_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            editable = root / "editable_input"
            editable.mkdir()

            rules_path = root / "rules.json"

            write_rules(
                rules_path,
                global_terms=["Jane Example"],
            )

            result = run_tool(
                editable,
                editable,
                rules_path,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            self.assertIn(
                "must be separate",
                result.stderr,
            )

    def test_dry_run_reports_matches_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            editable = root / "editable_input"
            redacted = root / "redacted_input"
            rules_path = root / "rules.json"

            source = editable / "rbc" / "2025" / "statement.pdf"

            make_test_pdf(source)

            source_hash = file_hash(source)

            write_rules(
                rules_path,
                global_terms=["Jane Example"],
                institution_terms={
                    "rbc": ["1234-5678"],
                },
            )

            result = run_tool(
                editable,
                redacted,
                rules_path,
                "--dry-run",
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=result.stdout + result.stderr,
            )

            self.assertEqual(
                file_hash(source),
                source_hash,
            )

            self.assertFalse(
                (
                    redacted
                    / "rbc"
                    / "2025"
                    / "statement.pdf"
                ).exists()
            )

            self.assertIn(
                "WOULD REDACT: rbc",
                result.stdout,
            )
            self.assertIn(
                "Configured terms: 2",
                result.stdout,
            )
            self.assertIn(
                "Term 1 matches: 1",
                result.stdout,
            )
            self.assertIn(
                "Term 2 matches: 1",
                result.stdout,
            )
            self.assertIn(
                "Total matches: 2",
                result.stdout,
            )
            self.assertIn(
                "Would redact: 1",
                result.stdout,
            )
            self.assertIn(
                "Failed: 0",
                result.stdout,
            )
            self.assertNotIn(
                "Jane Example",
                result.stdout,
            )
            self.assertNotIn(
                "1234-5678",
                result.stdout,
            )
            self.assertNotIn(
                "Jane Example",
                result.stderr,
            )
            self.assertNotIn(
                "1234-5678",
                result.stderr,
            )

if __name__ == "__main__":
    unittest.main()
