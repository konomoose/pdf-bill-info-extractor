from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


class PDFSecurityError(RuntimeError):
    """Raised when PDF security removal cannot complete safely."""


class PDFPasswordRequiredError(PDFSecurityError):
    """Raised when a PDF requires a password that was not accepted."""


@dataclass(frozen=True)
class PageTextState:
    page_number: int
    text_characters: int
    positioned_words: int
    images: int
    normalized_text_hash: str


@dataclass(frozen=True)
class PDFSecurityResult:
    source: Path
    destination: Path
    status: str
    message: str


def normalized_text_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def inspect_document(document: fitz.Document) -> list[PageTextState]:
    states: list[PageTextState] = []

    for page_number, page in enumerate(document, start=1):
        text = page.get_text("text")
        words = page.get_text("words")

        states.append(
            PageTextState(
                page_number=page_number,
                text_characters=len(text.strip()),
                positioned_words=len(words),
                images=len(page.get_images(full=True)),
                normalized_text_hash=normalized_text_hash(text),
            )
        )

    return states


def verify_text_preserved(
    source_states: list[PageTextState],
    output_states: list[PageTextState],
) -> None:
    if len(source_states) != len(output_states):
        raise PDFSecurityError(
            f"Page count changed from {len(source_states)} "
            f"to {len(output_states)}."
        )

    source_total_words = sum(
        state.positioned_words for state in source_states
    )
    output_total_words = sum(
        state.positioned_words for state in output_states
    )

    if source_total_words == 0:
        raise PDFSecurityError(
            "Source PDF is image-only. Security removal cannot create "
            "a text layer; OCR would be required."
        )

    if output_total_words == 0:
        raise PDFSecurityError(
            "Saved PDF lost its readable text layer."
        )

    for source, output in zip(
        source_states,
        output_states,
        strict=True,
    ):
        if (
            source.positioned_words == 0
            and source.text_characters == 0
        ):
            continue

        if (
            output.positioned_words == 0
            or output.text_characters == 0
        ):
            raise PDFSecurityError(
                f"Page {source.page_number} had readable text before "
                "saving but none afterward."
            )

        if source.positioned_words != output.positioned_words:
            raise PDFSecurityError(
                f"Page {source.page_number} positioned-word count "
                f"changed from {source.positioned_words} "
                f"to {output.positioned_words}."
            )

        if (
            source.normalized_text_hash
            != output.normalized_text_hash
        ):
            raise PDFSecurityError(
                f"Page {source.page_number} extracted text changed "
                "during security removal."
            )


def authenticate_document(
    document: fitz.Document,
    password: str | None,
) -> int:
    needs_password = bool(document.needs_pass)
    candidate = "" if password is None else password
    result = document.authenticate(candidate)

    if needs_password and result == 0:
        raise PDFPasswordRequiredError(
            "The supplied PDF password was not accepted."
        )

    return result


def collect_pdfs(source_root: Path) -> list[Path]:
    if not source_root.exists():
        raise PDFSecurityError(
            f"Source folder not found: {source_root}"
        )

    if not source_root.is_dir():
        raise PDFSecurityError(
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


def verify_existing_output(
    source: Path,
    destination: Path,
    *,
    password: str | None,
) -> bool:
    try:
        with fitz.open(source) as source_document:
            authenticate_document(source_document, password)
            source_states = inspect_document(source_document)

        with fitz.open(destination) as output_document:
            if (
                output_document.needs_pass
                or output_document.is_encrypted
            ):
                return False

            output_states = inspect_document(output_document)

        verify_text_preserved(source_states, output_states)
        return True

    except Exception:
        return False


def remove_pdf_security(
    source: Path,
    source_root: Path,
    output_root: Path,
    *,
    password: str | None = None,
    force: bool = False,
) -> PDFSecurityResult:
    source = source.resolve()
    source_root = source_root.resolve()
    output_root = output_root.resolve()

    destination = output_path_for(
        source,
        source_root,
        output_root,
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if destination.exists() and not force:
        if verify_existing_output(
            source,
            destination,
            password=password,
        ):
            return PDFSecurityResult(
                source=source,
                destination=destination,
                status="skipped",
                message="Verified unsecured output already exists.",
            )

        raise PDFSecurityError(
            f"Output exists but could not be verified against source: "
            f"{destination}. Use force only after reviewing it."
        )

    temporary = destination.with_name(
        f".{destination.name}.unsecured.tmp.pdf"
    )

    temporary.unlink(missing_ok=True)

    try:
        with fitz.open(source) as document:
            authentication_result = authenticate_document(
                document,
                password,
            )

            source_states = inspect_document(document)

            if sum(
                state.positioned_words
                for state in source_states
            ) == 0:
                raise PDFSecurityError(
                    "Source PDF is image-only. Security removal was "
                    "stopped because it would not make the document "
                    "text-readable."
                )

            document.save(
                temporary,
                encryption=fitz.PDF_ENCRYPT_NONE,
                garbage=4,
                deflate=True,
            )

        with fitz.open(temporary) as output_document:
            if (
                output_document.needs_pass
                or output_document.is_encrypted
            ):
                raise PDFSecurityError(
                    "Saved PDF is still encrypted."
                )

            output_states = inspect_document(
                output_document
            )

        verify_text_preserved(
            source_states,
            output_states,
        )

        if destination.exists():
            destination.unlink()

        os.replace(
            temporary,
            destination,
        )

        return PDFSecurityResult(
            source=source,
            destination=destination,
            status="created",
            message=(
                "Verified unsecured PDF created "
                f"(authentication result {authentication_result})."
            ),
        )

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


process_pdf = remove_pdf_security
