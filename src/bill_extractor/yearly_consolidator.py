from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from uuid import uuid4

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


def _managed_yearly_csv_paths(
    output_root: Path,
    institution_slug: str,
) -> set[Path]:
    """
    Find yearly CSVs managed by this institution slug.

    Unrelated CSVs and statement-level CSVs are not included.
    """
    name_pattern = re.compile(
        rf"^{re.escape(institution_slug)}_"
        r"(?P<year>\d{4})_transactions\.csv$"
    )

    managed: set[Path] = set()

    for csv_path in output_root.glob(
        f"????/{institution_slug}_"
        "????_transactions.csv"
    ):
        match = name_pattern.fullmatch(
            csv_path.name
        )

        if (
            match is None
            or match.group("year")
            != csv_path.parent.name
            or not csv_path.is_file()
        ):
            continue

        managed.add(csv_path)

    return managed


def _stage_yearly_csv(
    transactions: pd.DataFrame,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        ) as temporary_file:
            temporary_path = Path(
                temporary_file.name
            )

            transactions.to_csv(
                temporary_file,
                index=False,
                lineterminator="\n",
            )

        return temporary_path

    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )

        raise


def _commit_yearly_outputs(
    staged: dict[Path, Path],
    stale_paths: set[Path],
) -> None:
    """
    Replace all current yearly outputs as one recoverable operation.

    Existing target and stale files are first moved to backups.
    They are restored if any replacement fails.
    """
    affected_paths = (
        set(staged)
        | stale_paths
    )

    backups: dict[Path, Path] = {}
    committed: set[Path] = set()

    try:
        for target in sorted(
            affected_paths,
            key=lambda path: str(path).casefold(),
        ):
            if not target.exists():
                continue

            backup = target.with_name(
                f".{target.name}."
                f"{uuid4().hex}.bak"
            )

            target.replace(backup)
            backups[target] = backup

        for target in sorted(
            staged,
            key=lambda path: str(path).casefold(),
        ):
            staged[target].replace(target)
            committed.add(target)

    except Exception as exc:
        restoration_errors: list[str] = []

        # New files with no previous version must disappear.
        for target in committed:
            if target in backups:
                continue

            try:
                target.unlink(
                    missing_ok=True
                )
            except OSError as restore_exc:
                restoration_errors.append(
                    f"{target}: {restore_exc}"
                )

        # Existing and stale files return to their old paths.
        for target, backup in backups.items():
            if not backup.exists():
                continue

            try:
                backup.replace(target)
            except OSError as restore_exc:
                restoration_errors.append(
                    f"{target}: {restore_exc}"
                )

        if restoration_errors:
            preserved_backups = [
                str(backup)
                for backup in backups.values()
                if backup.exists()
            ]

            details = "; ".join(
                restoration_errors
            )

            backup_details = (
                ", ".join(preserved_backups)
                if preserved_backups
                else "none"
            )

            raise ConsolidationError(
                "Could not restore previous yearly "
                f"outputs: {details}. "
                "Preserved backup files: "
                f"{backup_details}."
            ) from exc

        raise

    else:
        # On success, deleting the backups also removes stale
        # yearly outputs that are no longer represented.
        for backup in backups.values():
            backup.unlink(
                missing_ok=True
            )

    finally:
        for temporary_path in staged.values():
            temporary_path.unlink(
                missing_ok=True
            )


def write_yearly_transaction_csvs(
    frames: Iterable[pd.DataFrame],
    *,
    output_root: str | Path,
    institution_slug: str,
) -> dict[int, Path]:
    """
    Write one consolidated CSV per transaction year.

    Existing managed yearly files are replaced atomically.
    Managed years no longer present in the input are removed.
    Unrelated files remain untouched.

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

    targets = {
        year: (
            root
            / str(year)
            / (
                f"{institution_slug}_{year}"
                "_transactions.csv"
            )
        )
        for year in yearly
    }

    staged: dict[Path, Path] = {}

    try:
        for year, target in targets.items():
            staged[target] = _stage_yearly_csv(
                yearly[year],
                target,
            )

    except Exception:
        for temporary_path in staged.values():
            temporary_path.unlink(
                missing_ok=True
            )

        raise

    existing_managed = (
        _managed_yearly_csv_paths(
            root,
            institution_slug,
        )
    )

    stale_paths = (
        existing_managed
        - set(targets.values())
    )

    _commit_yearly_outputs(
        staged,
        stale_paths,
    )

    return targets
