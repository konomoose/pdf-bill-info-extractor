from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tests" / "tools" / "remove_pdf_security.py"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_test_pdf(path: Path, text: str) -> None:
    """Create a small text-based PDF for workflow testing."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), text)
        document.save(path)


def run_tool(
    source_root: Path,
    output_root: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(source_root),
            str(output_root),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class PDFSecurityRemovalTest(unittest.TestCase):
    def test_recursive_processing_preserves_relative_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            secured = root / "secured_input"
            output = root / "input"

            source_one = secured / "rbc" / "2025" / "jan.pdf"
            source_two = (
                secured
                / "simplii_loc"
                / "statements"
                / "feb.pdf"
            )

            make_test_pdf(
                source_one,
                "RBC January transaction statement",
            )
            make_test_pdf(
                source_two,
                "Simplii LOC February transaction statement",
            )

            source_one_hash = file_hash(source_one)
            source_two_hash = file_hash(source_two)

            result = run_tool(
                secured,
                output,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=result.stdout + result.stderr,
            )

            destination_one = (
                output / "rbc" / "2025" / "jan.pdf"
            )
            destination_two = (
                output
                / "simplii_loc"
                / "statements"
                / "feb.pdf"
            )

            # Originals remain present and unchanged.
            self.assertTrue(source_one.is_file())
            self.assertTrue(source_two.is_file())

            self.assertEqual(
                file_hash(source_one),
                source_one_hash,
            )
            self.assertEqual(
                file_hash(source_two),
                source_two_hash,
            )

            # Same relative paths are recreated under output_root.
            self.assertTrue(destination_one.is_file())
            self.assertTrue(destination_two.is_file())

            with fitz.open(destination_one) as output_document:
                self.assertFalse(output_document.is_encrypted)
                self.assertFalse(output_document.needs_pass)

                output_text = (
                    output_document[0]
                    .get_text("text")
                    .strip()
                )

            self.assertEqual(
                output_text,
                "RBC January transaction statement",
            )

            with fitz.open(destination_two) as output_document:
                output_text = (
                    output_document[0]
                    .get_text("text")
                    .strip()
                )

            self.assertEqual(
                output_text,
                "Simplii LOC February transaction statement",
            )

            self.assertIn(
                "PDF files found: 2",
                result.stdout,
            )
            self.assertIn(
                "Created: 2",
                result.stdout,
            )
            self.assertIn(
                "Skipped: 0",
                result.stdout,
            )
            self.assertIn(
                "Failed: 0",
                result.stdout,
            )

    def test_second_run_skips_verified_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            secured = root / "secured_input"
            output = root / "input"

            source = (
                secured
                / "rbc"
                / "statement.pdf"
            )

            make_test_pdf(
                source,
                "Statement text",
            )

            source_hash = file_hash(source)

            first = run_tool(
                secured,
                output,
            )

            self.assertEqual(
                first.returncode,
                0,
                msg=first.stdout + first.stderr,
            )

            destination = (
                output
                / "rbc"
                / "statement.pdf"
            )

            self.assertTrue(destination.is_file())

            first_output_hash = file_hash(destination)

            second = run_tool(
                secured,
                output,
            )

            self.assertEqual(
                second.returncode,
                0,
                msg=second.stdout + second.stderr,
            )

            # Neither the source nor the verified destination changed.
            self.assertEqual(
                file_hash(source),
                source_hash,
            )
            self.assertEqual(
                file_hash(destination),
                first_output_hash,
            )

            self.assertIn(
                "Created: 0",
                second.stdout,
            )
            self.assertIn(
                "Skipped: 1",
                second.stdout,
            )
            self.assertIn(
                "Failed: 0",
                second.stdout,
            )

    def test_source_and_output_roots_must_be_different(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(root),
                    str(root),
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                1,
            )

            self.assertIn(
                "Source and output folders must be different",
                result.stderr,
            )


if __name__ == "__main__":
    unittest.main()
