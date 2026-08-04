from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz

from src.bill_extractor.pdf_processor import (
    VisaPDFProcessor,
)
from src.bill_extractor.profile_loader import (
    discover_profiles,
)


def write_pdf(
    path: Path,
    text: str,
) -> None:
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text(
            (72, 72),
            text,
        )
        document.save(path)


class NonTransactionDocumentTest(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls) -> None:
        profile = next(
            item
            for item in discover_profiles()
            if item.profile_id
            == "simplii_chequing_account_v1"
        )

        cls.processor = VisaPDFProcessor(
            profile=profile
        )

    def test_simplii_annual_summary_is_recognized(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "annual.pdf"
            )

            write_pdf(
                path,
                "Statement period\n"
                "Annual summary\n"
                "Year\n"
                "Interest\n"
                "Fees",
            )

            self.assertEqual(
                self.processor
                .non_transaction_document_reason(
                    path
                ),
                (
                    "Annual Simplii account summary; "
                    "no transaction table."
                ),
            )

    def test_ordinary_statement_is_not_skipped(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "statement.pdf"
            )

            write_pdf(
                path,
                "Statement period\n"
                "Interest\n"
                "Fees",
            )

            self.assertIsNone(
                self.processor
                .non_transaction_document_reason(
                    path
                )
            )


if __name__ == "__main__":
    unittest.main()
