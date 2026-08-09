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
class StructuredRedactionOptions:
    etransfer_keep_first_name_only: bool = False

    @property
    def has_rules(self) -> bool:
        return self.etransfer_keep_first_name_only


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


@dataclass(frozen=True)
class PositionedWord:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int
    word: int


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


def _positioned_words(
    page: fitz.Page,
) -> list[PositionedWord]:
    return [
        PositionedWord(
            x0=float(word[0]),
            y0=float(word[1]),
            x1=float(word[2]),
            y1=float(word[3]),
            text=str(word[4]),
            block=int(word[5]),
            line=int(word[6]),
            word=int(word[7]),
        )
        for word in page.get_text("words")
    ]


def _word_token(
    word: PositionedWord,
) -> str:
    return word.text.strip().casefold().rstrip(":")


def _table_geometry(
    words: list[PositionedWord],
) -> tuple[float, float, float, float] | None:
    transaction_words = [
        word
        for word in words
        if _word_token(word) == "transaction"
    ]
    description_words = [
        word
        for word in words
        if _word_token(word) == "description"
    ]
    amount_words = [
        word
        for word in words
        if _word_token(word).startswith("amount")
    ]
    balance_words = [
        word
        for word in words
        if _word_token(word).startswith("balance")
    ]

    for description in description_words:
        same_line_transaction = [
            word
            for word in transaction_words
            if abs(word.y0 - description.y0) <= 3.0
        ]
        description_label_transaction = [
            word
            for word in same_line_transaction
            if word.x0 < description.x0
        ]
        same_line_amount = [
            word
            for word in amount_words
            if abs(word.y0 - description.y0) <= 3.0
        ]
        same_line_balance = [
            word
            for word in balance_words
            if abs(word.y0 - description.y0) <= 3.0
        ]

        if (
            not same_line_transaction
            or not description_label_transaction
            or not same_line_amount
        ):
            continue

        date_left = min(
            word.x0
            for word in same_line_transaction
        )
        description_left = max(
            word.x0
            for word in description_label_transaction
        )
        amount_left = min(
            word.x0
            for word in same_line_amount
        )
        balance_left = (
            min(word.x0 for word in same_line_balance)
            if same_line_balance
            else amount_left + 80.0
        )

        return (
            date_left,
            description_left,
            amount_left,
            balance_left,
        )

    return None


def _is_description_word(
    word: PositionedWord,
    *,
    description_left: float,
    amount_left: float,
) -> bool:
    return (
        word.x0 >= description_left - 2.0
        and word.x0 < amount_left - 4.0
    )


def _group_words_by_visual_y(
    words: list[PositionedWord],
    *,
    tolerance: float = 2.0,
) -> list[tuple[float, list[PositionedWord]]]:
    groups: list[tuple[float, list[PositionedWord]]] = []

    for word in sorted(
        words,
        key=lambda item: (
            item.y0,
            item.x0,
            item.word,
        ),
    ):
        if groups and abs(word.y0 - groups[-1][0]) <= tolerance:
            groups[-1][1].append(word)
            continue

        groups.append(
            (
                word.y0,
                [word],
            )
        )

    return [
        (
            y,
            sorted(
                group_words,
                key=lambda word: (
                    word.x0,
                    word.word,
                ),
            ),
        )
        for y, group_words in groups
    ]


def _line_tokens(
    words: list[PositionedWord],
) -> tuple[str, ...]:
    return tuple(
        _word_token(word)
        for word in sorted(
            words,
            key=lambda word: (
                word.x0,
                word.word,
            ),
        )
    )


def _is_description_header_line(
    words: list[PositionedWord],
) -> bool:
    tokens = _line_tokens(words)
    return (
        "transaction" in tokens
        and "description" in tokens
    )


def _is_closing_boundary_line(
    words: list[PositionedWord],
) -> bool:
    tokens = _line_tokens(words)
    return (
        "closing" in tokens
        and "balance" in tokens
    )


def _has_side_column_transaction_signals(
    words: list[PositionedWord],
    *,
    start_y: float,
    end_y: float,
    date_left: float,
    description_left: float,
    amount_left: float,
    balance_left: float,
) -> bool:
    side_words = [
        word
        for word in words
        if word.y0 >= start_y - 2.0
        and word.y0 < end_y - 1.0
    ]
    date_side_words = [
        word
        for word in side_words
        if word.x0 < description_left - 8.0
        and word.x0 >= date_left - 8.0
    ]
    amount_side_words = [
        word
        for word in side_words
        if word.x0 >= amount_left - 8.0
        and word.x0 < balance_left + 120.0
    ]

    return any(
        abs(date_word.y0 - amount_word.y0) <= 3.0
        for date_word in date_side_words
        for amount_word in amount_side_words
    )


def _description_line_groups(
    words: list[PositionedWord],
    *,
    description_left: float,
    amount_left: float,
) -> list[tuple[float, list[PositionedWord]]]:
    description_words = [
        word
        for word in words
        if _is_description_word(
            word,
            description_left=description_left,
            amount_left=amount_left,
        )
    ]

    return _group_words_by_visual_y(description_words)


def _description_start_ys(
    words: list[PositionedWord],
    *,
    date_left: float,
    description_left: float,
    amount_left: float,
    balance_left: float,
) -> list[float]:
    groups = [
        (y, line_words)
        for y, line_words in _description_line_groups(
            words,
            description_left=description_left,
            amount_left=amount_left,
        )
        if not _is_description_header_line(line_words)
        and not _is_closing_boundary_line(line_words)
    ]
    starts: list[float] = []

    for index, (y, _line_words) in enumerate(groups):
        next_y = (
            groups[index + 1][0]
            if index + 1 < len(groups)
            else float("inf")
        )

        if _has_side_column_transaction_signals(
            words,
            start_y=y,
            end_y=next_y,
            date_left=date_left,
            description_left=description_left,
            amount_left=amount_left,
            balance_left=balance_left,
        ):
            starts.append(y)

    return starts


def _closing_boundary_ys(
    words: list[PositionedWord],
    *,
    description_left: float,
    amount_left: float,
) -> list[float]:
    return [
        y
        for y, line_words in _description_line_groups(
            words,
            description_left=description_left,
            amount_left=amount_left,
        )
        if _is_closing_boundary_line(line_words)
    ]


def _description_band_words(
    words: list[PositionedWord],
    *,
    start_y: float,
    end_y: float,
    description_left: float,
    amount_left: float,
) -> list[PositionedWord]:
    return [
        word
        for word in sorted(
            words,
            key=lambda item: (
                item.y0,
                item.x0,
                item.word,
            ),
        )
        if word.y0 >= start_y - 1.0
        and word.y0 < end_y - 1.0
        and _is_description_word(
            word,
            description_left=description_left,
            amount_left=amount_left,
        )
    ]


def _band_for_word(
    word: PositionedWord,
    *,
    start_ys: list[float],
    closing_ys: list[float],
    page_bottom: float,
) -> tuple[float, float] | None:
    current_start = None

    for start_y in start_ys:
        if start_y <= word.y0 + 1.0:
            current_start = start_y
        else:
            break

    if current_start is None:
        return None

    candidates = [
        boundary
        for boundary in (*start_ys, *closing_ys, page_bottom)
        if boundary > current_start + 1.0
    ]

    return (
        current_start,
        min(candidates) if candidates else page_bottom,
    )


def _same_positioned_word(
    left: PositionedWord,
    right: PositionedWord,
) -> bool:
    return (
        left.block == right.block
        and left.line == right.line
        and left.word == right.word
        and left.text == right.text
        and abs(left.x0 - right.x0) <= 0.01
        and abs(left.y0 - right.y0) <= 0.01
    )


def _positioned_word_index(
    words: list[PositionedWord],
    target: PositionedWord,
) -> int | None:
    for index, word in enumerate(words):
        if _same_positioned_word(word, target):
            return index

    return None


def _redaction_rect_for_word(
    word: PositionedWord,
) -> fitz.Rect:
    height = word.y1 - word.y0
    vertical_inset = height * 0.18

    return fitz.Rect(
        word.x0,
        word.y0 + vertical_inset,
        word.x1,
        word.y1 - vertical_inset,
    )


def _redact_etransfer_names_on_page(
    page: fitz.Page,
) -> int:
    words = _positioned_words(page)
    geometry = _table_geometry(words)

    if geometry is None:
        return 0

    date_left, description_left, amount_left, balance_left = geometry
    start_ys = _description_start_ys(
        words,
        date_left=date_left,
        description_left=description_left,
        amount_left=amount_left,
        balance_left=balance_left,
    )

    if not start_ys:
        return 0

    closing_ys = _closing_boundary_ys(
        words,
        description_left=description_left,
        amount_left=amount_left,
    )
    description_words = _description_band_words(
        words,
        start_y=start_ys[0],
        end_y=page.rect.y1 + 1.0,
        description_left=description_left,
        amount_left=amount_left,
    )
    redaction_count = 0
    index = 0

    while index <= len(description_words) - 4:
        current = description_words[index:index + 4]
        tokens = [
            _word_token(word)
            for word in current
        ]

        if (
            tokens[0] == "interac"
            and tokens[1] == "e-transfer"
            and tokens[2] in ("from", "to")
        ):
            band = _band_for_word(
                current[0],
                start_ys=start_ys,
                closing_ys=closing_ys,
                page_bottom=page.rect.y1 + 1.0,
            )

            if band is None:
                index += 1
                continue

            start_y, end_y = band
            band_words = _description_band_words(
                words,
                start_y=start_y,
                end_y=end_y,
                description_left=description_left,
                amount_left=amount_left,
            )

            prefix_position = _positioned_word_index(
                band_words,
                current[2],
            )

            if prefix_position is None:
                index += 1
                continue

            name_words = band_words[prefix_position + 1:]

            for word in name_words[1:]:
                page.add_redact_annot(
                    _redaction_rect_for_word(word),
                    fill=(0, 0, 0),
                    cross_out=False,
                )
                redaction_count += 1

            index += max(
                len(name_words),
                1,
            )
            continue

        index += 1

    return redaction_count


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
    structured_options: StructuredRedactionOptions | None = None,
) -> PDFRedactionResult:
    source = source.resolve()
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    terms = normalize_redaction_terms(tuple(terms))
    structured_options = (
        structured_options
        if structured_options is not None
        else StructuredRedactionOptions()
    )

    destination = output_path_for(
        source,
        source_root,
        output_root,
    )

    if not terms and not structured_options.has_rules:
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
                    page_redactions = 0

                if structured_options.etransfer_keep_first_name_only:
                    structured_redactions = (
                        _redact_etransfer_names_on_page(page)
                    )
                    page_redactions += structured_redactions
                    redaction_count += structured_redactions

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
    structured_options: StructuredRedactionOptions | None = None,
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
        structured_options=structured_options,
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
