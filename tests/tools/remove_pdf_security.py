from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bill_extractor.pdf_security import (
    PDFPasswordRequiredError,
    PDFSecurityError,
    collect_pdfs,
    remove_pdf_security,
)


DEFAULT_SOURCE_ROOT = Path("source_input")
DEFAULT_OUTPUT_ROOT = Path("editable_input")


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
            "Folder containing original/source PDFs. "
            "Default: source_input"
        ),
    )

    parser.add_argument(
        "output_root",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=(
            "Destination root for editable, unsecured PDFs. "
            "Default: editable_input"
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
    except PDFSecurityError as exc:
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
                result = remove_pdf_security(
                    source,
                    source_root,
                    output_root,
                    password=password,
                    force=args.force,
                )

            except PDFPasswordRequiredError:
                if password is None:
                    password = getpass.getpass(
                        f"Password for {relative}: "
                    )

                    result = remove_pdf_security(
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
