from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
import unittest

from src.bill_extractor.statement_metadata import StatementMetadata


class StatementMetadataTest(unittest.TestCase):
    def test_complete_statement_period(self) -> None:
        metadata = StatementMetadata(
            source_file=Path("statement.pdf"),
            profile_id="test_profile_v1",
            institution="Test Bank",
            document_type="bank_account_statement",
            statement_start_date=date(2025, 12, 30),
            statement_end_date=date(2026, 1, 28),
        )

        self.assertEqual(
            metadata.statement_start_date,
            date(2025, 12, 30),
        )
        self.assertEqual(
            metadata.statement_end_date,
            date(2026, 1, 28),
        )
        self.assertEqual(
            metadata.source_file,
            Path("statement.pdf"),
        )

    def test_unknown_or_end_only_period_is_allowed(self) -> None:
        unknown = StatementMetadata(
            source_file=Path("unknown.pdf"),
            profile_id="test_profile_v1",
            institution="Test Bank",
            document_type="credit_card_statement",
        )

        end_only = StatementMetadata(
            source_file=Path("end-only.pdf"),
            profile_id="test_profile_v1",
            institution="Test Bank",
            document_type="line_of_credit_statement",
            statement_end_date=date(2026, 1, 19),
        )

        self.assertIsNone(unknown.statement_start_date)
        self.assertIsNone(unknown.statement_end_date)
        self.assertIsNone(end_only.statement_start_date)
        self.assertEqual(
            end_only.statement_end_date,
            date(2026, 1, 19),
        )

    def test_reversed_period_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "statement_start_date",
        ):
            StatementMetadata(
                source_file=Path("statement.pdf"),
                profile_id="test_profile_v1",
                institution="Test Bank",
                document_type="bank_account_statement",
                statement_start_date=date(2026, 1, 28),
                statement_end_date=date(2025, 12, 30),
            )

    def test_identifiers_are_cleaned_and_frozen(self) -> None:
        metadata = StatementMetadata(
            source_file=Path("statement.pdf"),
            profile_id=" test_profile_v1 ",
            institution=" Test Bank ",
            document_type=" bank_account_statement ",
        )

        self.assertEqual(
            metadata.profile_id,
            "test_profile_v1",
        )
        self.assertEqual(
            metadata.institution,
            "Test Bank",
        )
        self.assertEqual(
            metadata.document_type,
            "bank_account_statement",
        )

        with self.assertRaises(FrozenInstanceError):
            metadata.profile_id = "changed"  # type: ignore[misc]
