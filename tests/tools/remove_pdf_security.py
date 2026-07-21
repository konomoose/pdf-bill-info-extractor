from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass(frozen=True)
class PageTextState:
    page_number: int
    text_characters: int
    positioned_words: int
    images: int
    normalized_text_hash: str


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


def print_page_summary(label: str, states: list[PageTextState]) -> None:
    print(label)
    for state in states:
        print(
            f"  Page {state.page_number}: "
            f"{state.text_characters} text characters, "
            f"{state.positioned_words} positioned words, "
            f"{state.images} images"
        )


def verify_text_preserved(
    source_states: list[PageTextState],
    output_states: list[PageTextState],
) -> None:
    if len(source_states) != len(output_states):
        raise RuntimeError(
            f"Page count changed from {len(source_states)} to {len(output_states)}."
        )

    source_total_words = sum(state.positioned_words for state in source_states)
    output_total_words = sum(state.positioned_words for state in output_states)

    if source_total_words == 0:
        raise RuntimeError(
            "The source PDF is image-only. Removing PDF security cannot create a "
            "text layer; OCR would be required."
        )

    if output_total_words == 0:
        raise RuntimeError(
            "The saved PDF became image-only or lost its readable text layer."
        )

    for source, output in zip(source_states, output_states, strict=True):
        if source.positioned_words == 0 and source.text_characters == 0:
            # A legitimate promotional or scanned page may already be image-only.
            continue

        if output.positioned_words == 0 or output.text_characters == 0:
            raise RuntimeError(
                f"Page {source.page_number} had readable text before saving but "
                "has no readable text afterward."
            )

        if source.positioned_words != output.positioned_words:
            raise RuntimeError(
                f"Page {source.page_number} positioned-word count changed from "
                f"{source.positioned_words} to {output.positioned_words}."
            )

        if source.normalized_text_hash != output.normalized_text_hash:
            raise RuntimeError(
                f"Page {source.page_number} extracted text changed during saving."
            )


def authenticate_document(document: fitz.Document, password: str | None) -> int:
    # Calling authenticate("") also handles owner-password-only PDFs that open
    # normally but have permission restrictions.
    if password is None:
        password = ""

    result = document.authenticate(password)

    if document.needs_pass and result == 0:
        raise RuntimeError("The supplied PDF password was not accepted.")

    return result


def destination_for(source: Path, suffix: str) -> Path:
    return source.with_name(f"{source.stem}{suffix}{source.suffix}")


def process_pdf(
    source: Path,
    *,
    password: str | None,
    suffix: str,
    force: bool,
    replace_original: bool,
) -> Path:
    source = source.resolve()

    if not source.is_file():
        raise RuntimeError(f"PDF not found: {source}")

    destination = destination_for(source, suffix)
    temporary = source.with_name(f".{source.name}.unsecured.tmp.pdf")
    backup = source.with_name(f"{source.name}.restricted")

    if destination.exists() and not force and not replace_original:
        raise RuntimeError(
            f"Destination already exists: {destination}. "
            "Use --force only after reviewing the existing file."
        )

    temporary.unlink(missing_ok=True)

    try:
        with fitz.open(source) as document:
            encryption_description = (document.metadata or {}).get("encryption")
            originally_encrypted = bool(
                document.is_encrypted
                or document.needs_pass
                or encryption_description
            )
            original_permissions = document.permissions
            authentication_result = authenticate_document(document, password)
            source_states = inspect_document(document)

            if sum(state.positioned_words for state in source_states) == 0:
                raise RuntimeError(
                    "The source PDF is image-only. Security removal was stopped "
                    "because it would not make the document text-readable."
                )

            document.save(
                temporary,
                encryption=fitz.PDF_ENCRYPT_NONE,
                garbage=4,
                deflate=True,
            )

        with fitz.open(temporary) as output_document:
            if output_document.needs_pass or output_document.is_encrypted:
                raise RuntimeError("The saved PDF is still encrypted.")

            output_states = inspect_document(output_document)

        verify_text_preserved(source_states, output_states)

        if replace_original:
            if backup.exists() and not force:
                raise RuntimeError(
                    f"Backup already exists: {backup}. "
                    "Nothing was replaced. Use --force only after reviewing it."
                )

            if backup.exists():
                backup.unlink()

            os.replace(source, backup)
            os.replace(temporary, source)
            final_path = source
            print(f"Original backup: {backup}")
        else:
            if destination.exists():
                destination.unlink()
            os.replace(temporary, destination)
            final_path = destination

        print(f"Source: {source}")
        print(f"Authentication result: {authentication_result}")
        print(f"Originally encrypted/restricted: {originally_encrypted}")
        print(f"Original encryption: {encryption_description or 'None'}")
        print(f"Original permissions value: {original_permissions}")
        print_page_summary("Source text check:", source_states)
        print_page_summary("Saved text check:", output_states)
        print(f"Verified unencrypted PDF: {final_path}")
        return final_path

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def collect_pdfs(target: Path, recursive: bool, suffix: str) -> list[Path]:
    if target.is_file():
        return [target]

    if not target.is_dir():
        raise RuntimeError(f"Path not found: {target}")

    iterator = target.rglob("*.pdf") if recursive else target.glob("*.pdf")
    return sorted(
        (
            path
            for path in iterator
            if path.is_file() and not path.stem.endswith(suffix)
        ),
        key=lambda path: str(path).casefold(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Save an authorized PDF without encryption or permission restrictions "
            "while verifying that its existing text layer is preserved."
        )
    )
    parser.add_argument("target", type=Path, help="PDF file or folder")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Process PDFs in subfolders when target is a folder",
    )
    parser.add_argument(
        "--password",
        help=(
            "PDF owner or user password. Omit this option to try an empty password "
            "first and securely prompt only when the PDF requires one."
        ),
    )
    parser.add_argument(
        "--suffix",
        default="_unsecured",
        help="Output filename suffix; default: _unsecured",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing generated output or backup",
    )
    parser.add_argument(
        "--replace-original",
        action="store_true",
        help=(
            "After verification, replace the original filename and preserve the "
            "restricted original as filename.pdf.restricted"
        ),
    )
    args = parser.parse_args()

    try:
        pdfs = collect_pdfs(args.target, args.recursive, args.suffix)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not pdfs:
        print("ERROR: No PDF files found.", file=sys.stderr)
        return 1

    failures = 0

    for source in pdfs:
        print("=" * 72)
        print(f"Processing: {source}")

        password = args.password

        try:
            # First try without prompting. If the PDF truly requires a password,
            # reopen it after prompting so the password is not exposed on-screen.
            try:
                process_pdf(
                    source,
                    password=password,
                    suffix=args.suffix,
                    force=args.force,
                    replace_original=args.replace_original,
                )
            except RuntimeError as exc:
                if password is None and "password was not accepted" in str(exc):
                    password = getpass.getpass(
                        f"Password for {source.name}: "
                    )
                    process_pdf(
                        source,
                        password=password,
                        suffix=args.suffix,
                        force=args.force,
                        replace_original=args.replace_original,
                    )
                else:
                    raise

        except Exception as exc:
            failures += 1
            print(f"FAILED: {exc}", file=sys.stderr)

    print("=" * 72)
    print(f"Processed: {len(pdfs) - failures}")
    print(f"Failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
