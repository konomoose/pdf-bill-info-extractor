from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF


class PDFRedactionError(RuntimeError):
    """Raised when PDF redaction cannot complete safely."""


REPLACE_REDACTED_PDF_ERROR = (
    "Could not replace the existing redacted PDF. Close the redacted PDF "
    "if it is open in another application and run Prepare PDFs again."
)


@dataclass(frozen=True)
class RedactionRules:
    global_terms: tuple[str, ...]
    institution_terms: dict[str, tuple[str, ...]]

    def terms_for(self, institution: str) -> tuple[str, ...]:
        terms = list(self.global_terms)
        terms.extend(self.institution_terms.get(institution, ()))
        return normalize_redaction_terms(terms)


@dataclass(frozen=True)
class PDFRedactionResult:
    source: Path
    destination: Path | None
    status: str
    message: str
    redaction_count: int = 0


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_redaction_terms(
    values: Any,
    *,
    label: str = "redaction terms",
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise PDFRedactionError(
            f"{label} must be a list of strings."
        )

    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            raise PDFRedactionError(
                f"{label} contains a non-string value."
            )

        term = value.strip()

        if not term:
            continue

        key = term.casefold()
        if key in seen:
            continue

        seen.add(key)
        normalized.append(term)

    return tuple(normalized)


def normalize_rule_terms(
    values: object,
    *,
    label: str,
) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise PDFRedactionError(
            f"{label} must be a JSON array of strings."
        )

    for value in values:
        if not isinstance(value, str):
            raise PDFRedactionError(
                f"{label} contains a non-string value."
            )

        if not value.strip():
            raise PDFRedactionError(
                f"{label} contains an empty redaction term."
            )

    return normalize_redaction_terms(
        values,
        label=label,
    )


def load_rules(path: Path) -> RedactionRules:
    if not path.is_file():
        raise PDFRedactionError(
            f"Redaction rules file not found: {path}\n"
            "Create config/redaction.local.json from "
            "config/redaction.example.json."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PDFRedactionError(
            f"Invalid JSON in redaction rules file: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise PDFRedactionError(
            "Redaction rules must contain a JSON object."
        )

    global_terms = normalize_rule_terms(
        data.get("global_terms", []),
        label="global_terms",
    )

    raw_institutions = data.get("institution_terms", {})

    if not isinstance(raw_institutions, dict):
        raise PDFRedactionError(
            "institution_terms must be a JSON object."
        )

    institution_terms: dict[str, tuple[str, ...]] = {}

    for institution, values in raw_institutions.items():
        if not isinstance(institution, str) or not institution.strip():
            raise PDFRedactionError(
                "institution_terms contains an invalid institution name."
            )

        key = institution.strip()
        institution_terms[key] = normalize_rule_terms(
            values,
            label=f"institution_terms.{key}",
        )

    return RedactionRules(
        global_terms=global_terms,
        institution_terms=institution_terms,
    )


def collect_pdfs(source_root: Path) -> list[Path]:
    if not source_root.exists():
        raise PDFRedactionError(
            f"Source folder not found: {source_root}"
        )

    if not source_root.is_dir():
        raise PDFRedactionError(
            f"Source path must be a folder: {source_root}"
        )

    return sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: str(path).casefold(),
    )


def output_path_for(
    source: Path,
    source_root: Path,
    output_root: Path,
) -> Path:
    return output_root / source.relative_to(source_root)


def institution_for(
    source: Path,
    source_root: Path,
) -> str:
    relative = source.relative_to(source_root)

    if not relative.parts:
        return ""

    return relative.parts[0]


def verify_redacted_output(
    destination: Path,
    *,
    expected_page_count: int,
    terms: tuple[str, ...],
) -> None:
    with fitz.open(destination) as document:
        if document.needs_pass or document.is_encrypted:
            raise PDFRedactionError(
                "Redacted output is encrypted."
            )

        if document.page_count != expected_page_count:
            raise PDFRedactionError(
                "Page count changed during redaction."
            )

        for page_number, page in enumerate(document, start=1):
            for term in terms:
                if page.search_for(term):
                    raise PDFRedactionError(
                        "Configured redaction content still exists "
                        f"on page {page_number}."
                    )

        metadata = document.metadata or {}

        sensitive_metadata_keys = (
            "title",
            "author",
            "subject",
            "keywords",
            "creator",
            "producer",
            "creationDate",
            "modDate",
        )

        for key in sensitive_metadata_keys:
            value = metadata.get(key)

            if value not in (None, "", "none"):
                raise PDFRedactionError(
                    f"Metadata field was not cleared: {key}"
                )

        if document.embfile_count() != 0:
            raise PDFRedactionError(
                "Embedded files remain in redacted output."
            )


def find_redaction_matches(
    source: Path,
    terms: tuple[str, ...],
) -> dict[str, int]:
    matches = {term: 0 for term in terms}

    with fitz.open(source) as document:
        if document.needs_pass:
            raise PDFRedactionError(
                "Source PDF requires a password. "
                "Run PDF security removal first."
            )

        for page in document:
            for term in terms:
                matches[term] += len(page.search_for(term))

    return matches


def redact_pdf(
    source: Path,
    source_root: Path,
    output_root: Path,
    terms: tuple[str, ...],
    *,
    force: bool = False,
) -> PDFRedactionResult:
    source = source.resolve()
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    terms = normalize_redaction_terms(tuple(terms))

    destination = output_path_for(
        source,
        source_root,
        output_root,
    )

    if not terms:
        return PDFRedactionResult(
            source=source,
            destination=None,
            status="no_rules",
            message="No redaction terms configured.",
        )

    if destination.exists() and not force:
        return PDFRedactionResult(
            source=source,
            destination=destination,
            status="skipped",
            message=(
                "Redacted output already exists. "
                "Use force after changing the source or rules."
            ),
        )

    source_hash_before = file_hash(source)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = destination.with_name(
        f".{destination.name}.redacted.tmp.pdf"
    )

    temporary.unlink(missing_ok=True)

    try:
        with fitz.open(source) as document:
            if document.needs_pass:
                raise PDFRedactionError(
                    "Source PDF requires a password. "
                    "Run PDF security removal first."
                )

            expected_page_count = document.page_count
            redaction_count = 0

            for page in document:
                page_redactions = 0

                for term in terms:
                    rectangles = page.search_for(term)

                    for rectangle in rectangles:
                        page.add_redact_annot(
                            rectangle,
                            fill=(0, 0, 0),
                            cross_out=False,
                        )
                        redaction_count += 1
                        page_redactions += 1

                if page_redactions:
                    page.apply_redactions()

            document.scrub(
                attached_files=True,
                clean_pages=True,
                embedded_files=True,
                hidden_text=False,
                javascript=True,
                metadata=True,
                redactions=False,
                remove_links=True,
                reset_fields=True,
                reset_responses=True,
                thumbnails=True,
                xml_metadata=True,
            )

            document.save(
                temporary,
                encryption=fitz.PDF_ENCRYPT_NONE,
                garbage=4,
                deflate=True,
            )

        verify_redacted_output(
            temporary,
            expected_page_count=expected_page_count,
            terms=terms,
        )

        if file_hash(source) != source_hash_before:
            raise PDFRedactionError(
                "Source PDF changed during redaction."
            )

        try:
            os.replace(
                temporary,
                destination,
            )
        except OSError as exc:
            raise PDFRedactionError(
                REPLACE_REDACTED_PDF_ERROR
            ) from exc

        if redaction_count == 0:
            return PDFRedactionResult(
                source=source,
                destination=destination,
                status="already_clean",
                message=(
                    "Configured redaction terms were already absent; "
                    "verified sanitized copy created."
                ),
                redaction_count=0,
            )

        return PDFRedactionResult(
            source=source,
            destination=destination,
            status="created",
            message=(
                f"Verified redacted PDF created with "
                f"{redaction_count} redaction(s)."
            ),
            redaction_count=redaction_count,
        )

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def redact_pdf_with_rules(
    source: Path,
    source_root: Path,
    output_root: Path,
    rules: RedactionRules,
    *,
    force: bool = False,
) -> PDFRedactionResult:
    institution = institution_for(
        source.resolve(),
        source_root.resolve(),
    )

    return redact_pdf(
        source,
        source_root,
        output_root,
        rules.terms_for(institution),
        force=force,
    )


def roots_overlap(
    source_root: Path,
    output_root: Path,
) -> bool:
    source_root = source_root.resolve()
    output_root = output_root.resolve()

    return (
        source_root == output_root
        or source_root in output_root.parents
        or output_root in source_root.parents
    )


process_pdf = redact_pdf_with_rules
