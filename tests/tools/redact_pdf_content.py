from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


DEFAULT_SOURCE_ROOT = Path("editable_input")
DEFAULT_OUTPUT_ROOT = Path("redacted_input")
DEFAULT_RULES_PATH = Path("config/redaction.local.json")


@dataclass(frozen=True)
class RedactionRules:
    global_terms: tuple[str, ...]
    institution_terms: dict[str, tuple[str, ...]]

    def terms_for(self, institution: str) -> tuple[str, ...]:
        terms = list(self.global_terms)
        terms.extend(self.institution_terms.get(institution, ()))

        # Preserve order while removing duplicates case-insensitively.
        unique: list[str] = []
        seen: set[str] = set()

        for term in terms:
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(term)

        return tuple(unique)


@dataclass(frozen=True)
class ProcessResult:
    source: Path
    destination: Path | None
    status: str
    message: str
    redaction_count: int = 0


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_terms(
    values: object,
    *,
    label: str,
) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise RuntimeError(
            f"{label} must be a JSON array of strings."
        )

    normalized: list[str] = []

    for value in values:
        if not isinstance(value, str):
            raise RuntimeError(
                f"{label} contains a non-string value."
            )

        term = value.strip()

        if not term:
            raise RuntimeError(
                f"{label} contains an empty redaction term."
            )

        normalized.append(term)

    return tuple(normalized)


def load_rules(path: Path) -> RedactionRules:
    if not path.is_file():
        raise RuntimeError(
            f"Redaction rules file not found: {path}\n"
            "Create config/redaction.local.json from "
            "config/redaction.example.json."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON in redaction rules file: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Redaction rules must contain a JSON object."
        )

    global_terms = normalize_terms(
        data.get("global_terms", []),
        label="global_terms",
    )

    raw_institutions = data.get("institution_terms", {})

    if not isinstance(raw_institutions, dict):
        raise RuntimeError(
            "institution_terms must be a JSON object."
        )

    institution_terms: dict[str, tuple[str, ...]] = {}

    for institution, values in raw_institutions.items():
        if not isinstance(institution, str) or not institution.strip():
            raise RuntimeError(
                "institution_terms contains an invalid institution name."
            )

        key = institution.strip()

        institution_terms[key] = normalize_terms(
            values,
            label=f"institution_terms.{key}",
        )

    return RedactionRules(
        global_terms=global_terms,
        institution_terms=institution_terms,
    )


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
            raise RuntimeError(
                "Redacted output is encrypted."
            )

        if document.page_count != expected_page_count:
            raise RuntimeError(
                "Page count changed during redaction."
            )

        for page_number, page in enumerate(document, start=1):
            for term in terms:
                if page.search_for(term):
                    raise RuntimeError(
                        f"Redaction term still exists on page "
                        f"{page_number}: {term!r}"
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
                raise RuntimeError(
                    f"Metadata field was not cleared: {key}"
                )

        if document.embfile_count() != 0:
            raise RuntimeError(
                "Embedded files remain in redacted output."
            )

def find_redaction_matches(
    source: Path,
    terms: tuple[str, ...],
) -> dict[str, int]:
    matches = {term: 0 for term in terms}

    with fitz.open(source) as document:
        if document.needs_pass:
            raise RuntimeError(
                "Source PDF requires a password. "
                "Run PDF security removal first."
            )

        for page in document:
            for term in terms:
                matches[term] += len(page.search_for(term))

    return matches

def process_pdf(
    source: Path,
    source_root: Path,
    output_root: Path,
    rules: RedactionRules,
    *,
    force: bool,
) -> ProcessResult:
    source = source.resolve()
    source_root = source_root.resolve()
    output_root = output_root.resolve()

    institution = institution_for(
        source,
        source_root,
    )

    terms = rules.terms_for(institution)

    destination = output_path_for(
        source,
        source_root,
        output_root,
    )

    if not terms:
        return ProcessResult(
            source=source,
            destination=None,
            status="no_rules",
            message=(
                f"No redaction terms configured for "
                f"institution {institution!r}."
            ),
        )

    if destination.exists() and not force:
        return ProcessResult(
            source=source,
            destination=destination,
            status="skipped",
            message=(
                "Redacted output already exists. "
                "Use --force after changing the source or rules."
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
                raise RuntimeError(
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
                    # Applying redactions removes the underlying content,
                    # rather than placing a visual overlay over readable text.
                    page.apply_redactions()

            # Remove document-level material that could disclose information
            # outside the visible page text. Keep hidden/OCR text because it
            # may be needed by regression extraction tests.
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
            raise RuntimeError(
                "Source PDF changed during redaction."
            )

        os.replace(
            temporary,
            destination,
        )

        if redaction_count == 0:
            return ProcessResult(
                source=source,
                destination=destination,
                status="already_clean",
                message=(
                    "Configured redaction terms were already absent; "
                    "verified sanitized copy created."
                ),
                redaction_count=0,
            )

        if redaction_count == 0:
            return ProcessResult(
                source=source,
                destination=destination,
                status="already_clean",
                message=(
                    "Configured redaction terms were already absent; "
                    "verified sanitized copy created."
                ),
                redaction_count=0,
            )

        return ProcessResult(
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
    except RuntimeError as exc:
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

            for term, count in matches.items():
                print(f"         {term!r}: {count}")

            print(f"         Total matches: {total_matches}")
            continue

        try:
            result = process_pdf(
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
