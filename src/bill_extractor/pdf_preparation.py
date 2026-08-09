from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .pdf_redaction import (
    PDFRedactionError,
    normalize_redaction_terms,
    redact_pdf,
)
from .pdf_security import (
    PDFPasswordRequiredError,
    PDFSecurityError,
    collect_pdfs,
    remove_pdf_security,
)
from .profile_loader import (
    PROJECT_ROOT,
    ExtractionProfile,
)


class PDFPreparationError(RuntimeError):
    """Raised when PDF preparation cannot be configured or run safely."""


@dataclass(frozen=True)
class PDFPreparationFolders:
    account_key: Path
    source_folder: Path
    editable_folder: Path
    redacted_folder: Path


@dataclass(frozen=True)
class PDFPreparationResult:
    account_key: str
    profile_id: str | None
    profile_display_name: str | None
    source_folder: Path
    editable_folder: Path
    redacted_folder: Path
    source_pdf_count: int
    security_created_count: int
    security_skipped_count: int
    security_password_required_count: int
    security_failed_count: int
    redaction_created_count: int
    redaction_already_clean_count: int
    redaction_skipped_count: int
    redaction_failed_count: int
    redaction_failure_messages: tuple[str, ...] = ()


def _project_root(
    project_root: Path | None,
) -> Path:
    return (
        Path(project_root)
        if project_root is not None
        else PROJECT_ROOT
    ).resolve()


def _ensure_within_root(
    path: Path,
    root: Path,
) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PDFPreparationError(
            "Preparation account key resolves outside the preparation root."
        ) from exc


def normalize_account_key(
    account_key: str | Path,
) -> Path:
    key = Path(account_key)

    if key.is_absolute() or key.drive or key.root:
        raise PDFPreparationError(
            "Preparation account key must be a relative path."
        )

    if not key.parts:
        raise PDFPreparationError(
            "Preparation account key must not be empty."
        )

    if any(part in ("", ".", "..") for part in key.parts):
        raise PDFPreparationError(
            "Preparation account key must not contain '.', '..', or empty parts."
        )

    return key


def preparation_folders_for_account(
    account_key: str | Path,
    *,
    project_root: Path | None = None,
) -> PDFPreparationFolders:
    root = _project_root(project_root)
    normalized_key = normalize_account_key(account_key)

    source_root = (
        root
        / "source_input"
    ).resolve()
    editable_root = (
        root
        / "editable_input"
    ).resolve()
    redacted_root = (
        root
        / "redacted_input"
    ).resolve()

    source_folder = (
        source_root
        / normalized_key
    ).resolve()
    editable_folder = (
        editable_root
        / normalized_key
    ).resolve()
    redacted_folder = (
        redacted_root
        / normalized_key
    ).resolve()

    _ensure_within_root(source_folder, source_root)
    _ensure_within_root(editable_folder, editable_root)
    _ensure_within_root(redacted_folder, redacted_root)

    return PDFPreparationFolders(
        account_key=normalized_key,
        source_folder=source_folder,
        editable_folder=editable_folder,
        redacted_folder=redacted_folder,
    )


def account_key_for_profile(
    profile: ExtractionProfile,
    *,
    project_root: Path | None = None,
) -> Path:
    root = _project_root(project_root)
    editable_root = (
        root
        / "editable_input"
    ).resolve()

    configured_input = (
        profile.input_folder.resolve()
        if profile.input_folder.is_absolute()
        else (root / profile.input_folder).resolve()
    )

    try:
        account_key = configured_input.relative_to(
            editable_root
        )
    except ValueError as exc:
        raise PDFPreparationError(
            "PDF preparation requires the selected profile input folder "
            "to be under editable_input."
        ) from exc

    return normalize_account_key(account_key)


def preparation_folders_for_profile(
    profile: ExtractionProfile,
    *,
    project_root: Path | None = None,
) -> PDFPreparationFolders:
    return preparation_folders_for_account(
        account_key_for_profile(
            profile,
            project_root=project_root,
        ),
        project_root=project_root,
    )


def prepare_account_pdfs(
    account_key: str | Path,
    terms: list[str] | tuple[str, ...],
    *,
    password: str | None = None,
    force: bool = False,
    project_root: Path | None = None,
    profile: ExtractionProfile | None = None,
) -> PDFPreparationResult:
    normalized_terms = normalize_redaction_terms(
        terms
    )

    if not normalized_terms:
        raise PDFPreparationError(
            "At least one redaction term is required."
        )

    folders = preparation_folders_for_account(
        account_key,
        project_root=project_root,
    )

    if not folders.source_folder.exists():
        raise PDFPreparationError(
            "Source folder does not exist:\n"
            f"{folders.source_folder}\n"
            "Create it and place PDFs there first."
        )

    try:
        source_pdfs = collect_pdfs(
            folders.source_folder
        )
    except PDFSecurityError as exc:
        raise PDFPreparationError(str(exc)) from exc

    security_created = 0
    security_skipped = 0
    security_password_required = 0
    security_failed = 0
    redaction_created = 0
    redaction_already_clean = 0
    redaction_skipped = 0
    redaction_failed = 0
    redaction_failure_messages: set[str] = set()

    for source_pdf in source_pdfs:
        try:
            security_result = remove_pdf_security(
                source_pdf,
                folders.source_folder,
                folders.editable_folder,
                password=password,
                force=force,
            )

        except PDFPasswordRequiredError:
            security_password_required += 1
            continue

        except Exception:
            security_failed += 1
            continue

        if security_result.status == "created":
            security_created += 1
        elif security_result.status == "skipped":
            security_skipped += 1

        try:
            redaction_result = redact_pdf(
                security_result.destination,
                folders.editable_folder,
                folders.redacted_folder,
                normalized_terms,
                force=True,
            )

        except PDFRedactionError as exc:
            redaction_failed += 1
            redaction_failure_messages.add(str(exc))
            continue

        except Exception:
            redaction_failed += 1
            redaction_failure_messages.add(
                "An unexpected redaction error occurred."
            )
            continue

        if redaction_result.status == "created":
            redaction_created += 1
        elif redaction_result.status == "already_clean":
            redaction_already_clean += 1
        elif redaction_result.status == "skipped":
            redaction_skipped += 1

    return PDFPreparationResult(
        account_key=folders.account_key.as_posix(),
        profile_id=(
            profile.profile_id
            if profile is not None
            else None
        ),
        profile_display_name=(
            profile.display_name
            if profile is not None
            else None
        ),
        source_folder=folders.source_folder,
        editable_folder=folders.editable_folder,
        redacted_folder=folders.redacted_folder,
        source_pdf_count=len(source_pdfs),
        security_created_count=security_created,
        security_skipped_count=security_skipped,
        security_password_required_count=security_password_required,
        security_failed_count=security_failed,
        redaction_created_count=redaction_created,
        redaction_already_clean_count=redaction_already_clean,
        redaction_skipped_count=redaction_skipped,
        redaction_failed_count=redaction_failed,
        redaction_failure_messages=tuple(
            sorted(
                redaction_failure_messages,
                key=str.casefold,
            )
        ),
    )


def prepare_profile_pdfs(
    profile: ExtractionProfile,
    terms: list[str] | tuple[str, ...],
    *,
    password: str | None = None,
    force: bool = False,
    project_root: Path | None = None,
) -> PDFPreparationResult:
    return prepare_account_pdfs(
        account_key_for_profile(
            profile,
            project_root=project_root,
        ),
        terms,
        password=password,
        force=force,
        project_root=project_root,
        profile=profile,
    )
