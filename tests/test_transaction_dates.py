from datetime import date
from pathlib import Path
import unittest

from src.bill_extractor.statement_metadata import StatementMetadata
from src.bill_extractor.transaction_dates import (
    TransactionDateError,
    resolve_related_date,
    resolve_transaction_date,
)


def metadata(
    start: date | None,
    end: date | None,
) -> StatementMetadata:
    return StatementMetadata(
        source_file=Path("statement.pdf"),
        profile_id="test_profile_v1",
        institution="Test Bank",
        document_type="bank_account_statement",
        statement_start_date=start,
        statement_end_date=end,
    )


class TransactionDateResolutionTest(unittest.TestCase):
    def test_same_year_month_first_date(self) -> None:
        result = resolve_transaction_date(
            "Mar 14",
            metadata(
                date(2025, 3, 1),
                date(2025, 3, 31),
            ),
        )

        self.assertEqual(result, date(2025, 3, 14))

    def test_cross_year_period_resolves_each_year(self) -> None:
        statement = metadata(
            date(2025, 12, 30),
            date(2026, 1, 28),
        )

        self.assertEqual(
            resolve_transaction_date(
                "Dec 30",
                statement,
            ),
            date(2025, 12, 30),
        )
        self.assertEqual(
            resolve_transaction_date(
                "Jan 4",
                statement,
            ),
            date(2026, 1, 4),
        )

    def test_day_first_date_is_supported(self) -> None:
        result = resolve_transaction_date(
            "14 Dec",
            metadata(
                date(2025, 12, 1),
                date(2025, 12, 31),
            ),
        )

        self.assertEqual(result, date(2025, 12, 14))

    def test_iso_date_is_preserved(self) -> None:
        result = resolve_transaction_date(
            "2025-06-18",
            metadata(None, None),
        )

        self.assertEqual(result, date(2025, 6, 18))

    def test_partial_date_requires_complete_period(self) -> None:
        with self.assertRaisesRegex(
            TransactionDateError,
            "complete statement period",
        ):
            resolve_transaction_date(
                "Jan 4",
                metadata(
                    None,
                    date(2026, 1, 28),
                ),
            )

    def test_date_outside_period_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            TransactionDateError,
            "does not fall within",
        ):
            resolve_transaction_date(
                "Apr 2",
                metadata(
                    date(2025, 3, 1),
                    date(2025, 3, 31),
                ),
            )

    def test_invalid_calendar_date_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            TransactionDateError,
            "does not fall within",
        ):
            resolve_transaction_date(
                "Feb 30",
                metadata(
                    date(2025, 2, 1),
                    date(2025, 2, 28),
                ),
            )

    def test_ambiguous_multiyear_period_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            TransactionDateError,
            "ambiguous",
        ):
            resolve_transaction_date(
                "Jan 4",
                metadata(
                    date(2025, 1, 1),
                    date(2026, 1, 31),
                ),
            )

    def test_related_date_can_follow_statement_end(
        self,
    ) -> None:
        result = resolve_related_date(
            "Jan 30",
            date(2025, 1, 29),
        )

        self.assertEqual(
            result,
            date(2025, 1, 30),
        )

    def test_related_date_crosses_calendar_year(
        self,
    ) -> None:
        result = resolve_related_date(
            "Jan 2",
            date(2025, 12, 31),
        )

        self.assertEqual(
            result,
            date(2026, 1, 2),
        )

    def test_related_date_too_far_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            TransactionDateError,
            "cannot be resolved",
        ):
            resolve_related_date(
                "Dec 1",
                date(2025, 3, 15),
            )


if __name__ == "__main__":
    unittest.main()
