from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from src.bill_extractor.pdf_redaction import (
    PDFRedactionError,
    StructuredRedactionOptions,
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


def make_transaction_table_pdf(
    path: Path,
    rows: tuple[tuple[str, ...], ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((50, 50), "Transaction Date")
        page.insert_text((145, 50), "Transaction Description")
        page.insert_text((410, 50), "Amount($)")
        page.insert_text((500, 50), "Balance($)")

        y = 80
        for row_number, lines in enumerate(rows, start=1):
            page.insert_text((50, y), f"2025-01-{row_number:02d}")
            for offset, line in enumerate(lines):
                page.insert_text((145, y + (offset * 16)), line)

            page.insert_text((410, y), "-10.00")
            page.insert_text((500, y), "100.00")
            y += 28 + ((len(lines) - 1) * 16)

        document.save(path)


def make_eq_transaction_table_pdf(
    path: Path,
    rows: tuple[tuple[str, ...], ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page(width=792, height=792)
        page.insert_text((50, 50), "Date")
        page.insert_text((145, 50), "Description")
        page.insert_text((500, 50), "Withdrawals")
        page.insert_text((590, 50), "Deposits")
        page.insert_text((680, 50), "Balance")

        y = 80
        for row_number, lines in enumerate(rows, start=1):
            page.insert_text((50, y), f"Jan {row_number:02d}")
            for offset, line in enumerate(lines):
                page.insert_text((145, y + (offset * 16)), line)

            page.insert_text((590, y), "$10.00")
            page.insert_text((680, y), "$100.00")
            y += 28 + ((len(lines) - 1) * 16)

        document.save(path)


def make_offset_transaction_table_pdf(
    path: Path,
    rows: tuple[tuple[float, tuple[str, ...]], ...],
    *,
    closing_balance_y: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((50, 50), "Transaction Date")
        page.insert_text((145, 50), "Transaction Description")
        page.insert_text((410, 50), "Amount($)")
        page.insert_text((500, 50), "Balance($)")

        for row_number, (y, lines) in enumerate(rows, start=1):
            side_y = y + 6
            page.insert_text((50, side_y), f"2025-02-{row_number:02d}")

            for offset, line in enumerate(lines):
                page.insert_text((145, y + (offset * 12)), line)

            page.insert_text((410, side_y), "-11.00")
            page.insert_text((500, side_y), "200.00")

        if closing_balance_y is not None:
            page.insert_text((145, closing_balance_y), "Closing Balance")
            page.insert_text((500, closing_balance_y + 6), "300.00")

        document.save(path)


def redacted_text(
    path: Path,
) -> str:
    with fitz.open(path) as document:
        return "\n".join(
            page.get_text("text")
            for page in document
        )


ETRANSFER_OPTION = StructuredRedactionOptions(
    etransfer_keep_first_name_only=True,
)


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

    def test_etransfer_from_keeps_first_name_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: ALPHA BRAVO CHARLIE",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer From:", text)
            self.assertIn("ALPHA", text)
            self.assertNotIn("BRAVO", text)
            self.assertNotIn("CHARLIE", text)
            self.assertNotIn("ALPHA BRAVO", repr(result))

    def test_etransfer_to_keeps_first_name_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer To: DELTA ECHO",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer To:", text)
            self.assertIn("DELTA", text)
            self.assertNotIn("ECHO", text)

    def test_eq_received_from_keeps_first_name_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_eq_transaction_table_pdf(
                source,
                (
                    (
                        "Interac e-Transfer received from ALPHA BRAVO",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("Interac e-Transfer received from", text)
            self.assertIn("ALPHA", text)
            self.assertNotIn("BRAVO", text)
            self.assertNotIn("ALPHA BRAVO", repr(result))

    def test_eq_received_from_three_word_name_redacts_later_words(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_eq_transaction_table_pdf(
                source,
                (
                    (
                        "Interac e-Transfer received from CHARLIE DELTA ECHO",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("CHARLIE", text)
            self.assertNotIn("DELTA", text)
            self.assertNotIn("ECHO", text)

    def test_eq_received_from_case_variation_is_redacted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_eq_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC E-TRANSFER RECEIVED FROM FOXTROT GOLF",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("FOXTROT", text)
            self.assertNotIn("GOLF", text)

    def test_eq_received_from_wrapped_name_redacts_continuation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_eq_transaction_table_pdf(
                source,
                (
                    (
                        "Interac e-Transfer received from HOTEL INDIA",
                        "JULIET",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("HOTEL", text)
            self.assertNotIn("INDIA", text)
            self.assertNotIn("JULIET", text)

    def test_etransfer_two_word_name_redacts_second_word(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: GOLF HOTEL",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("GOLF", text)
            self.assertNotIn("HOTEL", text)

    def test_wrapped_etransfer_name_redacts_continuation_words(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: ALPHA BRAVO",
                        "CHARLIE",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer From:", text)
            self.assertIn("ALPHA", text)
            self.assertNotIn("BRAVO", text)
            self.assertNotIn("CHARLIE", text)

    def test_offset_side_columns_do_not_end_description_band(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_offset_transaction_table_pdf(
                source,
                (
                    (
                        100,
                        (
                            "INTERAC e-Transfer From: ALPHA BRAVO",
                            "CHARLIE",
                        ),
                    ),
                    (
                        130,
                        (
                            "PAYROLL DELTA NEXTROW",
                        ),
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer From:", text)
            self.assertIn("ALPHA", text)
            self.assertNotIn("BRAVO", text)
            self.assertNotIn("CHARLIE", text)
            self.assertIn("PAYROLL", text)
            self.assertIn("DELTA", text)
            self.assertIn("NEXTROW", text)
            self.assertIn("2025-02-01", text)
            self.assertIn("2025-02-02", text)
            self.assertIn("-11.00", text)
            self.assertIn("200.00", text)

    def test_two_continuation_lines_are_redacted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: INDIA JULIET",
                        "KILO",
                        "LIMA",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer From:", text)
            self.assertIn("INDIA", text)
            self.assertNotIn("JULIET", text)
            self.assertNotIn("KILO", text)
            self.assertNotIn("LIMA", text)

    def test_offset_two_continuation_lines_are_redacted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_offset_transaction_table_pdf(
                source,
                (
                    (
                        100,
                        (
                            "INTERAC e-Transfer From: INDIA JULIET",
                            "KILO",
                            "LIMA",
                        ),
                    ),
                    (
                        150,
                        (
                            "PAYROLL MIKE NEXTROW",
                        ),
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INDIA", text)
            self.assertNotIn("JULIET", text)
            self.assertNotIn("KILO", text)
            self.assertNotIn("LIMA", text)
            self.assertIn("PAYROLL MIKE NEXTROW", text)

    def test_offset_to_prefix_keeps_first_name_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_offset_transaction_table_pdf(
                source,
                (
                    (
                        100,
                        (
                            "INTERAC e-Transfer To: DELTA ECHO",
                        ),
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer To:", text)
            self.assertIn("DELTA", text)
            self.assertNotIn("ECHO", text)

    def test_next_wrapped_non_etransfer_row_is_untouched(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_offset_transaction_table_pdf(
                source,
                (
                    (
                        100,
                        (
                            "INTERAC e-Transfer From: PAPA QUEBEC",
                            "ROMEO",
                        ),
                    ),
                    (
                        140,
                        (
                            "CARD PAYMENT SIERRA",
                            "TANGO",
                        ),
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("PAPA", text)
            self.assertNotIn("QUEBEC", text)
            self.assertNotIn("ROMEO", text)
            self.assertIn("CARD PAYMENT SIERRA", text)
            self.assertIn("TANGO", text)

    def test_final_etransfer_stops_before_closing_balance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_offset_transaction_table_pdf(
                source,
                (
                    (
                        100,
                        (
                            "INTERAC e-Transfer From: UNIFORM VICTOR",
                            "WHISKEY",
                        ),
                    ),
                ),
                closing_balance_y=130,
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("UNIFORM", text)
            self.assertNotIn("VICTOR", text)
            self.assertNotIn("WHISKEY", text)
            self.assertIn("Closing Balance", text)
            self.assertIn("300.00", text)

    def test_etransfer_redaction_stops_before_next_row(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: MIKE NOVEMBER",
                        "OSCAR",
                    ),
                    (
                        "PAYROLL NOVEMBER NEXTROW",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                (),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer From:", text)
            self.assertIn("MIKE", text)
            self.assertNotIn("OSCAR", text)
            self.assertIn("PAYROLL", text)
            self.assertIn("NOVEMBER", text)
            self.assertIn("NEXTROW", text)
            self.assertIn("2025-01-01", text)
            self.assertIn("2025-01-02", text)
            self.assertIn("-10.00", text)
            self.assertIn("100.00", text)

    def test_non_interac_description_is_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "CARD PAYMENT PAPA QUEBEC",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                ("MISSING EXACT TERM",),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("CARD PAYMENT PAPA QUEBEC", text)
            self.assertEqual(result.redaction_count, 0)

    def test_option_disabled_leaves_etransfer_names_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: ROMEO SIERRA TANGO",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                ("MISSING EXACT TERM",),
                structured_options=StructuredRedactionOptions(),
            )

            text = redacted_text(result.destination)

            self.assertIn("ROMEO", text)
            self.assertIn("SIERRA", text)
            self.assertIn("TANGO", text)

    def test_exact_terms_and_etransfer_rule_work_together(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            editable = root / "editable_input"
            redacted = root / "redacted_input"
            source = editable / "account" / "statement.pdf"

            make_transaction_table_pdf(
                source,
                (
                    (
                        "INTERAC e-Transfer From: UNIFORM VICTOR WHISKEY",
                    ),
                    (
                        "Account token: 1234-5678",
                    ),
                ),
            )

            result = redact_pdf(
                source,
                editable,
                redacted,
                ("1234-5678",),
                structured_options=ETRANSFER_OPTION,
            )

            text = redacted_text(result.destination)

            self.assertIn("INTERAC e-Transfer From:", text)
            self.assertIn("UNIFORM", text)
            self.assertNotIn("VICTOR", text)
            self.assertNotIn("WHISKEY", text)
            self.assertNotIn("1234-5678", text)


if __name__ == "__main__":
    unittest.main()
