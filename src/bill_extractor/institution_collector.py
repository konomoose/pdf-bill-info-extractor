from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from uuid import uuid4

import pandas as pd

from .profile_loader import (
    PROJECT_ROOT,
    ExtractionProfile,
    discover_profiles,
)
from .transaction_normalizer import NORMALIZED_COLUMNS


class InstitutionCollectionError(RuntimeError):
    """Raised when institution CSV collection cannot be completed safely."""


@dataclass(frozen=True)
class InstitutionCollectionResult:
    institution: str
    institution_slug: str
    collection_root: Path
    all_statements_folder: Path
    combined_csv_path: Path
    manifest_path: Path
    profile_ids: tuple[str, ...]
    collected_count: int
    combined_transaction_count: int
    stale_removed_count: int


@dataclass(frozen=True)
class AccountCollectionResult:
    profile_id: str
    profile_display_name: str
    account_slug: str
    output_folder: Path
    combined_csv_path: Path
    manifest_path: Path
    collected_count: int
    combined_transaction_count: int
    duplicate_statement_copies_skipped: int = 0


@dataclass(frozen=True)
class _CollectionEntry:
    institution: str
    profile_id: str
    profile_display_name: str
    account_type: str
    normalized_output_columns: tuple[str, ...] | None
    source_path: Path
    source_relative_path: str
    destination_name: str
    destination_path: Path


_NORMALIZED_SUFFIX = "_normalized_transactions.csv"
_MANIFEST_NAME = ".collection_manifest.json"
_ACCOUNT_MANIFEST_NAME = ".account_collection_manifest.json"
_ALL_STATEMENTS_FOLDER = "all_statements"
_MANIFEST_VERSION = 1
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_UNDERSCORE_RE = re.compile(r"_+")
_COMBINED_COLUMNS = (
    "institution",
    "profile_id",
    "profile_display_name",
    "account_type",
    "source_statement",
    "source_relative_path",
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
)


def _normalized_institution(value: str) -> str:
    return " ".join(value.split()).casefold()


def _display_institution(value: str) -> str:
    return " ".join(value.split())


def institution_slug(institution: str) -> str:
    slug = re.sub(
        r"[^a-z0-9]+",
        "_",
        _display_institution(institution).casefold(),
    ).strip("_")

    if not slug:
        raise InstitutionCollectionError(
            "Institution name cannot produce a usable collection slug."
        )

    return slug


def account_slug(profile: ExtractionProfile) -> str:
    output_name = profile.resolve_output_folder().name
    slug = re.sub(
        r"[^a-z0-9]+",
        "_",
        output_name.casefold(),
    ).strip("_")

    if not slug:
        slug = re.sub(
            r"[^a-z0-9]+",
            "_",
            profile.profile_id.casefold(),
        ).strip("_")

    if not slug:
        raise InstitutionCollectionError(
            "Profile cannot produce a usable account collection slug."
        )

    return slug


def profiles_for_institution(
    profiles: Iterable[ExtractionProfile],
    institution: str,
) -> tuple[ExtractionProfile, ...]:
    target = _normalized_institution(institution)
    matches = [
        profile
        for profile in profiles
        if _normalized_institution(profile.institution) == target
    ]

    if not matches:
        raise InstitutionCollectionError(
            "No configured profiles were found for institution "
            f"{_display_institution(institution)!r}."
        )

    return tuple(
        sorted(
            matches,
            key=lambda profile: profile.profile_id.casefold(),
        )
    )


def default_collection_base() -> Path:
    return PROJECT_ROOT / "csv_output" / "institution_collections"


def _resolve_path(path: str | Path) -> Path:
    return Path(path).resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False

    return True


def _validate_collection_base(
    collection_base: Path,
    profiles: Sequence[ExtractionProfile],
) -> None:
    resolved_base = collection_base.resolve()

    for profile in profiles:
        output_root = profile.resolve_output_folder().resolve()

        if (
            resolved_base == output_root
            or _is_relative_to(resolved_base, output_root)
            or _is_relative_to(output_root, resolved_base)
        ):
            raise InstitutionCollectionError(
                "Collection base must be separate from profile "
                "output roots: "
                f"{resolved_base} conflicts with {output_root}."
            )


def _safe_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", value.strip())
    cleaned = _UNDERSCORE_RE.sub("_", cleaned).strip("._-")
    return cleaned or "statement"


def _statement_stem(csv_path: Path) -> str:
    if not csv_path.name.endswith(_NORMALIZED_SUFFIX):
        raise InstitutionCollectionError(
            "Normalized statement CSV has an unexpected name: "
            f"{csv_path}"
        )

    return csv_path.name[: -len(_NORMALIZED_SUFFIX)]


def _destination_name(
    profile: ExtractionProfile,
    output_root: Path,
    source_path: Path,
) -> tuple[str, str]:
    relative_path = source_path.relative_to(output_root).as_posix()
    relative_parent = source_path.parent.relative_to(output_root)
    parent_parts = [
        _safe_name(part)
        for part in relative_parent.parts
        if part not in ("", ".")
    ]

    source_identity = "\n".join(
        (
            profile.profile_id,
            output_root.resolve().as_posix(),
            relative_path,
        )
    )
    short_hash = sha256(
        source_identity.encode("utf-8")
    ).hexdigest()[:12]

    parts = [
        _safe_name(profile.profile_id),
        *parent_parts,
        _safe_name(_statement_stem(source_path)),
        short_hash,
    ]

    return (
        "__".join(parts) + _NORMALIZED_SUFFIX,
        relative_path,
    )


def _normalized_statement_paths(output_root: Path) -> list[Path]:
    if not output_root.is_dir():
        return []

    return sorted(
        (
            path
            for path in output_root.rglob(
                f"*{_NORMALIZED_SUFFIX}"
            )
            if path.is_file()
        ),
        key=lambda path: str(path).casefold(),
    )


def _collection_entries(
    profiles: Sequence[ExtractionProfile],
    all_statements_folder: Path,
) -> tuple[_CollectionEntry, ...]:
    entries: list[_CollectionEntry] = []
    destinations: dict[str, Path] = {}

    for profile in profiles:
        output_root = profile.resolve_output_folder().resolve()

        for source_path in _normalized_statement_paths(output_root):
            (
                destination_name,
                source_relative_path,
            ) = _destination_name(
                profile,
                output_root,
                source_path,
            )

            existing = destinations.get(
                destination_name.casefold()
            )
            if (
                existing is not None
                and existing.resolve()
                != source_path.resolve()
            ):
                raise InstitutionCollectionError(
                    "Two normalized statement CSVs would use "
                    f"the same collected filename: {destination_name}."
                )

            destinations[destination_name.casefold()] = source_path
            entries.append(
                _CollectionEntry(
                    institution=profile.institution,
                    profile_id=profile.profile_id,
                    profile_display_name=(
                        profile.display_name
                    ),
                    account_type=profile.document_type,
                    normalized_output_columns=(
                        profile.normalized_output_columns
                    ),
                    source_path=source_path,
                    source_relative_path=source_relative_path,
                    destination_name=destination_name,
                    destination_path=(
                        all_statements_folder
                        / destination_name
                    ),
                )
            )

    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                entry.destination_name.casefold(),
                str(entry.source_path).casefold(),
            ),
        )
    )


def _manifest_entry(
    entry: _CollectionEntry,
    collection_root: Path,
) -> dict[str, str]:
    return {
        "destination_path": (
            entry.destination_path
            .relative_to(collection_root)
            .as_posix()
        ),
        "profile_id": entry.profile_id,
        "profile_display_name": entry.profile_display_name,
        "source_relative_path": entry.source_relative_path,
    }


def _manifest_payload(
    *,
    institution: str,
    slug: str,
    collection_root: Path,
    combined_csv_path: Path,
    combined_transaction_count: int,
    entries: Sequence[_CollectionEntry],
) -> dict[str, object]:
    return {
        "all_statements_folder": _ALL_STATEMENTS_FOLDER,
        "managed_artifacts": [
            {
                "kind": "combined_transactions",
                "path": (
                    combined_csv_path
                    .relative_to(collection_root)
                    .as_posix()
                ),
                "transaction_count": combined_transaction_count,
            }
        ],
        "entries": [
            _manifest_entry(
                entry,
                collection_root,
            )
            for entry in entries
        ],
        "institution": institution,
        "institution_key": _normalized_institution(institution),
        "institution_slug": slug,
        "version": _MANIFEST_VERSION,
    }


def _write_manifest_temp(
    manifest_path: Path,
    payload: dict[str, object],
) -> Path:
    manifest_path.parent.mkdir(
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
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
        ) as temporary_file:
            temporary_path = Path(
                temporary_file.name
            )
            temporary_file.write(
                json.dumps(
                    payload,
                    indent=2,
                    sort_keys=True,
                )
            )
            temporary_file.write("\n")

        return temporary_path

    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )

        raise


def _copy_temp(
    source_path: Path,
    destination_path: Path,
) -> Path:
    destination_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
        ) as temporary_file:
            temporary_path = Path(
                temporary_file.name
            )

            with source_path.open("rb") as source_file:
                shutil.copyfileobj(
                    source_file,
                    temporary_file,
                )

        return temporary_path

    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )

        raise


def _read_normalized_source(
    entry: _CollectionEntry,
) -> pd.DataFrame:
    try:
        frame = pd.read_csv(
            entry.source_path,
            dtype=str,
            keep_default_na=False,
        )
    except (
        OSError,
        UnicodeDecodeError,
        pd.errors.EmptyDataError,
        pd.errors.ParserError,
    ) as exc:
        raise InstitutionCollectionError(
            "Could not read normalized source CSV for "
            f"{entry.profile_id}: {entry.source_relative_path}."
        ) from exc

    if tuple(frame.columns) == tuple(NORMALIZED_COLUMNS):
        prepared = frame.loc[
            :,
            list(NORMALIZED_COLUMNS),
        ].copy()
    elif (
        entry.normalized_output_columns is not None
        and tuple(frame.columns) == entry.normalized_output_columns
    ):
        prepared = pd.DataFrame(
            "",
            index=frame.index,
            columns=list(NORMALIZED_COLUMNS),
        )

        for column in frame.columns:
            prepared[column] = frame[column]

        prepared["institution"] = entry.institution
        prepared["account_type"] = entry.account_type
        prepared["profile_id"] = entry.profile_id
        prepared["source_file"] = entry.source_relative_path
    else:
        raise InstitutionCollectionError(
            "Normalized source CSV has an unexpected schema for "
            f"{entry.profile_id}: {entry.source_relative_path}."
        )

    prepared.insert(
        2,
        "_row_sequence",
        range(len(prepared)),
    )
    prepared.insert(
        0,
        "source_relative_path",
        entry.source_relative_path,
    )
    prepared.insert(
        0,
        "source_statement",
        f"{entry.profile_id}::{entry.source_relative_path}",
    )
    prepared.insert(
        0,
        "profile_display_name",
        entry.profile_display_name,
    )

    return prepared


def _validate_combined_dates(
    frame: pd.DataFrame,
) -> None:
    for column, allow_blank in (
        ("transaction_date", False),
        ("posting_date", True),
        ("effective_date", True),
    ):
        values = (
            frame[column]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        if not allow_blank and values.eq("").any():
            raise InstitutionCollectionError(
                "Normalized source CSV contains a blank "
                f"{column} value."
            )

        invalid_format = (
            values.ne("")
            & ~values.str.fullmatch(
                r"\d{4}-\d{2}-\d{2}"
            )
        )

        if invalid_format.any():
            raise InstitutionCollectionError(
                "Normalized source CSV contains an invalid "
                f"{column} value."
            )

        try:
            pd.to_datetime(
                values.where(values.ne("")),
                format="%Y-%m-%d",
                errors="raise",
            )
        except (TypeError, ValueError) as exc:
            raise InstitutionCollectionError(
                "Normalized source CSV contains an invalid "
                f"{column} calendar date."
            ) from exc


def _combined_transactions_frame(
    entries: Sequence[_CollectionEntry],
) -> pd.DataFrame:
    frames = [
        frame
        for frame in (
            _read_normalized_source(entry)
            for entry in entries
        )
        if not frame.empty
    ]

    if not frames:
        return pd.DataFrame(
            columns=list(_COMBINED_COLUMNS),
        )

    combined = pd.concat(
        frames,
        ignore_index=True,
    )

    _validate_combined_dates(combined)

    combined = combined.sort_values(
        by=[
            "transaction_date",
            "posting_date",
            "effective_date",
            "institution",
            "profile_id",
            "source_statement",
            "source_relative_path",
            "_row_sequence",
        ],
        kind="mergesort",
    )

    return (
        combined.loc[
            :,
            list(_COMBINED_COLUMNS),
        ]
        .reset_index(drop=True)
    )


def _file_digest(
    path: Path,
) -> str:
    digest = sha256()

    try:
        with path.open("rb") as source_file:
            for chunk in iter(
                lambda: source_file.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)
    except OSError as exc:
        raise InstitutionCollectionError(
            "Could not read normalized source CSV for duplicate detection: "
            f"{path.name}."
        ) from exc

    return digest.hexdigest()


def _deduplicated_account_source_paths(
    source_paths: Sequence[Path],
    output_root: Path,
) -> tuple[tuple[Path, ...], int]:
    resolved_sources = tuple(
        path.resolve()
        for path in source_paths
    )
    by_name: dict[str, list[Path]] = {}

    for source_path in resolved_sources:
        by_name.setdefault(
            source_path.name.casefold(),
            [],
        ).append(source_path)

    selected: list[Path] = []
    duplicate_skipped = 0

    for filename_key in sorted(by_name):
        candidates = sorted(
            by_name[filename_key],
            key=lambda path: (
                path.relative_to(output_root).as_posix().casefold(),
                path.relative_to(output_root).as_posix(),
            ),
        )

        if len(candidates) == 1:
            selected.append(candidates[0])
            continue

        digests = {
            _file_digest(candidate)
            for candidate in candidates
        }

        if len(digests) != 1:
            relative_paths = ", ".join(
                candidate.relative_to(output_root).as_posix()
                for candidate in candidates
            )
            raise InstitutionCollectionError(
                "Multiple normalized statement CSV copies share the same "
                "filename but have different contents: "
                f"{relative_paths}."
            )

        selected.append(candidates[0])
        duplicate_skipped += len(candidates) - 1

    return (
        tuple(
            sorted(
                selected,
                key=lambda path: (
                    path.relative_to(output_root).as_posix().casefold(),
                    path.relative_to(output_root).as_posix(),
                ),
            )
        ),
        duplicate_skipped,
    )


def _account_entries(
    profile: ExtractionProfile,
) -> tuple[tuple[_CollectionEntry, ...], int]:
    output_root = profile.resolve_output_folder().resolve()
    source_paths = _deduplicated_account_source_paths(
        _normalized_statement_paths(output_root),
        output_root,
    )
    entries = [
        _CollectionEntry(
            institution=profile.institution,
            profile_id=profile.profile_id,
            profile_display_name=profile.display_name,
            account_type=profile.document_type,
            normalized_output_columns=profile.normalized_output_columns,
            source_path=source_path,
            source_relative_path=source_path.relative_to(
                output_root
            ).as_posix(),
            destination_name=source_path.name,
            destination_path=source_path,
        )
        for source_path in source_paths[0]
    ]

    return (
        tuple(
            sorted(
                entries,
                key=lambda entry: (
                    entry.source_relative_path.casefold(),
                    str(entry.source_path).casefold(),
                ),
            )
        ),
        source_paths[1],
    )


def _account_combined_transactions_frame(
    profile: ExtractionProfile,
    entries: Sequence[_CollectionEntry],
) -> pd.DataFrame:
    frames = [
        frame
        for frame in (
            _read_normalized_source(entry)
            for entry in entries
        )
        if not frame.empty
    ]
    output_columns = list(
        profile.normalized_output_columns
        if profile.normalized_output_columns is not None
        else NORMALIZED_COLUMNS
    )

    if not frames:
        return pd.DataFrame(
            columns=output_columns,
        )

    combined = pd.concat(
        frames,
        ignore_index=True,
    )

    _validate_combined_dates(combined)

    combined = combined.sort_values(
        by=[
            "transaction_date",
            "posting_date",
            "effective_date",
            "source_relative_path",
            "_row_sequence",
        ],
        kind="mergesort",
    )

    return (
        combined.loc[
            :,
            output_columns,
        ]
        .reset_index(drop=True)
    )


def _account_manifest_payload(
    *,
    profile: ExtractionProfile,
    slug: str,
    collection_root: Path,
    combined_csv_path: Path,
    combined_transaction_count: int,
    duplicate_statement_copies_skipped: int,
    entries: Sequence[_CollectionEntry],
) -> dict[str, object]:
    return {
        "account_slug": slug,
        "duplicate_statement_copies_skipped": (
            duplicate_statement_copies_skipped
        ),
        "entries": [],
        "managed_artifacts": [
            {
                "kind": "combined_transactions",
                "path": (
                    combined_csv_path
                    .relative_to(collection_root)
                    .as_posix()
                ),
                "transaction_count": combined_transaction_count,
            }
        ],
        "profile_display_name": profile.display_name,
        "profile_id": profile.profile_id,
        "source_statements": [
            {
                "source_relative_path": entry.source_relative_path,
            }
            for entry in entries
        ],
        "version": _MANIFEST_VERSION,
    }


def _write_combined_temp(
    transactions: pd.DataFrame,
    combined_csv_path: Path,
) -> Path:
    combined_csv_path.parent.mkdir(
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
            dir=combined_csv_path.parent,
            prefix=f".{combined_csv_path.name}.",
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


def _manifest_destination_path(
    collection_root: Path,
    raw_value: object,
    *,
    require_all_statements: bool = False,
) -> Path:
    if not isinstance(raw_value, str) or not raw_value:
        raise InstitutionCollectionError(
            "Collection manifest contains an invalid destination path."
        )

    relative_path = Path(raw_value)

    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise InstitutionCollectionError(
            "Collection manifest contains an unsafe destination path."
        )

    if require_all_statements and (
        len(relative_path.parts) < 2
        or relative_path.parts[0]
        != _ALL_STATEMENTS_FOLDER
    ):
        raise InstitutionCollectionError(
            "Collection manifest destination paths must be inside "
            f"{_ALL_STATEMENTS_FOLDER}/."
        )

    return (collection_root / relative_path).resolve()


def _load_previous_manifest(
    manifest_path: Path,
    *,
    expected_combined_artifact_path: Path,
) -> dict[str, Path]:
    if not manifest_path.exists():
        return {}

    collection_root = manifest_path.parent.resolve()
    try:
        expected_combined_relative = expected_combined_artifact_path.relative_to(
            collection_root
        ).as_posix()
    except ValueError as exc:
        raise InstitutionCollectionError(
            "Collection manifest is malformed; no files were changed."
        ) from exc

    try:
        with manifest_path.open(
            "r",
            encoding="utf-8",
        ) as manifest_file:
            payload = json.load(manifest_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise InstitutionCollectionError(
            "Collection manifest is malformed; no files were changed."
        ) from exc

    if (
        not isinstance(payload, dict)
        or payload.get("version") != _MANIFEST_VERSION
        or not isinstance(payload.get("entries"), list)
    ):
        raise InstitutionCollectionError(
            "Collection manifest is malformed; no files were changed."
        )

    by_destination: dict[str, Path] = {}

    for item in payload["entries"]:
        if not isinstance(item, dict):
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )

        source_relative_value = item.get(
            "source_relative_path"
        )
        if (
            not isinstance(source_relative_value, str)
            or not source_relative_value
        ):
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )

        destination = _manifest_destination_path(
            collection_root,
            item.get("destination_path"),
            require_all_statements=True,
        )

        by_destination[
            destination.as_posix().casefold()
        ] = destination

    artifacts = payload.get("managed_artifacts", [])
    if not isinstance(artifacts, list):
        raise InstitutionCollectionError(
            "Collection manifest is malformed; no files were changed."
        )

    seen_combined_artifact = False
    for item in artifacts:
        if not isinstance(item, dict):
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )
        if item.get("kind") != "combined_transactions":
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )
        if item.get("path") != expected_combined_relative:
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )
        if seen_combined_artifact:
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )
        seen_combined_artifact = True

        destination = _manifest_destination_path(
            collection_root,
            item.get("path"),
        )
        if destination != expected_combined_artifact_path.resolve():
            raise InstitutionCollectionError(
                "Collection manifest is malformed; no files were changed."
            )
        by_destination[
            destination.as_posix().casefold()
        ] = destination

    return by_destination


def _protect_unmanaged_destinations(
    entries: Sequence[_CollectionEntry],
    previous_by_destination: dict[str, Path],
    extra_targets: Sequence[Path] = (),
) -> None:
    managed_destinations = set(previous_by_destination)

    targets = [
        entry.destination_path
        for entry in entries
    ] + list(extra_targets)

    for target in targets:
        key = target.resolve().as_posix().casefold()

        if (
            target.exists()
            and key not in managed_destinations
        ):
            raise InstitutionCollectionError(
                "Refusing to overwrite unmanaged collection file: "
                f"{target}"
            )


def _stage_collection(
    entries: Sequence[_CollectionEntry],
    combined_csv_path: Path,
    combined_transactions: pd.DataFrame,
    manifest_path: Path,
    manifest_payload: dict[str, object],
) -> tuple[dict[Path, Path], Path]:
    staged: dict[Path, Path] = {}

    try:
        for entry in entries:
            staged[entry.destination_path] = _copy_temp(
                entry.source_path,
                entry.destination_path,
            )

        staged[combined_csv_path] = _write_combined_temp(
            combined_transactions,
            combined_csv_path,
        )

        manifest_temp = _write_manifest_temp(
            manifest_path,
            manifest_payload,
        )

    except Exception:
        for temporary_path in staged.values():
            temporary_path.unlink(
                missing_ok=True
            )

        raise

    return staged, manifest_temp


def _commit_collection(
    *,
    staged: dict[Path, Path],
    manifest_path: Path,
    manifest_temp: Path,
    stale_paths: set[Path],
) -> None:
    affected_paths = set(staged) | stale_paths | {manifest_path}
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
                f".{target.name}.{uuid4().hex}.bak"
            )
            target.replace(backup)
            backups[target] = backup

        for target in sorted(
            staged,
            key=lambda path: str(path).casefold(),
        ):
            staged[target].replace(target)
            committed.add(target)

        manifest_temp.replace(manifest_path)
        committed.add(manifest_path)

    except Exception as exc:
        restoration_errors: list[str] = []

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
            raise InstitutionCollectionError(
                "Could not restore previous institution collection "
                "after refresh failed: "
                + "; ".join(restoration_errors)
            ) from exc

        raise InstitutionCollectionError(
            "Could not refresh institution collection: "
            f"{exc}"
        ) from exc

    else:
        cleanup_errors: list[str] = []

        for backup in backups.values():
            try:
                backup.unlink(
                    missing_ok=True
                )
            except OSError as exc:
                cleanup_errors.append(
                    f"{backup}: {exc}"
                )

        if cleanup_errors:
            raise InstitutionCollectionError(
                "Institution collection was refreshed, but backup "
                "files could not be deleted: "
                + "; ".join(cleanup_errors)
            )

    finally:
        for temporary_path in staged.values():
            temporary_path.unlink(
                missing_ok=True
            )

        manifest_temp.unlink(
            missing_ok=True
        )


def collect_institution_statement_csvs(
    profiles: Iterable[ExtractionProfile],
    institution: str,
    *,
    collection_base: str | Path | None = None,
) -> InstitutionCollectionResult:
    selected_profiles = profiles_for_institution(
        tuple(profiles),
        institution,
    )
    display_name = _display_institution(
        selected_profiles[0].institution
    )
    slug = institution_slug(display_name)
    base = _resolve_path(
        collection_base
        if collection_base is not None
        else default_collection_base()
    )

    _validate_collection_base(
        base,
        selected_profiles,
    )

    collection_root = (base / slug).resolve()
    all_statements_folder = (
        collection_root
        / _ALL_STATEMENTS_FOLDER
    )
    combined_csv_path = (
        collection_root
        / f"{slug}_all_transactions.csv"
    )
    manifest_path = (
        collection_root
        / _MANIFEST_NAME
    )

    previous_by_destination = _load_previous_manifest(
        manifest_path,
        expected_combined_artifact_path=combined_csv_path,
    )

    entries = _collection_entries(
        selected_profiles,
        all_statements_folder,
    )

    combined_transactions = _combined_transactions_frame(
        entries
    )

    _protect_unmanaged_destinations(
        entries,
        previous_by_destination,
        extra_targets=(combined_csv_path,),
    )

    current_destinations = {
        entry.destination_path.resolve().as_posix().casefold()
        for entry in entries
    }
    current_destinations.add(
        combined_csv_path.resolve().as_posix().casefold()
    )
    stale_paths = {
        destination
        for key, destination in previous_by_destination.items()
        if key not in current_destinations
        and destination.exists()
    }

    manifest_payload = _manifest_payload(
        institution=display_name,
        slug=slug,
        collection_root=collection_root,
        combined_csv_path=combined_csv_path,
        combined_transaction_count=len(combined_transactions),
        entries=entries,
    )
    staged, manifest_temp = _stage_collection(
        entries,
        combined_csv_path,
        combined_transactions,
        manifest_path,
        manifest_payload,
    )

    _commit_collection(
        staged=staged,
        manifest_path=manifest_path,
        manifest_temp=manifest_temp,
        stale_paths=stale_paths,
    )

    return InstitutionCollectionResult(
        institution=display_name,
        institution_slug=slug,
        collection_root=collection_root,
        all_statements_folder=all_statements_folder,
        combined_csv_path=combined_csv_path,
        manifest_path=manifest_path,
        profile_ids=tuple(
            profile.profile_id
            for profile in selected_profiles
        ),
        collected_count=len(entries),
        combined_transaction_count=len(combined_transactions),
        stale_removed_count=len(stale_paths),
    )


def collect_profile_account_statement_csvs(
    selected_profile: ExtractionProfile,
) -> AccountCollectionResult:
    output_folder = selected_profile.resolve_output_folder().resolve()
    slug = account_slug(selected_profile)
    combined_csv_path = (
        output_folder
        / f"{slug}_all_transactions.csv"
    )
    manifest_path = (
        output_folder
        / _ACCOUNT_MANIFEST_NAME
    )

    previous_by_destination = _load_previous_manifest(
        manifest_path,
        expected_combined_artifact_path=combined_csv_path,
    )

    entries, duplicate_statement_copies_skipped = _account_entries(
        selected_profile
    )
    combined_transactions = _account_combined_transactions_frame(
        selected_profile,
        entries,
    )

    _protect_unmanaged_destinations(
        (),
        previous_by_destination,
        extra_targets=(combined_csv_path,),
    )

    manifest_payload = _account_manifest_payload(
        profile=selected_profile,
        slug=slug,
        collection_root=output_folder,
        combined_csv_path=combined_csv_path,
        combined_transaction_count=len(combined_transactions),
        duplicate_statement_copies_skipped=(
            duplicate_statement_copies_skipped
        ),
        entries=entries,
    )
    staged = {
        combined_csv_path: _write_combined_temp(
            combined_transactions,
            combined_csv_path,
        )
    }

    try:
        manifest_temp = _write_manifest_temp(
            manifest_path,
            manifest_payload,
        )
    except Exception:
        for temporary_path in staged.values():
            temporary_path.unlink(
                missing_ok=True
            )
        raise

    _commit_collection(
        staged=staged,
        manifest_path=manifest_path,
        manifest_temp=manifest_temp,
        stale_paths=set(),
    )

    return AccountCollectionResult(
        profile_id=selected_profile.profile_id,
        profile_display_name=selected_profile.display_name,
        account_slug=slug,
        output_folder=output_folder,
        combined_csv_path=combined_csv_path,
        manifest_path=manifest_path,
        collected_count=len(entries),
        combined_transaction_count=len(combined_transactions),
        duplicate_statement_copies_skipped=(
            duplicate_statement_copies_skipped
        ),
    )


def collect_profile_institution_statement_csvs(
    selected_profile: ExtractionProfile,
    *,
    profiles: Iterable[ExtractionProfile] | None = None,
    collection_base: str | Path | None = None,
) -> InstitutionCollectionResult:
    available_profiles = (
        discover_profiles()
        if profiles is None
        else tuple(profiles)
    )

    return collect_institution_statement_csvs(
        available_profiles,
        selected_profile.institution,
        collection_base=collection_base,
    )
