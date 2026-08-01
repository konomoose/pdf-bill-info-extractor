from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from .statement_metadata import StatementMetadata
from .transaction_dates import (
    TransactionDateError,
    resolve_related_date,
    resolve_transaction_date,
)


class NormalizationError(ValueError):
    """Raised when extracted transactions cannot be normalized safely."""


NORMALIZED_COLUMNS = (
    "transaction_date",
    "posting_date",
    "effective_date",
    "description",
    "category",
    "amount",
    "withdrawal",
    "deposit",
    "payment",
    "interest_fees_insurance",
    "balance",
    "institution",
    "account_type",
    "profile_id",
    "source_file",
)


PROFILE_MAPPINGS = {
    "capital_one_mastercard_v1": {
        "transaction_date": "Transaction date",
        "posting_date": "Posting date",
        "description": "Description",
        "amount": "Amount",
    },
    "cibc_credit_card_v1": {
        "transaction_date": "Trans date",
        "posting_date": "Post date",
        "description": "Description",
        "category": "Spend Categories",
        "amount": "Amount($)",
    },
    "rbc_chequing_account_v1": {
        "transaction_date": "Date",
        "description": "Description",
        "withdrawal": "Withdrawals ($)",
        "deposit": "Deposits ($)",
        "balance": "Balance ($)",
    },
    "rbc_loc_v1": {
        "transaction_date": "Date",
        "description": "Description",
        "interest_fees_insurance": (
            "Interest/Fees/Insurance ($)"
        ),
        "withdrawal": "Withdrawals ($)",
        "payment": "Payments ($)",
        "balance": "Balance owing ($)",
    },
    "rbc_visa_credit_card_v1": {
        "transaction_date": "Transaction date",
        "posting_date": "Posting date",
        "description": "Activity description",
        "amount": "Amount($)",
    },
    "simplii_chequing_account_v1": {
        "transaction_date": "Trans. date",
        "effective_date": "Eff. date",
        "description": "Transaction",
        "withdrawal": "Funds out",
        "deposit": "Funds in",
        "balance": "Balance",
    },
    "simplii_loc_v1": {
        "transaction_date": "Trans. date",
        "effective_date": "Eff. date",
        "description": "Transaction",
        "withdrawal": "Funds out",
        "deposit": "Funds in",
        "balance": "Balance",
    },
    "simplii_visa_v1": {
        "transaction_date": "Trans date",
        "posting_date": "Post date",
        "description": "Description",
        "category": "Spend Categories",
        "amount": "Amount($)",
    },
    "td_visa_credit_card_v1": {
        "transaction_date": "Transaction date",
        "posting_date": "Posting date",
        "description": "Activity description",
        "amount": "Amount($)",
    },
    "triangle_mastercard_v1": {
        "transaction_date": "Transaction date",
        "posting_date": "Posting date",
        "description": "Activity description",
        "amount": "Amount($)",
    },
}


_SECONDARY_DATE_FIELDS = {
    "posting_date",
    "effective_date",
}

_MONEY_FIELDS = {
    "amount",
    "withdrawal",
    "deposit",
    "payment",
    "interest_fees_insurance",
    "balance",
}


def _is_blank(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _normalize_text(value: Any) -> str:
    if _is_blank(value):
        return ""

    return " ".join(str(value).split())


def _normalize_money(value: Any) -> str:
    if _is_blank(value):
        return ""

    if isinstance(value, float):
        raise NormalizationError(
            "Floating-point monetary values are not accepted."
        )

    cleaned = (
        str(value)
        .replace("$", "")
        .replace(",", "")
        .replace("*", "")
        .strip()
    )

    if cleaned.endswith("-"):
        cleaned = "-" + cleaned[:-1]

    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise NormalizationError(
            f"Invalid monetary value: {value!r}."
        ) from exc

    return format(amount, ".2f")


def _normalize_date(
    value: Any,
    metadata: StatementMetadata,
) -> str:
    if _is_blank(value):
        return ""

    try:
        resolved = resolve_transaction_date(
            str(value),
            metadata,
        )
    except TransactionDateError as exc:
        raise NormalizationError(str(exc)) from exc

    return resolved.isoformat()


def _normalize_related_date(
    value: Any,
    anchor_date: date,
) -> str:
    if _is_blank(value):
        return ""

    try:
        resolved = resolve_related_date(
            str(value),
            anchor_date,
        )
    except TransactionDateError as exc:
        raise NormalizationError(str(exc)) from exc

    return resolved.isoformat()


def normalize_transactions(
    transactions: pd.DataFrame,
    metadata: StatementMetadata,
) -> pd.DataFrame:
    """Convert a parser-specific table to the normalized CSV schema."""

    mapping = PROFILE_MAPPINGS.get(metadata.profile_id)

    if mapping is None:
        raise NormalizationError(
            f"No normalization mapping exists for profile "
            f"{metadata.profile_id!r}."
        )

    missing_columns = sorted(
        source_column
        for source_column in mapping.values()
        if source_column not in transactions.columns
    )

    if missing_columns:
        raise NormalizationError(
            "Extracted transactions are missing required source "
            f"columns: {', '.join(missing_columns)}."
        )

    rows: list[dict[str, str]] = []

    for row_number, (_, source_row) in enumerate(
        transactions.iterrows(),
        start=1,
    ):
        normalized = {
            column: ""
            for column in NORMALIZED_COLUMNS
        }

        transaction_source_column = mapping[
            "transaction_date"
        ]

        normalized["transaction_date"] = _normalize_date(
            source_row[transaction_source_column],
            metadata,
        )

        if not normalized["transaction_date"]:
            raise NormalizationError(
                f"Row {row_number} has no transaction date."
            )

        anchor_date = date.fromisoformat(
            normalized["transaction_date"]
        )

        for target_column, source_column in mapping.items():
            if target_column == "transaction_date":
                continue

            value = source_row[source_column]

            if target_column in _SECONDARY_DATE_FIELDS:
                normalized[target_column] = (
                    _normalize_related_date(
                        value,
                        anchor_date,
                    )
                )
            elif target_column in _MONEY_FIELDS:
                normalized[target_column] = _normalize_money(
                    value
                )
            else:
                normalized[target_column] = _normalize_text(
                    value
                )

        if not normalized["description"]:
            raise NormalizationError(
                f"Row {row_number} has no description."
            )

        normalized["institution"] = metadata.institution
        normalized["account_type"] = metadata.document_type
        normalized["profile_id"] = metadata.profile_id
        normalized["source_file"] = (
            metadata.source_file.as_posix()
        )

        rows.append(normalized)

    return pd.DataFrame(
        rows,
        columns=NORMALIZED_COLUMNS,
    )
