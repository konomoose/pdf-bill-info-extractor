from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bill_extractor.pdf_redaction import (
    PDFRedactionError,
    collect_pdfs,
    find_redaction_matches,
    institution_for,
    load_rules,
    redact_pdf_with_rules,
    roots_overlap,
)


DEFAULT_SOURCE_ROOT = Path("editable_input")
DEFAULT_OUTPUT_ROOT = Path("redacted_input")
DEFAULT_RULES_PATH = Path("config/redaction.local.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively create rule-based redacted PDF copies from "
            "editable_input while preserving relative folder structure. "
            "Source PDFs are never modified."
        )
    )

    parser.add_argument(
        "source_root",
        nargs="?",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help=(
            "Folder containing editable PDFs. "
            "Default: editable_input"
        ),
    )

    parser.add_argument(
        "output_root",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=(
            "Destination root for redacted PDFs. "
            "Default: redacted_input"
        ),
    )

    parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES_PATH,
        help=(
            "JSON redaction rules file. "
            "Default: config/redaction.local.json"
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Replace existing redacted output. Use after reviewing "
            "changes to the source PDF or redaction rules."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report configured redaction matches without creating "
            "or modifying any PDF files."
        ),
    )

    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    rules_path = args.rules.resolve()

    if roots_overlap(source_root, output_root):
        print(
            "ERROR: Source and output folders must be separate "
            "and must not contain one another.",
            file=sys.stderr,
        )
        return 1

    try:
        rules = load_rules(rules_path)
        pdfs = collect_pdfs(source_root)
    except PDFRedactionError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"Source root: {source_root}")
    print(f"Output root: {output_root}")
    print(f"Rules: {rules_path}")
    print(f"PDF files found: {len(pdfs)}")

    created = 0
    already_clean = 0
    no_rules = 0
    skipped = 0
    failed = 0
    would_redact = 0

    for source in pdfs:
        relative = source.relative_to(source_root)

        print("=" * 72)
        print(f"Processing: {relative}")

        institution = institution_for(
            source,
            source_root,
        )

        terms = rules.terms_for(institution)

        if args.dry_run:
            if not terms:
                no_rules += 1
                print(f"NO RULES: {relative}")
                print(
                    f"         No redaction terms configured for "
                    f"institution {institution!r}."
                )
                continue

            try:
                matches = find_redaction_matches(
                    source,
                    terms,
                )
            except Exception as exc:
                failed += 1
                print(f"FAILED: {relative}")
                print(f"        {exc}")
                continue

            total_matches = sum(matches.values())

            if total_matches == 0:
                already_clean += 1
                print(f"ALREADY CLEAN: {relative}")
            else:
                would_redact += 1
                print(f"WOULD REDACT: {relative}")

            print(f"         Configured terms: {len(matches)}")

            for index, count in enumerate(matches.values(), start=1):
                print(f"         Term {index} matches: {count}")

            print(f"         Total matches: {total_matches}")
            continue

        try:
            result = redact_pdf_with_rules(
                source,
                source_root,
                output_root,
                rules,
                force=args.force,
            )
        except Exception as exc:
            failed += 1
            print(f"FAILED: {relative}")
            print(f"        {exc}")
            continue

        if result.status == "created":
            created += 1
            print(f"CREATED: {relative}")
        elif result.status == "already_clean":
            already_clean += 1
            print(f"ALREADY CLEAN: {relative}")
        elif result.status == "no_rules":
            no_rules += 1
            print(f"NO RULES: {relative}")
        else:
            skipped += 1
            print(f"SKIPPED: {relative}")

        print(f"         {result.message}")

        if result.destination is not None:
            print(f"         {result.destination}")

    print("=" * 72)
    print("SUMMARY")
    print(f"PDF files found: {len(pdfs)}")

    if args.dry_run:
        print(f"Would redact: {would_redact}")
        print(f"Already clean: {already_clean}")
        print(f"No rules: {no_rules}")
    else:
        print(f"Created: {created}")
        print(f"Already clean: {already_clean}")
        print(f"No rules: {no_rules}")
        print(f"Skipped existing: {skipped}")

    print(f"Failed: {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
