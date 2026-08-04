from pathlib import Path
import unittest

import fitz

from src.bill_extractor.pdf_processor import (
    VisaPDFProcessor,
)
from src.bill_extractor.profile_loader import (
    load_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROFILE_PATH = (
    PROJECT_ROOT
    / "config"
    / "profiles"
    / "rbc_visa_credit_card.json"
)


class RBCVisaMultipleSectionsTest(
    unittest.TestCase
):
    def test_interest_chart_does_not_hide_transactions(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile=load_profile(PROFILE_PATH)
        )

        with fitz.open() as document:
            page = document.new_page(
                width=612,
                height=792,
            )

            page.insert_text(
                (40, 72),
                "Transaction date",
            )
            page.insert_text(
                (140, 72),
                "Posting date",
            )
            page.insert_text(
                (240, 72),
                "Activity description",
            )
            page.insert_text(
                (500, 72),
                "Amount",
            )

            page.insert_text(
                (40, 100),
                "JAN 01 JAN 02 FIRST 10.00",
            )

            page.insert_text(
                (360, 500),
                "INTEREST RATE CHART",
            )

            rows = (
                processor
                ._extract_rbc_page_transactions(
                    page
                )
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["Activity description"],
            "FIRST",
        )

    def test_topmost_multiline_header_is_selected(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile=load_profile(PROFILE_PATH)
        )

        with fitz.open() as document:
            page = document.new_page(
                width=612,
                height=792,
            )

            # Earlier heading split over three lines.
            page.insert_text(
                (40, 60),
                "Transaction",
            )
            page.insert_text(
                (40, 72),
                "date",
            )
            page.insert_text(
                (140, 84),
                "Posting date",
            )
            page.insert_text(
                (240, 84),
                "Activity description",
            )
            page.insert_text(
                (500, 84),
                "Amount",
            )

            page.insert_text(
                (40, 112),
                "JAN 01 JAN 02 FIRST 10.00",
            )

            page.insert_text(
                (240, 136),
                "Subtotal of monthly activity",
            )

            # Later heading fits on one line. The old finder
            # selected this because it searched short windows
            # before considering the earlier split heading.
            page.insert_text(
                (40, 220),
                "Transaction date",
            )
            page.insert_text(
                (140, 220),
                "Posting date",
            )
            page.insert_text(
                (240, 220),
                "Activity description",
            )
            page.insert_text(
                (500, 220),
                "Amount",
            )

            page.insert_text(
                (40, 248),
                "JAN 03 JAN 04 SECOND 20.00",
            )

            rows = (
                processor
                ._extract_rbc_page_transactions(
                    page
                )
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [
                row["Activity description"]
                for row in rows
            ],
            ["FIRST", "SECOND"],
        )

    def test_rows_after_first_subtotal_are_retained(
        self,
    ) -> None:
        processor = VisaPDFProcessor(
            profile=load_profile(PROFILE_PATH)
        )

        with fitz.open() as document:
            page = document.new_page(
                width=612,
                height=792,
            )

            def add_header(y: float) -> None:
                page.insert_text(
                    (40, y),
                    "Transaction date",
                )
                page.insert_text(
                    (140, y),
                    "Posting date",
                )
                page.insert_text(
                    (240, y),
                    "Activity description",
                )
                page.insert_text(
                    (500, y),
                    "Amount",
                )

            def add_row(
                y: float,
                transaction_month: str,
                transaction_day: str,
                posting_month: str,
                posting_day: str,
                description: str,
                amount: str,
            ) -> None:
                page.insert_text(
                    (40, y),
                    transaction_month,
                )
                page.insert_text(
                    (75, y),
                    transaction_day,
                )
                page.insert_text(
                    (140, y),
                    posting_month,
                )
                page.insert_text(
                    (175, y),
                    posting_day,
                )
                page.insert_text(
                    (240, y),
                    description,
                )
                page.insert_text(
                    (500, y),
                    amount,
                )

            add_header(72)

            add_row(
                96,
                "JAN",
                "01",
                "JAN",
                "02",
                "FIRST",
                "10.00",
            )

            page.insert_text(
                (240, 116),
                "Subtotal of monthly activity",
            )

            add_header(140)

            add_row(
                164,
                "JAN",
                "03",
                "JAN",
                "04",
                "SECOND",
                "20.00",
            )

            page.insert_text(
                (240, 184),
                "Subtotal of monthly activity",
            )

            rows = (
                processor
                ._extract_rbc_page_transactions(
                    page
                )
            )

        self.assertEqual(len(rows), 2)

        self.assertEqual(
            rows[0]["Activity description"],
            "FIRST",
        )
        self.assertEqual(
            rows[1]["Activity description"],
            "SECOND",
        )

        self.assertEqual(
            rows[0]["Amount($)"],
            "10.00",
        )
        self.assertEqual(
            rows[1]["Amount($)"],
            "20.00",
        )


if __name__ == "__main__":
    unittest.main()
