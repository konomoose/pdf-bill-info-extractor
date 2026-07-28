from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


DEFAULT_SOURCE_ROOT = Path("secured_input")
DEFAULT_OUTPUT_ROOT = Path("input")


@dataclass(frozen=True)
class PageTextState:
    page_number: int
    text_characters: int
    positioned_words: int
    images: int
    normalized_text_hash: str


@dataclass(frozen=True)
class ProcessResult:
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
        raise RuntimeError(
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
        raise RuntimeError(
            "Source PDF is image-only. Security removal cannot create "
            "a text layer; OCR would be required."
        )

    if output_total_words == 0:
        raise RuntimeError(
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
            # A page that was already image-only is allowed to remain so.
            continue

        if (
            output.positioned_words == 0
            or output.text_characters == 0
        ):
            raise RuntimeError(
                f"Page {source.page_number} had readable text before "
                "saving but none afterward."
            )

        if source.positioned_words != output.positioned_words:
            raise RuntimeError(
                f"Page {source.page_number} positioned-word count "
                f"changed from {source.positioned_words} "
                f"to {output.positioned_words}."
            )

        if (
            source.normalized_text_hash
            != output.normalized_text_hash
        ):
            raise RuntimeError(
                f"Page {source.page_number} extracted text changed "
                "during security removal."
            )


def authenticate_document(
    document: fitz.Document,
    password: str | None,
) -> int:
    # Empty authentication also handles many owner-password-only PDFs
    # that open normally but retain permission restrictions.
    candidate = "" if password is None else password
    result = document.authenticate(candidate)

    if document.needs_pass and result == 0:
        raise RuntimeError(
            "The supplied PDF password was not accepted."
        )

    return result


def collect_pdfs(source_root: Path) -> list[Path]:
    if not source_root.exists():
        raise RuntimeError(
            f"Source folder not found: {source_root}"
        )

    if not source_root.is_dir():
        raise RuntimeError(
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
    relative = source.relative_to(source_root)
    return output_root / relative


def verify_existing_output(
    source: Path,
    destination: Path,
    *,
    password: str | None,
) -> bool:
    """
    Return True when an existing destination is already a valid
    unsecured, text-preserving copy of source.
    """
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


def process_pdf(
    source: Path,
    source_root: Path,
    output_root: Path,
    *,
    password: str | None,
    force: bool,
) -> ProcessResult:
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
            return ProcessResult(
                source=source,
                destination=destination,
                status="skipped",
                message="Verified unsecured output already exists.",
            )

        raise RuntimeError(
            f"Output exists but could not be verified against source: "
            f"{destination}. Use --force only after reviewing it."
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
                raise RuntimeError(
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
                raise RuntimeError(
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

        return ProcessResult(
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively create verified unsecured PDF copies while "
            "preserving the source folder structure. Originals are "
            "never modified."
        )
    )

    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help=(
            "Folder containing secured/original PDFs. "
            "Default: secured_input"
        ),
    )

    parser.add_argument(
        "output_root",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=(
            "Destination root for unsecured PDFs. "
            "Default: input"
        ),
    )

    parser.add_argument(
        "--password",
        help=(
            "PDF owner or user password. Omit to try an empty "
            "password first and prompt securely only when needed."
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Replace an existing output that does not already verify "
            "against its source."
        ),
    )

    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()

    if source_root == output_root:
        print(
            "ERROR: Source and output folders must be different.",
            file=sys.stderr,
        )
        return 1

    try:
        pdfs = collect_pdfs(source_root)
    except RuntimeError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 1

    if not pdfs:
        print(
            f"No PDF files found under: {source_root}"
        )
        return 0

    created = 0
    skipped = 0
    failed = 0

    print(f"Source root: {source_root}")
    print(f"Output root: {output_root}")
    print(f"PDF files found: {len(pdfs)}")

    for source in pdfs:
        relative = source.relative_to(source_root)

        print("=" * 72)
        print(f"Processing: {relative}")

        password = args.password

        try:
            try:
                result = process_pdf(
                    source,
                    source_root,
                    output_root,
                    password=password,
                    force=args.force,
                )

            except RuntimeError as exc:
                if (
                    password is None
                    and "password was not accepted" in str(exc)
                ):
                    password = getpass.getpass(
                        f"Password for {relative}: "
                    )

                    result = process_pdf(
                        source,
                        source_root,
                        output_root,
                        password=password,
                        force=args.force,
                    )
                else:
                    raise

            if result.status == "created":
                created += 1
                print(
                    f"CREATED: {relative}"
                )
                print(
                    f"         {result.destination}"
                )

            elif result.status == "skipped":
                skipped += 1
                print(
                    f"SKIPPED: {relative}"
                )
                print(
                    f"         {result.message}"
                )

        except Exception as exc:
            failed += 1
            print(
                f"FAILED: {relative} | {exc}",
                file=sys.stderr,
            )

    print("=" * 72)
    print("SUMMARY")
    print(f"PDF files found: {len(pdfs)}")
    print(f"Created: {created}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
