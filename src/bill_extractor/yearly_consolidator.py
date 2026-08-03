from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re

import pandas as pd

from .transaction_normalizer import NORMALIZED_COLUMNS


class ConsolidationError(ValueError):
    """Raised when normalized transactions cannot be consolidated safely."""


DATE_COLUMNS = (
    "transaction_date",
    "posting_date",
    "effective_date",
)

EXACT_DUPLICATE_COLUMNS = tuple(NORMALIZED_COLUMNS)

_INSTITUTION_SLUG_RE = re.compile(
    r"^[a-z0-9]+(?:_[a-z0-9]+)*$"
)


def _parse_date_column(
    frame: pd.DataFrame,
    column: str,
    *,
    allow_blank: bool,
) -> pd.Series:
    values = (
        frame[column]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    if not allow_blank and values.eq("").any():
        raise ConsolidationError(
            f"Normalized column {column!r} contains "
            "a blank value."
        )

    invalid_format = (
        values.ne("")
        & ~values.str.fullmatch(
            r"\d{4}-\d{2}-\d{2}"
        )
    )

    if invalid_format.any():
        invalid_value = values[invalid_format].iloc[0]
        raise ConsolidationError(
            f"Normalized column {column!r} contains "
            f"an invalid ISO date: {invalid_value!r}."
        )

    try:
        return pd.to_datetime(
            values.where(values.ne("")),
            format="%Y-%m-%d",
            errors="raise",
        )
    except (TypeError, ValueError) as exc:
        raise ConsolidationError(
            f"Normalized column {column!r} contains "
            "an invalid calendar date."
        ) from exc


def _prepare_normalized_frame(
    frame: pd.DataFrame,
    *,
    frame_number: int,
    starting_sequence: int,
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise ConsolidationError(
            f"Input {frame_number} is not a pandas DataFrame."
        )

    missing_columns = [
        column
        for column in NORMALIZED_COLUMNS
        if column not in frame.columns
    ]

    if missing_columns:
        raise ConsolidationError(
            "Normalized input is missing required columns: "
            + ", ".join(missing_columns)
            + "."
        )

    prepared = frame.loc[
        :,
        list(NORMALIZED_COLUMNS),
    ].copy()

    if prepared.empty:
        return prepared

    prepared["_transaction_sort"] = _parse_date_column(
        prepared,
        "transaction_date",
        allow_blank=False,
    )
    prepared["_posting_sort"] = _parse_date_column(
        prepared,
        "posting_date",
        allow_blank=True,
    )
    prepared["_effective_sort"] = _parse_date_column(
        prepared,
        "effective_date",
        allow_blank=True,
    )

    prepared["_sequence"] = range(
        starting_sequence,
        starting_sequence + len(prepared),
    )

    return prepared


def consolidate_normalized_transactions(
    frames: Iterable[pd.DataFrame],
) -> dict[int, pd.DataFrame]:
    """
    Consolidate normalized transactions into calendar-year tables.

    Transactions are assigned according to transaction_date, not the
    statement folder or statement end date. Only exact duplicates across
    all normalized columns are removed. The source_file column therefore
    protects legitimate matching transactions from different statements.
    """

    prepared_frames: list[pd.DataFrame] = []
    next_sequence = 0

    for frame_number, frame in enumerate(
        frames,
        start=1,
    ):
        prepared = _prepare_normalized_frame(
            frame,
            frame_number=frame_number,
            starting_sequence=next_sequence,
        )

        next_sequence += len(prepared)

        if not prepared.empty:
            prepared_frames.append(prepared)

    if not prepared_frames:
        return {}

    combined = pd.concat(
        prepared_frames,
        ignore_index=True,
    )

    combined = combined.drop_duplicates(
        subset=list(EXACT_DUPLICATE_COLUMNS),
        keep="first",
    )

    combined["_year"] = (
        combined["_transaction_sort"]
        .dt.year
        .astype(int)
    )

    yearly: dict[int, pd.DataFrame] = {}

    for year in sorted(combined["_year"].unique()):
        year_frame = combined.loc[
            combined["_year"].eq(year)
        ].copy()

        year_frame = year_frame.sort_values(
            by=[
                "_transaction_sort",
                "_posting_sort",
                "_effective_sort",
                "source_file",
                "_sequence",
            ],
            kind="mergesort",
            na_position="last",
        )

        yearly[int(year)] = (
            year_frame.loc[
                :,
                list(NORMALIZED_COLUMNS),
            ]
            .reset_index(drop=True)
        )

    return yearly


def write_yearly_transaction_csvs(
    frames: Iterable[pd.DataFrame],
    *,
    output_root: str | Path,
    institution_slug: str,
) -> dict[int, Path]:
    """
    Write one consolidated CSV per transaction year.

    Example:
        csv_output/cibc/2025/cibc_2025_transactions.csv
    """

    if not _INSTITUTION_SLUG_RE.fullmatch(
        institution_slug
    ):
        raise ConsolidationError(
            "Institution slug must contain only lowercase "
            "letters, numbers, and single underscores."
        )

    root = Path(output_root)
    yearly = consolidate_normalized_transactions(
        frames
    )
    written: dict[int, Path] = {}

    for year, transactions in yearly.items():
        year_folder = root / str(year)
        year_folder.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path = (
            year_folder
            / (
                f"{institution_slug}_{year}"
                "_transactions.csv"
            )
        )

        transactions.to_csv(
            output_path,
            index=False,
            lineterminator="\n",
        )

        written[year] = output_path

    return written
