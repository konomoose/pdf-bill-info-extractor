from __future__ import annotations

from datetime import date
import re

from .statement_metadata import StatementMetadata


class TransactionDateError(ValueError):
    """Raised when a transaction date cannot be resolved safely."""


_MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_MONTH_FIRST_RE = re.compile(
    r"^([A-Za-z]+)\s+(\d{1,2})$",
)
_DAY_FIRST_RE = re.compile(
    r"^(\d{1,2})\s+([A-Za-z]+)$",
)
_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}$",
)


def _clean_date_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransactionDateError(
            "Transaction date must be a non-empty string."
        )

    return " ".join(
        value.replace(",", " ").split()
    )


def _parse_partial_month_day(
    value: str,
) -> tuple[int, int]:
    month_first = _MONTH_FIRST_RE.fullmatch(value)
    day_first = _DAY_FIRST_RE.fullmatch(value)

    if month_first is not None:
        month_text = month_first.group(1)
        day_text = month_first.group(2)
    elif day_first is not None:
        day_text = day_first.group(1)
        month_text = day_first.group(2)
    else:
        raise TransactionDateError(
            f"Unsupported transaction date format: {value!r}."
        )

    month = _MONTH_NUMBERS.get(month_text.lower())

    if month is None:
        raise TransactionDateError(
            f"Unknown month in transaction date: {value!r}."
        )

    day = int(day_text)

    if not 1 <= day <= 31:
        raise TransactionDateError(
            f"Invalid day in transaction date: {value!r}."
        )

    return month, day


def _validate_against_period(
    resolved: date,
    metadata: StatementMetadata,
) -> date:
    if (
        metadata.statement_start_date is not None
        and resolved < metadata.statement_start_date
    ):
        raise TransactionDateError(
            f"Transaction date {resolved.isoformat()} is before "
            "the statement period."
        )

    if (
        metadata.statement_end_date is not None
        and resolved > metadata.statement_end_date
    ):
        raise TransactionDateError(
            f"Transaction date {resolved.isoformat()} is after "
            "the statement period."
        )

    return resolved


def resolve_transaction_date(
    value: str,
    metadata: StatementMetadata,
) -> date:
    """
    Resolve a parser-produced transaction date to a full date.

    Full ISO dates are accepted directly. Partial dates require both
    statement-period boundaries so the year is never guessed.
    """

    cleaned = _clean_date_text(value)

    if _ISO_DATE_RE.fullmatch(cleaned):
        try:
            resolved = date.fromisoformat(cleaned)
        except ValueError as exc:
            raise TransactionDateError(
                f"Invalid ISO transaction date: {cleaned!r}."
            ) from exc

        return _validate_against_period(
            resolved,
            metadata,
        )

    if (
        metadata.statement_start_date is None
        or metadata.statement_end_date is None
    ):
        raise TransactionDateError(
            "A complete statement period is required to resolve "
            f"partial transaction date {cleaned!r}."
        )

    month, day = _parse_partial_month_day(cleaned)
    candidates: list[date] = []

    for year in range(
        metadata.statement_start_date.year,
        metadata.statement_end_date.year + 1,
    ):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue

        if (
            metadata.statement_start_date
            <= candidate
            <= metadata.statement_end_date
        ):
            candidates.append(candidate)

    if not candidates:
        raise TransactionDateError(
            f"Transaction date {cleaned!r} does not fall within "
            "the statement period."
        )

    if len(candidates) > 1:
        raise TransactionDateError(
            f"Transaction date {cleaned!r} is ambiguous within "
            "the statement period."
        )

    return candidates[0]


def resolve_related_date(
    value: str,
    anchor_date: date,
    *,
    max_distance_days: int = 45,
) -> date:
    """
    Resolve a posting or effective date relative to its transaction date.

    Related dates may fall just outside the statement period. The closest
    valid date within max_distance_days is selected without guessing a year.
    """

    if max_distance_days < 0:
        raise TransactionDateError(
            "max_distance_days cannot be negative."
        )

    cleaned = _clean_date_text(value)

    if _ISO_DATE_RE.fullmatch(cleaned):
        try:
            resolved = date.fromisoformat(cleaned)
        except ValueError as exc:
            raise TransactionDateError(
                f"Invalid ISO related date: {cleaned!r}."
            ) from exc

        if abs((resolved - anchor_date).days) > max_distance_days:
            raise TransactionDateError(
                f"Related date {resolved.isoformat()} is too far from "
                f"transaction date {anchor_date.isoformat()}."
            )

        return resolved

    month, day = _parse_partial_month_day(cleaned)
    candidates: list[date] = []

    for year in range(
        anchor_date.year - 1,
        anchor_date.year + 2,
    ):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue

        if abs((candidate - anchor_date).days) <= max_distance_days:
            candidates.append(candidate)

    if not candidates:
        raise TransactionDateError(
            f"Related date {cleaned!r} cannot be resolved within "
            f"{max_distance_days} days of "
            f"{anchor_date.isoformat()}."
        )

    nearest_distance = min(
        abs((candidate - anchor_date).days)
        for candidate in candidates
    )

    nearest = [
        candidate
        for candidate in candidates
        if abs((candidate - anchor_date).days)
        == nearest_distance
    ]

    if len(nearest) != 1:
        raise TransactionDateError(
            f"Related date {cleaned!r} is ambiguous relative to "
            f"{anchor_date.isoformat()}."
        )

    return nearest[0]
