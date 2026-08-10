from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from src.bill_extractor.statement_metadata import StatementMetadata
from src.bill_extractor.transaction_normalizer import (
    NORMALIZED_COLUMNS,
    NormalizationError,
    normalize_transactions,
)


def make_metadata(
    profile_id: str,
    document_type: str,
    *,
    start: date = date(2025, 3, 1),
    end: date = date(2025, 3, 31),
) -> StatementMetadata:
    return StatementMetadata(
        source_file=Path("input/test/statement.pdf"),
        profile_id=profile_id,
        institution="Test Bank",
        document_type=document_type,
        statement_start_date=start,
        statement_end_date=end,
    )


class TransactionNormalizerTest(unittest.TestCase):
    def test_all_profile_mappings(self) -> None:
        cases = [
            (
                "capital_one_mastercard_v1",
                "credit_card_statement",
                {
                    "Transaction date": "Mar 14",
                    "Posting date": "Mar 15",
                    "Description": "Purchase",
                    "Amount": "$10.00",
                },
                {"amount": "10.00"},
            ),
            (
                "cibc_credit_card_v1",
                "credit_card_statement",
                {
                    "Trans date": "Mar 14",
                    "Post date": "Mar 15",
                    "Description": "Purchase",
                    "Spend Categories": "General",
                    "Amount($)": "$10.00",
                },
                {
                    "category": "General",
                    "amount": "10.00",
                },
            ),
            (
                "rbc_chequing_account_v1",
                "bank_account_statement",
                {
                    "Date": "14 Mar",
                    "Description": "Deposit",
                    "Withdrawals ($)": "",
                    "Deposits ($)": "$100.00",
                    "Balance ($)": "$500.00",
                },
                {
                    "deposit": "100.00",
                    "balance": "500.00",
                },
            ),
            (
                "rbc_loc_v1",
                "line_of_credit_statement",
                {
                    "Date": "14 Mar",
                    "Description": "Payment",
                    "Interest/Fees/Insurance ($)": "",
                    "Withdrawals ($)": "",
                    "Payments ($)": "$50.00",
                    "Balance owing ($)": "$450.00",
                },
                {
                    "payment": "50.00",
                    "balance": "450.00",
                },
            ),
            (
                "rbc_visa_credit_card_v1",
                "credit_card_statement",
                {
                    "Transaction date": "Mar 14",
                    "Posting date": "Mar 15",
                    "Activity description": "Purchase",
                    "Amount($)": "$10.00",
                },
                {"amount": "10.00"},
            ),
            (
                "simplii_chequing_account_v1",
                "bank_account_statement",
                {
                    "Trans. date": "Mar 14",
                    "Eff. date": "Mar 15",
                    "Transaction": "Deposit",
                    "Funds out": "",
                    "Funds in": "$100.00",
                    "Balance": "$500.00",
                },
                {
                    "deposit": "100.00",
                    "balance": "500.00",
                },
            ),
            (
                "tangerine_chequing_account_v1",
                "bank_account_statement",
                {
                    "Date": "2025-03-14",
                    "Description": "Deposit",
                    "Withdrawals ($)": "",
                    "Deposits ($)": "$100.00",
                    "Balance ($)": "$500.00",
                },
                {
                    "deposit": "100.00",
                    "balance": "500.00",
                },
            ),
            (
                "simplii_loc_v1",
                "line_of_credit_statement",
                {
                    "Trans. date": "Mar 14",
                    "Eff. date": "Mar 15",
                    "Transaction": "Payment",
                    "Funds out": "",
                    "Funds in": "$50.00",
                    "Balance": "450.00-",
                },
                {
                    "deposit": "50.00",
                    "balance": "-450.00",
                },
            ),
            (
                "simplii_visa_v1",
                "credit_card_statement",
                {
                    "Trans date": "Mar 14",
                    "Post date": "Mar 15",
                    "Description": "Purchase",
                    "Spend Categories": "General",
                    "Amount($)": "$10.00",
                },
                {
                    "category": "General",
                    "amount": "10.00",
                },
            ),
            (
                "td_visa_credit_card_v1",
                "credit_card_statement",
                {
                    "Transaction date": "Mar 14",
                    "Posting date": "Mar 15",
                    "Activity description": "Refund",
                    "Amount($)": "-$10.00",
                },
                {"amount": "-10.00"},
            ),
            (
                "triangle_mastercard_v1",
                "credit_card_statement",
                {
                    "Transaction date": "Mar 14",
                    "Posting date": "Mar 15",
                    "Activity description": "Refund",
                    "Amount($)": "10.00-",
                },
                {"amount": "-10.00"},
            ),
        ]

        for (
            profile_id,
            document_type,
            source,
            expected,
        ) in cases:
            with self.subTest(profile_id=profile_id):
                result = normalize_transactions(
                    pd.DataFrame([source]),
                    make_metadata(
                        profile_id,
                        document_type,
                    ),
                )

                self.assertEqual(
                    list(result.columns),
                    list(NORMALIZED_COLUMNS),
                )
                self.assertEqual(
                    result.loc[0, "transaction_date"],
                    "2025-03-14",
                )
                self.assertEqual(
                    result.loc[0, "description"],
                    source.get(
                        "Description",
                        source.get(
                            "Activity description",
                            source.get(
                                "Transaction",
                            ),
                        ),
                    ),
                )

                for column, value in expected.items():
                    self.assertEqual(
                        result.loc[0, column],
                        value,
                    )

    def test_cross_year_dates_are_resolved(self) -> None:
        transactions = pd.DataFrame(
            [
                {
                    "Trans. date": "Dec 30",
                    "Eff. date": "Dec 31",
                    "Transaction": "First",
                    "Funds out": "10.00",
                    "Funds in": "",
                    "Balance": "90.00",
                },
                {
                    "Trans. date": "Jan 4",
                    "Eff. date": "Jan 5",
                    "Transaction": "Second",
                    "Funds out": "",
                    "Funds in": "20.00",
                    "Balance": "110.00",
                },
            ]
        )

        result = normalize_transactions(
            transactions,
            make_metadata(
                "simplii_chequing_account_v1",
                "bank_account_statement",
                start=date(2025, 12, 30),
                end=date(2026, 1, 28),
            ),
        )

        self.assertEqual(
            list(result["transaction_date"]),
            ["2025-12-30", "2026-01-04"],
        )
        self.assertEqual(
            list(result["effective_date"]),
            ["2025-12-31", "2026-01-05"],
        )

    def test_credit_card_purchase_may_precede_period(
        self,
    ) -> None:
        result = normalize_transactions(
            pd.DataFrame(
                [
                    {
                        "Trans date": "Nov 09",
                        "Post date": "Nov 12",
                        "Description": "Purchase",
                        "Spend Categories": "General",
                        "Amount($)": "10.00",
                    }
                ]
            ),
            make_metadata(
                "simplii_visa_v1",
                "credit_card_statement",
                start=date(2024, 11, 11),
                end=date(2024, 12, 10),
            ),
        )

        self.assertEqual(
            result.loc[
                0,
                "transaction_date",
            ],
            "2024-11-09",
        )
        self.assertEqual(
            result.loc[
                0,
                "posting_date",
            ],
            "2024-11-12",
        )

    def test_delayed_card_posting_is_resolved(
        self,
    ) -> None:
        result = normalize_transactions(
            pd.DataFrame(
                [
                    {
                        "Transaction date": "Jan 19",
                        "Posting date": "Jul 20",
                        "Description": "Adjustment",
                        "Amount": "10.00",
                    }
                ]
            ),
            make_metadata(
                "capital_one_mastercard_v1",
                "credit_card_statement",
                start=date(2022, 6, 24),
                end=date(2022, 7, 23),
            ),
        )

        self.assertEqual(
            result.loc[0, "transaction_date"],
            "2022-01-19",
        )
        self.assertEqual(
            result.loc[0, "posting_date"],
            "2022-07-20",
        )

    def test_simplii_month_end_entry_may_follow_period(
        self,
    ) -> None:
        result = normalize_transactions(
            pd.DataFrame(
                [
                    {
                        "Trans. date": "Sep 30",
                        "Eff. date": "Sep 30",
                        "Transaction": "Interest",
                        "Funds out": "",
                        "Funds in": "0.01",
                        "Balance": "100.01",
                    }
                ]
            ),
            make_metadata(
                "simplii_chequing_account_v1",
                "bank_account_statement",
                start=date(2022, 8, 24),
                end=date(2022, 9, 29),
            ),
        )

        self.assertEqual(
            result.loc[0, "transaction_date"],
            "2022-09-30",
        )
        self.assertEqual(
            result.loc[0, "effective_date"],
            "2022-09-30",
        )

    def test_simplii_post_period_grace_is_limited(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            NormalizationError,
            "does not fall within",
        ):
            normalize_transactions(
                pd.DataFrame(
                    [
                        {
                            "Trans. date": "Oct 3",
                            "Eff. date": "Oct 3",
                            "Transaction": "Interest",
                            "Funds out": "",
                            "Funds in": "0.01",
                            "Balance": "100.01",
                        }
                    ]
                ),
                make_metadata(
                    "simplii_chequing_account_v1",
                    "bank_account_statement",
                    start=date(2022, 8, 24),
                    end=date(2022, 9, 29),
                ),
            )

    def test_blank_directional_fields_remain_blank(self) -> None:
        result = normalize_transactions(
            pd.DataFrame(
                [
                    {
                        "Date": "14 Mar",
                        "Description": "Deposit",
                        "Withdrawals ($)": "",
                        "Deposits ($)": "100.00",
                        "Balance ($)": "",
                    }
                ]
            ),
            make_metadata(
                "rbc_chequing_account_v1",
                "bank_account_statement",
            ),
        )

        self.assertEqual(
            result.loc[0, "withdrawal"],
            "",
        )
        self.assertEqual(
            result.loc[0, "deposit"],
            "100.00",
        )
        self.assertEqual(
            result.loc[0, "balance"],
            "",
        )

    def test_provenance_fields_are_added(self) -> None:
        result = normalize_transactions(
            pd.DataFrame(
                [
                    {
                        "Transaction date": "Mar 14",
                        "Posting date": "Mar 15",
                        "Description": "Purchase",
                        "Amount": "10.00",
                    }
                ]
            ),
            make_metadata(
                "capital_one_mastercard_v1",
                "credit_card_statement",
            ),
        )

        self.assertEqual(
            result.loc[0, "institution"],
            "Test Bank",
        )
        self.assertEqual(
            result.loc[0, "account_type"],
            "credit_card_statement",
        )
        self.assertEqual(
            result.loc[0, "profile_id"],
            "capital_one_mastercard_v1",
        )
        self.assertEqual(
            result.loc[0, "source_file"],
            "input/test/statement.pdf",
        )

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            NormalizationError,
            "No normalization mapping",
        ):
            normalize_transactions(
                pd.DataFrame(),
                make_metadata(
                    "unknown_profile",
                    "credit_card_statement",
                ),
            )

    def test_missing_source_column_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            NormalizationError,
            "Posting date",
        ):
            normalize_transactions(
                pd.DataFrame(
                    [
                        {
                            "Transaction date": "Mar 14",
                            "Description": "Purchase",
                            "Amount": "10.00",
                        }
                    ]
                ),
                make_metadata(
                    "capital_one_mastercard_v1",
                    "credit_card_statement",
                ),
            )

    def test_float_money_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            NormalizationError,
            "Floating-point",
        ):
            normalize_transactions(
                pd.DataFrame(
                    [
                        {
                            "Transaction date": "Mar 14",
                            "Posting date": "Mar 15",
                            "Description": "Purchase",
                            "Amount": 10.25,
                        }
                    ]
                ),
                make_metadata(
                    "capital_one_mastercard_v1",
                    "credit_card_statement",
                ),
            )

    def test_effective_date_may_follow_period_end(
        self,
    ) -> None:
        result = normalize_transactions(
            pd.DataFrame(
                [
                    {
                        "Trans. date": "Jan 29",
                        "Eff. date": "Jan 30",
                        "Transaction": "Interest",
                        "Funds out": "",
                        "Funds in": "0.03",
                        "Balance": "100.03",
                    }
                ]
            ),
            make_metadata(
                "simplii_chequing_account_v1",
                "bank_account_statement",
                start=date(2024, 12, 30),
                end=date(2025, 1, 29),
            ),
        )

        self.assertEqual(
            result.loc[0, "transaction_date"],
            "2025-01-29",
        )
        self.assertEqual(
            result.loc[0, "effective_date"],
            "2025-01-30",
        )


if __name__ == "__main__":
    unittest.main()
