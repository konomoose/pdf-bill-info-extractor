from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

import pandas as pd

from .pdf_processor import (
    PDFProcessingError,
    VisaPDFProcessor,
)
from .profile_loader import (
    PROJECT_ROOT,
    ExtractionProfile,
    discover_profiles,
)
from .transaction_normalizer import (
    NormalizationError,
    normalize_transactions,
)
from .yearly_consolidator import (
    ConsolidationError,
    consolidate_normalized_transactions,
    write_yearly_transaction_csvs,
)


class WorkflowError(RuntimeError):
    """Raised when the extraction workflow cannot be completed."""


@dataclass(frozen=True)
class WorkflowFileResult:
    profile_id: str
    profile_name: str
    pdf_file: Path | None
    status: str
    transaction_count: int
    source_pages: tuple[int, ...]
    raw_csv: Path | None
    normalized_csv: Path | None
    error: str | None


@dataclass(frozen=True)
class YearlyOutput:
    output_root: Path
    institution_slug: str
    year: int
    csv_path: Path
    transaction_count: int


@dataclass(frozen=True)
class WorkflowResult:
    files: tuple[WorkflowFileResult, ...]
    yearly_outputs: tuple[YearlyOutput, ...]
    summary_csv: Path

    @property
    def successful_count(self) -> int:
        return sum(
            item.status == "Success"
            for item in self.files
        )

    @property
    def failed_count(self) -> int:
        return sum(
            item.status == "Failed"
            for item in self.files
        )

    @property
    def skipped_count(self) -> int:
        return sum(
            item.status == "Skipped"
            for item in self.files
        )

    @property
    def transaction_count(self) -> int:
        return sum(
            item.transaction_count
            for item in self.files
        )


ProcessorFactory = Callable[..., Any]


def _find_pdf_files(
    profile: ExtractionProfile,
    source_folder: Path,
) -> list[Path]:
    iterator = (
        source_folder.rglob(profile.file_pattern)
        if profile.recursive
        else source_folder.glob(profile.file_pattern)
    )

    return sorted(
        (
            path
            for path in iterator
            if path.is_file()
        ),
        key=lambda path: str(path).casefold(),
    )


def _normalize_selected_pdf_files(
    selected_pdf_files: Iterable[
        str | Path
    ],
) -> list[Path]:
    selected: dict[str, Path] = {}

    for value in selected_pdf_files:
        path = Path(value).resolve()

        if not path.is_file():
            raise WorkflowError(
                f"Selected PDF file not found: {path}"
            )

        if path.suffix.lower() != ".pdf":
            raise WorkflowError(
                f"Selected file is not a PDF: {path}"
            )

        selected[str(path).casefold()] = path

    return sorted(
        selected.values(),
        key=lambda path: str(path).casefold(),
    )


def _load_normalized_statement_frames(
    output_root: Path,
) -> list[pd.DataFrame]:
    """
    Load all normalized statement CSVs below an output root.

    This keeps yearly consolidation complete when the workflow
    processes only one newly selected statement.
    """
    frames: list[pd.DataFrame] = []

    for csv_path in sorted(
        output_root.rglob(
            "*_normalized_transactions.csv"
        ),
        key=lambda path: str(path).casefold(),
    ):
        frame = pd.read_csv(
            csv_path,
            dtype=str,
            keep_default_na=False,
        )

        if not frame.empty:
            frames.append(frame)

    return frames


def _statement_destination(
    profile: ExtractionProfile,
    pdf_file: Path,
    source_folder: Path,
    output_root: Path,
) -> Path:
    if not profile.preserve_subfolders:
        return output_root

    return (
        output_root
        / pdf_file.parent.relative_to(source_folder)
    )


def _normalized_csv_path(
    pdf_file: Path,
    destination: Path,
) -> Path:
    return (
        destination
        / (
            f"{pdf_file.stem}"
            "_normalized_transactions.csv"
        )
    )


def _raw_csv_path(
    pdf_file: Path,
    destination: Path,
) -> Path:
    return (
        destination
        / f"{pdf_file.stem}_transactions.csv"
    )


def _stage_csv(
    transactions: pd.DataFrame,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    ) as temporary_file:
        transactions.to_csv(
            temporary_file,
            index=False,
            lineterminator="\n",
        )

        return Path(temporary_file.name)


def _write_statement_csvs(
    raw_transactions: pd.DataFrame,
    raw_path: Path,
    normalized_transactions: pd.DataFrame,
    normalized_path: Path,
) -> None:
    targets = (
        (
            raw_transactions,
            raw_path,
        ),
        (
            normalized_transactions,
            normalized_path,
        ),
    )

    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path] = {}
    committed: set[Path] = set()

    try:
        for transactions, target in targets:
            staged.append(
                (
                    _stage_csv(
                        transactions,
                        target,
                    ),
                    target,
                )
            )

        for _, target in staged:
            if not target.exists():
                continue

            backup = target.with_name(
                f".{target.name}."
                f"{uuid4().hex}.bak"
            )

            target.replace(backup)
            backups[target] = backup

        for temporary_path, target in staged:
            temporary_path.replace(target)
            committed.add(target)

    except Exception:
        for target in committed:
            target.unlink(
                missing_ok=True
            )

        for target, backup in backups.items():
            if backup.exists():
                backup.replace(target)

        raise

    finally:
        for temporary_path, _ in staged:
            temporary_path.unlink(
                missing_ok=True
            )

        for backup in backups.values():
            backup.unlink(
                missing_ok=True
            )


def _profile_output_slug(
    profile: ExtractionProfile,
) -> str:
    slug = profile.output_folder.name.strip()

    if not slug:
        raise WorkflowError(
            f"Profile {profile.profile_id!r} has no "
            "usable output-folder name."
        )

    return slug


def _write_workflow_summary(
    file_results: Iterable[WorkflowFileResult],
    summary_root: Path,
) -> Path:
    summary_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    summary_path = (
        summary_root
        / f"workflow_summary_{timestamp}.csv"
    )

    rows = [
        {
            "Profile ID": item.profile_id,
            "Profile Name": item.profile_name,
            "PDF File": (
                str(item.pdf_file)
                if item.pdf_file is not None
                else ""
            ),
            "Status": item.status,
            "Transactions": item.transaction_count,
            "Pages": ", ".join(
                str(page)
                for page in item.source_pages
            ),
            "Raw CSV": (
                str(item.raw_csv)
                if item.raw_csv is not None
                else ""
            ),
            "Normalized CSV": (
                str(item.normalized_csv)
                if item.normalized_csv is not None
                else ""
            ),
            "Error": item.error or "",
        }
        for item in file_results
    ]

    pd.DataFrame(rows).to_csv(
        summary_path,
        index=False,
        lineterminator="\n",
    )

    return summary_path


def run_selected_extraction_workflow(
    profile: ExtractionProfile,
    source_path: str | Path,
    output_root: str | Path,
    *,
    summary_root: str | Path | None = None,
    processor_factory: ProcessorFactory = VisaPDFProcessor,
) -> WorkflowResult:
    """
    Run one profile against a user-selected PDF or folder.

    Profile parser settings remain unchanged. Only the runtime
    input and output paths are replaced.
    """
    selected_source = Path(
        source_path
    ).resolve()
    selected_output = Path(
        output_root
    ).resolve()

    if selected_source.is_file():
        if selected_source.suffix.lower() != ".pdf":
            raise WorkflowError(
                "The selected input file must be a PDF."
            )

        runtime_profile = replace(
            profile,
            input_folder=selected_source.parent,
            output_folder=selected_output,
            recursive=False,
            preserve_subfolders=False,
        )

        selected_pdf_files: tuple[Path, ...] | None = (
            selected_source,
        )

    elif selected_source.is_dir():
        runtime_profile = replace(
            profile,
            input_folder=selected_source,
            output_folder=selected_output,
        )

        selected_pdf_files = None

    else:
        raise WorkflowError(
            f"Selected input does not exist: "
            f"{selected_source}"
        )

    summary_folder = (
        Path(summary_root).resolve()
        if summary_root is not None
        else selected_output
    )

    return run_extraction_workflow(
        [runtime_profile],
        summary_root=summary_folder,
        processor_factory=processor_factory,
        selected_pdf_files=selected_pdf_files,
    )


def run_extraction_workflow(
    profiles: Iterable[ExtractionProfile] | None = None,
    *,
    summary_root: str | Path | None = None,
    processor_factory: ProcessorFactory = VisaPDFProcessor,
    selected_pdf_files: Iterable[
        str | Path
    ] | None = None,
) -> WorkflowResult:
    """
    Extract, normalize, and consolidate transactions.

    Original parser-specific statement CSVs are preserved.
    A normalized statement CSV is written beside each original
    statement CSV. Yearly normalized CSVs are written once per
    shared profile output root.
    """

    selected_profiles = tuple(
        discover_profiles()
        if profiles is None
        else profiles
    )

    if not selected_profiles:
        raise WorkflowError(
            "No extraction profiles were selected."
        )

    explicit_pdf_files: tuple[
        Path,
        ...,
    ] | None = None

    if selected_pdf_files is not None:
        if len(selected_profiles) != 1:
            raise WorkflowError(
                "Explicit PDF selection requires exactly "
                "one extraction profile."
            )

        explicit_pdf_files = tuple(
            _normalize_selected_pdf_files(
                selected_pdf_files
            )
        )

    summary_folder = (
        Path(summary_root)
        if summary_root is not None
        else PROJECT_ROOT / "csv_output"
    )

    file_results: list[WorkflowFileResult] = []

    successful_output_roots: set[Path] = set()

    grouped_slugs: dict[Path, str] = {}

    for profile in selected_profiles:
        processor = processor_factory(
            profile=profile
        )

        source_folder = (
            profile.resolve_input_folder()
        )
        output_root = (
            profile.resolve_output_folder()
        )

        output_key = output_root.resolve()
        profile_slug = _profile_output_slug(
            profile
        )

        existing_slug = grouped_slugs.get(
            output_key
        )

        if (
            existing_slug is not None
            and existing_slug != profile_slug
        ):
            raise WorkflowError(
                "Profiles sharing an output folder "
                "must use the same output-folder slug."
            )

        grouped_slugs[output_key] = profile_slug

        if not source_folder.is_dir():
            file_results.append(
                WorkflowFileResult(
                    profile_id=profile.profile_id,
                    profile_name=profile.display_name,
                    pdf_file=None,
                    status="Failed",
                    transaction_count=0,
                    source_pages=(),
                    raw_csv=None,
                    normalized_csv=None,
                    error=(
                        "Input folder not found: "
                        f"{source_folder}"
                    ),
                )
            )
            continue

        if explicit_pdf_files is not None:
            outside_source = [
                pdf_file
                for pdf_file in explicit_pdf_files
                if not pdf_file.is_relative_to(
                    source_folder
                )
            ]

            if outside_source:
                raise WorkflowError(
                    "Every explicitly selected PDF must be "
                    "inside the selected input folder."
                )

            pdf_files = list(
                explicit_pdf_files
            )
        else:
            pdf_files = _find_pdf_files(
                profile,
                source_folder,
            )

        if not pdf_files:
            file_results.append(
                WorkflowFileResult(
                    profile_id=profile.profile_id,
                    profile_name=profile.display_name,
                    pdf_file=None,
                    status="Skipped",
                    transaction_count=0,
                    source_pages=(),
                    raw_csv=None,
                    normalized_csv=None,
                    error=(
                        "No PDF files matching "
                        f"{profile.file_pattern!r}."
                    ),
                )
            )
            continue

        for pdf_file in pdf_files:
            raw_csv: Path | None = None
            normalized_csv: Path | None = None

            try:
                skip_reason = (
                    processor
                    .non_transaction_document_reason(
                        pdf_file
                    )
                )

                if skip_reason is not None:
                    file_results.append(
                        WorkflowFileResult(
                            profile_id=(
                                profile.profile_id
                            ),
                            profile_name=(
                                profile.display_name
                            ),
                            pdf_file=pdf_file,
                            status="Skipped",
                            transaction_count=0,
                            source_pages=(),
                            raw_csv=None,
                            normalized_csv=None,
                            error=skip_reason,
                        )
                    )
                    continue

                result = (
                    processor.extract_transactions(
                        pdf_file
                    )
                )

                if result.metadata is None:
                    raise WorkflowError(
                        "Extraction result has no "
                        "statement metadata."
                    )

                normalized = (
                    normalize_transactions(
                        result.transactions,
                        result.metadata,
                    )
                )

                destination = (
                    _statement_destination(
                        profile,
                        pdf_file,
                        source_folder,
                        output_root,
                    )
                )

                raw_csv = _raw_csv_path(
                    pdf_file,
                    destination,
                )
                normalized_csv = (
                    _normalized_csv_path(
                        pdf_file,
                        destination,
                    )
                )

                _write_statement_csvs(
                    result.transactions,
                    raw_csv,
                    normalized,
                    normalized_csv,
                )

                successful_output_roots.add(
                    output_key
                )

                file_results.append(
                    WorkflowFileResult(
                        profile_id=profile.profile_id,
                        profile_name=profile.display_name,
                        pdf_file=pdf_file,
                        status="Success",
                        transaction_count=len(
                            normalized
                        ),
                        source_pages=(
                            result.source_pages
                        ),
                        raw_csv=raw_csv,
                        normalized_csv=(
                            normalized_csv
                        ),
                        error=None,
                    )
                )

            except (
                PDFProcessingError,
                NormalizationError,
                WorkflowError,
                OSError,
                ValueError,
            ) as exc:
                file_results.append(
                    WorkflowFileResult(
                        profile_id=profile.profile_id,
                        profile_name=profile.display_name,
                        pdf_file=pdf_file,
                        status="Failed",
                        transaction_count=0,
                        source_pages=(),
                        raw_csv=raw_csv,
                        normalized_csv=(
                            normalized_csv
                        ),
                        error=str(exc),
                    )
                )

            except Exception as exc:
                file_results.append(
                    WorkflowFileResult(
                        profile_id=profile.profile_id,
                        profile_name=profile.display_name,
                        pdf_file=pdf_file,
                        status="Failed",
                        transaction_count=0,
                        source_pages=(),
                        raw_csv=raw_csv,
                        normalized_csv=(
                            normalized_csv
                        ),
                        error=(
                            "Unexpected error: "
                            f"{exc}"
                        ),
                    )
                )

    yearly_outputs: list[YearlyOutput] = []

    for output_root in sorted(
        successful_output_roots,
        key=lambda path: str(path).casefold(),
    ):
        frames = (
            _load_normalized_statement_frames(
                output_root
            )
        )
        slug = grouped_slugs[output_root]

        if not frames:
            continue

        try:
            yearly_frames = (
                consolidate_normalized_transactions(
                    frames
                )
            )
            written = (
                write_yearly_transaction_csvs(
                    frames,
                    output_root=output_root,
                    institution_slug=slug,
                )
            )
        except (
            ConsolidationError,
            OSError,
            ValueError,
        ) as exc:
            raise WorkflowError(
                "Could not create yearly CSVs for "
                f"{output_root}: {exc}"
            ) from exc

        for year, csv_path in sorted(
            written.items()
        ):
            yearly_outputs.append(
                YearlyOutput(
                    output_root=output_root,
                    institution_slug=slug,
                    year=year,
                    csv_path=csv_path,
                    transaction_count=len(
                        yearly_frames[year]
                    ),
                )
            )

    summary_csv = _write_workflow_summary(
        file_results,
        summary_folder,
    )

    return WorkflowResult(
        files=tuple(file_results),
        yearly_outputs=tuple(yearly_outputs),
        summary_csv=summary_csv,
    )
