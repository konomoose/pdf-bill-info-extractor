from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF
import pandas as pd

from .profile_loader import (
    DEFAULT_PROFILE_PATH,
    ExtractionProfile,
    ProfileError,
    load_profile,
)
from .statement_metadata import StatementMetadata

logger = logging.getLogger(__name__)

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
DATE_RE = re.compile(rf"^{MONTHS}\s+\d{{1,2}}$")
AMOUNT_RE = re.compile(r"^-?\$?\d[\d,]*\.\d{2}(?:\*+)?$")

RBC_CHQ_DATE_RE = re.compile(
    rf"^(\d{{1,2}})\s+({MONTHS})$",
    re.IGNORECASE,
)

RBC_ROW_RE = re.compile(
    rf"^({MONTHS})\s+(\d{{1,2}})\s+"
    rf"({MONTHS})\s+(\d{{1,2}})\s+"
    r"(.+?)\s+(-?\$?\d[\d,]*\.\d{2}(?:\*+)?)$",
    re.IGNORECASE,
)
RBC_REFERENCE_RE = re.compile(r"^\d{23}$")
RBC_PREVIOUS_BALANCE_RE = re.compile(
    r"Previous\s+Account\s+Balance\s+"
    r"(-?\$?\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)
RBC_TOTAL_BALANCE_RE = re.compile(
    r"Total\s+Account\s+Balance\s+"
    r"(-?\$?\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)

RBC_CHQ_OPENING_BALANCE_RE = re.compile(
    r"Your\s+opening\s+balance\s+on\s+"
    r"[A-Za-z]+\s+\d{1,2},\s+\d{4}\s+"
    r"\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)
RBC_CHQ_TOTAL_DEPOSITS_RE = re.compile(
    r"Total\s+deposits\s+into\s+your\s+account\s+"
    r"\+?\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)
RBC_CHQ_TOTAL_WITHDRAWALS_RE = re.compile(
    r"Total\s+withdrawals\s+from\s+your\s+account\s+"
    r"-?\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)
RBC_CHQ_CLOSING_BALANCE_RE = re.compile(
    r"Your\s+closing\s+balance\s+on\s+"
    r"[A-Za-z]+\s+\d{1,2},\s+\d{4}\s+"
    r"=?\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)
RBC_CHQ_OPENING_DATE_RE = re.compile(
    r"Your\s+opening\s+balance\s+on\s+"
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
RBC_CHQ_CLOSING_DATE_RE = re.compile(
    r"Your\s+closing\s+balance\s+on\s+"
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
SIMPLII_BALANCE_RE = re.compile(
    r"^(?:-\$?\d[\d,]*\.\d{2}|\$?\d[\d,]*\.\d{2}-?)(?:\*+)?$"
)
SIMPLII_TOTAL_OUT_RE = re.compile(
    r"total\s+funds\s+out\s+([\d,]+\.\d{2})", re.IGNORECASE
)
SIMPLII_TOTAL_IN_RE = re.compile(
    r"total\s+funds\s+in\s+([\d,]+\.\d{2})", re.IGNORECASE
)
SIMPLII_STATEMENT_PERIOD_RE = re.compile(
    r"statement\s+period:\s*"
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4})\s*-\s*"
    r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
TRIANGLE_DATE_RE = re.compile(
    rf"^{MONTHS}\s*\d{{1,2}}$",
    re.IGNORECASE,
)
TRIANGLE_AMOUNT_RE = re.compile(
    r"^(?:-\$?\d[\d,]*\.\d{2}|\$?\d[\d,]*\.\d{2}-?)(?:\*+)?$"
)

TD_DATE_RE = re.compile(rf"^{MONTHS}\s*\d{{1,2}}$", re.IGNORECASE)
TD_PREVIOUS_BALANCE_RE = re.compile(
    r"previous\s+statement\s+balance\s+\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)
TD_NEW_BALANCE_RE = re.compile(
    r"total\s+new\s+balance\s+\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

RBC_LOC_PRINCIPAL_BALANCE_RE = re.compile(
    r"Principal\s+balance\s+on\s+"
    r"[A-Za-z]+\s+\d{1,2},\s+\d{4}\s+"
    r"\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

RBC_LOC_WITHDRAWALS_RE = re.compile(
    r"Sum\s+of\s+withdrawals\s+including\s+adjustments\s+"
    r"on\s+your\s+account\s+"
    r"-?\s*\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

RBC_LOC_PAYMENTS_RE = re.compile(
    r"Sum\s+of\s+payments\s+including\s+adjustments\s+"
    r"on\s+your\s+account\s+"
    r"\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

RBC_LOC_INTEREST_RE = re.compile(
    r"Total\s+interest\s+costs\s+including\s+adjustments\s+"
    r"on\s+your\s+account\s+"
    r"\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

RBC_LOC_FEES_RE = re.compile(
    r"Total\s+fees\s+including\s+adjustments\s+"
    r"on\s+your\s+account\s+"
    r"\$?([\d,]+\.\d{2})",
    re.IGNORECASE,
)

SUPPORTED_PARSERS = {
    "cibc_credit_card",
    "simplii_chequing_account",
    "td_visa_credit_card",
    "rbc_visa_credit_card",
    "rbc_chequing_account",
    "rbc_loc",
    "triangle_mastercard",
    "capital_one_mastercard",
}


class PDFProcessingError(RuntimeError):
    """Raised when a statement cannot be processed reliably."""


@dataclass(frozen=True)
class ExtractionResult:
    transactions: pd.DataFrame
    source_pages: tuple[int, ...]
    ghostscript_path: str | None
    metadata: StatementMetadata | None = None


@dataclass(frozen=True)
class BatchFileResult:
    pdf_file: Path
    status: str
    transaction_count: int
    source_pages: tuple[int, ...]
    output_csv: Path | None
    error: str | None


@dataclass(frozen=True)
class BatchResult:
    files: tuple[BatchFileResult, ...]
    summary_csv: Path
    profile_id: str
    profile_name: str

    @property
    def successful_count(self) -> int:
        return sum(item.status == "Success" for item in self.files)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "Failed" for item in self.files)

    @property
    def transaction_count(self) -> int:
        return sum(item.transaction_count for item in self.files)


class VisaPDFProcessor:
    """Extract transactions using the parser selected by a saved profile."""

    def __init__(
        self,
        profile: ExtractionProfile | None = None,
        profile_path: str | Path | None = None,
        *,
        config_path: str | Path | None = None,
    ) -> None:
        if profile is not None and (profile_path is not None or config_path is not None):
            raise PDFProcessingError(
                "Pass either a loaded profile or a profile path, not both."
            )

        selected_path = profile_path or config_path or DEFAULT_PROFILE_PATH

        try:
            self.profile = profile or load_profile(selected_path)
        except ProfileError as exc:
            raise PDFProcessingError(str(exc)) from exc

        if self.profile.parser not in SUPPORTED_PARSERS:
            raise PDFProcessingError(
                f"Profile '{self.profile.display_name}' requires unsupported parser "
                f"'{self.profile.parser}'."
            )

        self.required_headers = list(self.profile.required_headers)
        self.excluded_page_phrases = list(self.profile.excluded_page_phrases)
        self.line_tolerance = self.profile.line_tolerance
        self.continuation_gap = self.profile.continuation_gap

    def _build_statement_metadata(
        self,
        source_path: Path,
    ) -> StatementMetadata:
        return StatementMetadata(
            source_file=source_path,
            profile_id=self.profile.profile_id,
            institution=self.profile.institution,
            document_type=self.profile.document_type,
        )

    @staticmethod
    def detect_ghostscript() -> str | None:
        """Return the Ghostscript command path when it is available on PATH."""
        return shutil.which("gswin64c") or shutil.which("gs")

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _word_center_x(word: tuple) -> float:
        return (word[0] + word[2]) / 2

    @staticmethod
    def _parse_amount(value: str) -> Decimal:
        cleaned = value.replace("$", "").replace(",", "").rstrip("*")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise PDFProcessingError(f"Invalid monetary amount: {value}") from exc

    def _group_words_into_lines(self, words: Iterable[tuple]) -> list[dict]:
        sorted_words = sorted(
            words, key=lambda word: (((word[1] + word[3]) / 2), word[0])
        )
        lines: list[dict] = []

        for word in sorted_words:
            y_center = (word[1] + word[3]) / 2

            if not lines or abs(y_center - lines[-1]["y_center"]) > self.line_tolerance:
                lines.append({"y_center": y_center, "words": [word]})
                continue

            lines[-1]["words"].append(word)
            lines[-1]["y_center"] = sum(
                (item[1] + item[3]) / 2 for item in lines[-1]["words"]
            ) / len(lines[-1]["words"])

        for line in lines:
            line["words"].sort(key=lambda word: word[0])

        return lines

    def _page_is_excluded(self, page_text: str) -> bool:
        if not self.excluded_page_phrases:
            return False

        lowered = page_text.lower()
        return all(
            phrase.lower() in lowered
            for phrase in self.excluded_page_phrases
        )

    # CIBC credit-card parser -------------------------------------------------

    def _find_cibc_transaction_header(self, page: fitz.Page) -> dict | None:
        lines = self._group_words_into_lines(page.get_text("words"))

        for index, line in enumerate(lines):
            line_text = " ".join(word[4] for word in line["words"]).lower()

            if "description" not in line_text or "amount" not in line_text:
                continue

            allow_missing_spend = self.profile.profile_id.startswith("simplii_visa")

            has_spend_header = (
                "spend" in line_text
                and "categories" in line_text
            )

            if not has_spend_header and not allow_missing_spend:
                continue

            nearby_lines = lines[max(0, index - 1) : index + 1]
            nearby_words = [
                word for nearby_line in nearby_lines for word in nearby_line["words"]
            ]
            nearby_text = " ".join(word[4] for word in nearby_words).lower()

            if not all(token in nearby_text for token in ("trans", "post", "date")):
                continue

            def first_word(token: str) -> tuple | None:
                matches = [word for word in nearby_words if word[4].lower() == token]
                return min(matches, key=lambda word: word[0]) if matches else None

            trans = first_word("trans")
            post = first_word("post")
            description = first_word("description")
            spend = first_word("spend")
            amount = next(
                (
                    word
                    for word in nearby_words
                    if word[4].lower().replace(" ", "") in {"amount($)", "amount"}
                ),
                None,
            )

            if not all((trans, post, description, amount)):
                continue

            if spend is None and not allow_missing_spend:
                continue

            x_positions = [trans[0], post[0], description[0]]

            if spend is not None:
                x_positions.append(spend[0])

            x_positions.append(amount[0])

            if x_positions != sorted(x_positions):
                continue

            return {
                "trans": trans[0],
                "post": post[0],
                "description": description[0],
                "spend": spend[0] if spend is not None else None,
                "amount": amount[0],
                "bottom": max(word[3] for word in nearby_words),
            }

        return None

    def _line_to_cibc_cells(self, line: dict, header: dict) -> dict[str, str]:
        cells = {
            "trans": [],
            "post": [],
            "description": [],
            "spend": [],
            "amount": [],
        }

        for word in line["words"]:
            x_position = word[0]
            text = word[4]

            if text == "Ý":
                continue

            if x_position < header["post"]:
                cells["trans"].append(text)
            elif x_position < header["description"]:
                cells["post"].append(text)
            elif header["spend"] is None:
                if x_position < header["amount"]:
                    cells["description"].append(text)
                else:
                    cells["amount"].append(text)
            elif x_position < header["spend"]:
                cells["description"].append(text)
            elif x_position < header["amount"]:
                cells["spend"].append(text)
            else:
                cells["amount"].append(text)

        return {
            key: self._normalize_text(" ".join(parts)) for key, parts in cells.items()
        }

    def _extract_cibc_page_transactions(
        self, page: fitz.Page
    ) -> list[dict[str, str]]:
        page_text = page.get_text("text")

        if self._page_is_excluded(page_text):
            logger.info(
                "Ignoring excluded report page %s for profile %s.",
                page.number + 1,
                self.profile.profile_id,
            )
            return []

        header = self._find_cibc_transaction_header(page)
        if header is None:
            return []

        candidate_words = [
            word for word in page.get_text("words") if word[1] >= header["bottom"] - 1
        ]
        lines = self._group_words_into_lines(candidate_words)

        rows: list[dict[str, str]] = []
        current_row: dict[str, str] | None = None
        last_row_y: float | None = None
        table_started = False

        for line in lines:
            y_center = line["y_center"]
            if y_center <= header["bottom"] + 1:
                continue

            cells = self._line_to_cibc_cells(line, header)
            joined_text = self._normalize_text(" ".join(cells.values()))
            lowered = joined_text.lower()

            if not joined_text:
                continue
            if lowered.startswith("card number"):
                continue
            if lowered.startswith("total for"):
                break

            is_transaction = bool(
                DATE_RE.fullmatch(cells["trans"])
                and DATE_RE.fullmatch(cells["post"])
                and AMOUNT_RE.fullmatch(cells["amount"])
            )

            if is_transaction:
                table_started = True
                current_row = {
                    "Trans date": cells["trans"],
                    "Post date": cells["post"],
                    "Description": cells["description"],
                    "Spend Categories": cells["spend"],
                    "Amount($)": cells["amount"].replace("$", "").rstrip("*"),
                }
                rows.append(current_row)
                last_row_y = y_center
                continue

            if not table_started or current_row is None or last_row_y is None:
                continue
            if y_center - last_row_y > self.continuation_gap:
                break

            is_continuation = (
                not cells["trans"] and not cells["post"] and not cells["amount"]
            )
            if not is_continuation:
                continue

            if cells["description"]:
                current_row["Description"] = self._normalize_text(
                    f'{current_row["Description"]} {cells["description"]}'
                )
            if cells["spend"]:
                current_row["Spend Categories"] = self._normalize_text(
                    f'{current_row["Spend Categories"]} {cells["spend"]}'
                )

            last_row_y = y_center

        return rows

    # Triangle Mastercard parser ---------------------------------------------

    def _find_triangle_transaction_headers(
        self,
        page: fitz.Page,
    ) -> list[dict]:
        lines = self._group_words_into_lines(page.get_text("words"))
        headers: list[dict] = []

        for index, line in enumerate(lines):
            words = line["words"]

            transaction_words = [
                word for word in words
                if word[4].lower() == "transaction"
            ]
            posting_words = [
                word for word in words
                if word[4].lower() == "posting"
            ]

            if not transaction_words or not posting_words:
                continue

            trans = min(transaction_words, key=lambda word: word[0])
            post = min(posting_words, key=lambda word: word[0])

            if trans[0] >= post[0]:
                continue

            for detail_index in range(
                index + 1,
                min(index + 3, len(lines)),
            ):
                detail_line = lines[detail_index]
                detail_words = detail_line["words"]

                date_words = [
                    word for word in detail_words
                    if word[4].lower() == "date"
                ]
                amount_words = [
                    word for word in detail_words
                    if word[4].lower() == "amount"
                ]

                if len(date_words) < 2 or not amount_words:
                    continue

                details_words = [
                    word for word in detail_words
                    if word[4].lower() == "details"
                ]
                description_words = [
                    word for word in detail_words
                    if word[4].lower() == "description"
                ]

                if details_words:
                    description = min(
                        details_words,
                        key=lambda word: word[0],
                    )
                elif description_words:
                    description_word = min(
                        description_words,
                        key=lambda word: word[0],
                    )

                    transaction_labels = [
                        word for word in detail_words
                        if (
                            word[4].lower() == "transaction"
                            and post[0] < word[0] < description_word[0]
                        )
                    ]

                    description = (
                        min(
                            transaction_labels,
                            key=lambda word: word[0],
                        )
                        if transaction_labels
                        else description_word
                    )
                else:
                    continue

                amount = min(
                    amount_words,
                    key=lambda word: word[0],
                )

                x_positions = [
                    trans[0],
                    post[0],
                    description[0],
                    amount[0],
                ]

                if x_positions != sorted(x_positions):
                    continue

                combined_words = words + detail_words

                headers.append(
                    {
                        "trans": trans[0],
                        "post": post[0],
                        "description": description[0],
                        "amount": amount[0],
                        "top": min(
                            word[1] for word in combined_words
                        ),
                        "bottom": max(
                            word[3] for word in combined_words
                        ),
                    }
                )
                break

        return headers

    def _line_to_triangle_cells(
        self,
        line: dict,
        header: dict,
    ) -> dict[str, str]:
        cells = {
            "trans": [],
            "post": [],
            "description": [],
            "amount": [],
        }

        for word in line["words"]:
            x_position = word[0]
            x_end = word[2]
            value = word[4]

            if x_position < header["post"]:
                cells["trans"].append(value)
            elif x_position < header["description"]:
                cells["post"].append(value)
            elif (
                TRIANGLE_AMOUNT_RE.fullmatch(value)
                and x_end >= header["amount"]
            ):
                # Triangle amounts are right-aligned, so the left edge
                # can begin slightly left of the Amount header label.
                cells["amount"].append(value)
            elif x_position < header["amount"]:
                cells["description"].append(value)

        return {
            key: self._normalize_text(" ".join(parts))
            for key, parts in cells.items()
        }

    @staticmethod
    def _normalize_triangle_amount(value: str) -> str:
        value = value.replace("$", "").rstrip("*")

        if value.endswith("-"):
            value = "-" + value[:-1]

        return value

    @staticmethod
    def _normalize_triangle_date(value: str) -> str | None:
        parts = value.split()

        if len(parts) < 2:
            return None

        candidate = f"{parts[0]} {parts[1]}"

        if not TRIANGLE_DATE_RE.fullmatch(candidate):
            return None

        return candidate

    def _extract_triangle_page_transactions(
        self,
        page: fitz.Page,
    ) -> list[dict[str, str]]:
        headers = self._find_triangle_transaction_headers(page)

        if not headers:
            return []

        lines = self._group_words_into_lines(page.get_text("words"))
        rows: list[dict[str, str]] = []

        for index, header in enumerate(headers):
            section_end = (
                headers[index + 1]["top"]
                if index + 1 < len(headers)
                else float("inf")
            )

            current_row: dict[str, str] | None = None
            last_row_y: float | None = None

            for line in lines:
                y_center = line["y_center"]

                if y_center <= header["bottom"] + 1:
                    continue

                if y_center >= section_end - 1:
                    break

                cells = self._line_to_triangle_cells(
                    line,
                    header,
                )

                joined_text = self._normalize_text(
                    " ".join(cells.values())
                )
                lowered = joined_text.lower()

                if not joined_text:
                    continue

                if lowered.startswith("total"):
                    current_row = None
                    last_row_y = None
                    continue

                transaction_date = self._normalize_triangle_date(
                    cells["trans"]
                )
                posting_date = self._normalize_triangle_date(
                    cells["post"]
                )

                is_transaction = bool(
                    transaction_date
                    and posting_date
                    and TRIANGLE_AMOUNT_RE.fullmatch(cells["amount"])
                )

                if is_transaction:
                    current_row = {
                        "Transaction date": transaction_date,
                        "Posting date": posting_date,
                        "Activity description": cells["description"],
                        "Amount($)": self._normalize_triangle_amount(
                            cells["amount"]
                        ),
                    }
                    rows.append(current_row)
                    last_row_y = y_center
                    continue

                if current_row is None or last_row_y is None:
                    continue

                if y_center - last_row_y > self.continuation_gap:
                    continue

                if (
                    not cells["trans"]
                    and not cells["post"]
                    and not cells["amount"]
                    and cells["description"]
                ):
                    current_row["Activity description"] = (
                        self._normalize_text(
                            current_row["Activity description"]
                            + " "
                            + cells["description"]
                        )
                    )
                    last_row_y = y_center

        return rows


    # Capital One Mastercard parser ------------------------------------------

    def _find_capital_one_modern_headers(
        self,
        page: fitz.Page,
    ) -> list[dict]:
        """Find modern Capital One transaction-table headers."""
        lines = self._group_words_into_lines(page.get_text("words"))
        headers: list[dict] = []

        for index in range(len(lines)):
            # Allow the PDF producer to split a visual header across a few
            # closely spaced text lines.
            for window_size in range(1, 4):
                window = lines[index : index + window_size]
                if len(window) != window_size:
                    continue

                words = [
                    word
                    for line in window
                    for word in line["words"]
                ]
                tokens = [
                    str(word[4]).strip().lower()
                    for word in words
                ]

                if not all(
                    token in tokens
                    for token in (
                        "transaction",
                        "posting",
                        "description",
                        "amount",
                    )
                ):
                    continue

                if tokens.count("date") < 2:
                    continue

                transaction_candidates = [
                    word
                    for word in words
                    if str(word[4]).strip().lower() == "transaction"
                    and word[0] < 90
                ]
                posting_candidates = [
                    word
                    for word in words
                    if str(word[4]).strip().lower() == "posting"
                    and 100 <= word[0] < 190
                ]
                description_candidates = [
                    word
                    for word in words
                    if str(word[4]).strip().lower() == "description"
                    and 175 <= word[0] < 320
                ]
                amount_candidates = [
                    word
                    for word in words
                    if str(word[4]).strip().lower() == "amount"
                    and word[0] > 450
                ]

                if not all(
                    (
                        transaction_candidates,
                        posting_candidates,
                        description_candidates,
                        amount_candidates,
                    )
                ):
                    continue

                transaction = min(
                    transaction_candidates,
                    key=lambda word: word[0],
                )
                posting = min(
                    posting_candidates,
                    key=lambda word: word[0],
                )
                description = min(
                    description_candidates,
                    key=lambda word: word[0],
                )
                amount = min(
                    amount_candidates,
                    key=lambda word: word[0],
                )

                anchors = [
                    transaction[0],
                    posting[0],
                    description[0],
                    amount[0],
                ]
                if anchors != sorted(anchors):
                    continue

                header = {
                    "trans": transaction[0],
                    "post": posting[0],
                    "description": description[0],
                    "amount": amount[0],
                    "top": min(word[1] for word in words),
                    "bottom": max(word[3] for word in words),
                }

                # Do not add the same visual header more than once when
                # overlapping windows describe it.
                if not headers or abs(
                    header["top"] - headers[-1]["top"]
                ) > self.line_tolerance:
                    headers.append(header)

                break

        return headers

    def _line_to_capital_one_modern_cells(
        self,
        line: dict,
        header: dict,
    ) -> dict[str, str]:
        cells = {
            "trans": [],
            "post": [],
            "description": [],
            "amount": [],
        }

        for word in line["words"]:
            text = str(word[4])
            if text == "Ý":
                continue

            x_center = self._word_center_x(word)

            if x_center < header["post"]:
                cells["trans"].append(text)
            elif x_center < header["description"]:
                cells["post"].append(text)
            elif x_center < header["amount"]:
                cells["description"].append(text)
            else:
                cells["amount"].append(text)

        return {
            key: self._normalize_text(" ".join(parts))
            for key, parts in cells.items()
        }

    @staticmethod
    def _normalize_capital_one_date(value: str) -> str | None:
        candidate = " ".join(
            value.replace(",", " ").split()
        )

        match = re.fullmatch(
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
            r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
            r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
            r"Dec(?:ember)?)\s+(\d{1,2})",
            candidate,
            re.IGNORECASE,
        )
        if match is None:
            return None

        day = int(match.group(2))
        if not 1 <= day <= 31:
            return None

        month = match.group(1)[:3].title()
        return f"{month} {day}"

    @staticmethod
    def _normalize_capital_one_amount(value: str) -> str | None:
        candidate = "".join(value.split()).rstrip("*")
        if not candidate:
            return None

        negative = False

        if candidate.startswith("(") and candidate.endswith(")"):
            negative = True
            candidate = candidate[1:-1]

        if candidate.upper().endswith("CR"):
            negative = True
            candidate = candidate[:-2]

        if candidate.endswith("-"):
            negative = True
            candidate = candidate[:-1]

        if candidate.startswith("-"):
            negative = True
            candidate = candidate[1:]
        elif candidate.startswith("+"):
            candidate = candidate[1:]

        candidate = candidate.replace("$", "")

        if not re.fullmatch(
            r"\d[\d,]*\.\d{2}",
            candidate,
        ):
            return None

        candidate = candidate.replace(",", "")

        return f"-{candidate}" if negative else candidate


    @staticmethod
    def _parse_capital_one_legacy_date_tokens(
        first_day: str,
        joined_token: str,
        second_month: str,
    ) -> tuple[str, str] | None:
        """
        Reconstruct the two dates used by the legacy Capital One layout.

        PyMuPDF joins the first date's month and the second date's day
        into one word, producing a visual sequence similar to:

            DAY | MONTH+DAY | MONTH

        Example structure only:
            02 | MAR03 | MAR
        """
        first_day = first_day.strip()
        joined_token = joined_token.strip()
        second_month = second_month.strip()

        if not re.fullmatch(r"\d{1,2}", first_day):
            return None

        joined_match = re.fullmatch(
            r"([A-Za-z]{3})[^A-Za-z0-9]*(\d{1,2})",
            joined_token,
        )
        if joined_match is None:
            return None

        if not re.fullmatch(r"[A-Za-z]{3}", second_month):
            return None

        first_month = joined_match.group(1)
        second_day = joined_match.group(2)

        first_date = VisaPDFProcessor._normalize_capital_one_date(
            f"{first_month} {first_day}"
        )
        posting_date = VisaPDFProcessor._normalize_capital_one_date(
            f"{second_month} {second_day}"
        )

        if first_date is None or posting_date is None:
            return None

        return first_date, posting_date

    def _extract_capital_one_legacy_page_transactions(
        self,
        page: fitz.Page,
    ) -> list[dict[str, str]]:
        """Extract Capital One's legacy 2020 / early-2021 transaction layout."""
        lines = self._group_words_into_lines(page.get_text("words"))
        rows: list[dict[str, str]] = []

        for line in lines:
            words = line["words"]

            if len(words) < 4:
                continue

            # The legacy transaction rows occupy the left side of the page:
            #
            #   first day      x ~ 40-45
            #   month+day      x ~ 55-65
            #   second month   x ~ 75-85
            #   description    begins after the date fields
            #   amount         x ~ 265-285
            #
            # Use word centres rather than raw x0 values because the token
            # widths vary slightly between statements.
            positioned = [
                (
                    self._word_center_x(word),
                    str(word[4]).strip(),
                    word,
                )
                for word in words
                if str(word[4]).strip()
            ]

            first_day_candidates = [
                (x, value, word)
                for x, value, word in positioned
                if 30 <= x <= 55
                and re.fullmatch(r"\d{1,2}", value)
            ]

            joined_date_candidates = [
                (x, value, word)
                for x, value, word in positioned
                if 45 <= x <= 75
                and re.fullmatch(
                    r"[A-Za-z]{3}[^A-Za-z0-9]*\d{1,2}",
                    value,
                )
            ]

            second_month_candidates = [
                (x, value, word)
                for x, value, word in positioned
                if 65 <= x <= 100
                and re.fullmatch(r"[A-Za-z]{3}", value)
            ]

            if not all(
                (
                    first_day_candidates,
                    joined_date_candidates,
                    second_month_candidates,
                )
            ):
                continue

            first_day_x, first_day, first_day_word = (
                first_day_candidates[0]
            )
            joined_x, joined_date, joined_word = (
                joined_date_candidates[0]
            )
            second_month_x, second_month, second_month_word = (
                second_month_candidates[0]
            )

            if not (
                first_day_x
                < joined_x
                < second_month_x
            ):
                continue

            dates = self._parse_capital_one_legacy_date_tokens(
                first_day,
                joined_date,
                second_month,
            )
            if dates is None:
                continue

            transaction_date, posting_date = dates

            amount_candidates: list[
                tuple[float, str, tuple]
            ] = []

            for x, value, word in positioned:
                if not 240 <= x <= 315:
                    continue

                normalized = self._normalize_capital_one_amount(value)
                if normalized is None:
                    continue

                amount_candidates.append((x, normalized, word))

            if not amount_candidates:
                continue

            amount_x, amount, amount_word = amount_candidates[-1]

            # Description starts after the second date token and stops before
            # the amount. Do not use anything from the right-side statement
            # panels that can occur on the same visual line.
            description_parts = [
                str(word[4])
                for word in words
                if word[0] > second_month_word[2]
                and word[2] < amount_word[0]
            ]

            description = self._normalize_text(
                " ".join(description_parts)
            )

            if not description:
                continue

            rows.append(
                {
                    "Transaction date": transaction_date,
                    "Posting date": posting_date,
                    "Description": description,
                    "Amount": amount,
                }
            )

        return rows

    def _extract_capital_one_page_transactions(
        self,
        page: fitz.Page,
    ) -> list[dict[str, str]]:
        """Extract the modern Capital One transaction layout."""
        headers = self._find_capital_one_modern_headers(page)

        if not headers:
            return self._extract_capital_one_legacy_page_transactions(
                page
            )

        lines = self._group_words_into_lines(page.get_text("words"))
        rows: list[dict[str, str]] = []

        for index, header in enumerate(headers):
            section_end = (
                headers[index + 1]["top"]
                if index + 1 < len(headers)
                else float("inf")
            )

            current_row: dict[str, str] | None = None
            last_row_y: float | None = None

            for line in lines:
                y_center = line["y_center"]

                if y_center <= header["bottom"] + 1:
                    continue

                if y_center >= section_end - 1:
                    break

                cells = self._line_to_capital_one_modern_cells(
                    line,
                    header,
                )

                if not any(cells.values()):
                    continue

                transaction_date = self._normalize_capital_one_date(
                    cells["trans"]
                )
                posting_date = self._normalize_capital_one_date(
                    cells["post"]
                )
                amount = self._normalize_capital_one_amount(
                    cells["amount"]
                )

                is_transaction = bool(
                    transaction_date
                    and posting_date
                    and cells["description"]
                    and amount is not None
                )

                if is_transaction:
                    current_row = {
                        "Transaction date": transaction_date,
                        "Posting date": posting_date,
                        "Description": cells["description"],
                        "Amount": amount,
                    }
                    rows.append(current_row)
                    last_row_y = y_center
                    continue

                if current_row is None or last_row_y is None:
                    continue

                if y_center - last_row_y > self.continuation_gap:
                    continue

                is_continuation = bool(
                    not cells["trans"]
                    and not cells["post"]
                    and not cells["amount"]
                    and cells["description"]
                )
                if not is_continuation:
                    continue

                current_row["Description"] = self._normalize_text(
                    current_row["Description"]
                    + " "
                    + cells["description"]
                )
                last_row_y = y_center

        return rows

    # Simplii chequing-account parser ----------------------------------------

    def _find_simplii_transaction_header(self, page: fitz.Page) -> dict | None:
        lines = self._group_words_into_lines(page.get_text("words"))

        for index, line in enumerate(lines):
            line_words = line["words"]
            line_text = " ".join(word[4] for word in line_words).lower()

            if "transaction" not in line_text or "balance" not in line_text:
                continue
            if line_text.count("funds") < 2 or "out" not in line_text or "in" not in line_text:
                continue

            nearby_lines = lines[index : index + 2]
            nearby_words = [
                word for nearby_line in nearby_lines for word in nearby_line["words"]
            ]
            nearby_text = " ".join(word[4] for word in nearby_words).lower()
            if not all(token in nearby_text for token in ("trans.", "eff.", "date")):
                continue

            trans = next(
                (word for word in nearby_words if word[4].lower() == "trans."),
                None,
            )
            eff = next(
                (word for word in nearby_words if word[4].lower() == "eff."),
                None,
            )
            transaction = next(
                (word for word in nearby_words if word[4].lower() == "transaction"),
                None,
            )
            funds_words = sorted(
                (word for word in nearby_words if word[4].lower() == "funds"),
                key=lambda word: word[0],
            )
            balance = next(
                (word for word in nearby_words if word[4].lower() == "balance"),
                None,
            )

            if not all((trans, eff, transaction, balance)) or len(funds_words) < 2:
                continue

            funds_out, funds_in = funds_words[:2]
            x_positions = [
                trans[0],
                eff[0],
                transaction[0],
                funds_out[0],
                funds_in[0],
                balance[0],
            ]
            if x_positions != sorted(x_positions):
                continue

            return {
                "trans": trans[0],
                "eff": eff[0],
                "transaction": transaction[0],
                "funds_out": funds_out[0],
                "funds_in": funds_in[0],
                "balance": balance[0],
                "bottom": max(word[3] for word in nearby_words),
            }

        return None

    def _line_to_simplii_cells(self, line: dict, header: dict) -> dict[str, str]:
        cells = {
            "trans": [],
            "eff": [],
            "transaction": [],
            "funds_out": [],
            "funds_in": [],
            "balance": [],
        }

        for word in line["words"]:
            text = word[4]
            if text == "Ý":
                continue

            x_center = self._word_center_x(word)
            if x_center < header["eff"]:
                cells["trans"].append(text)
            elif x_center < header["transaction"]:
                cells["eff"].append(text)
            elif x_center < header["funds_out"]:
                cells["transaction"].append(text)
            elif x_center < header["funds_in"]:
                cells["funds_out"].append(text)
            elif x_center < header["balance"]:
                cells["funds_in"].append(text)
            else:
                cells["balance"].append(text)

        return {
            key: self._normalize_text(" ".join(parts)) for key, parts in cells.items()
        }


    @staticmethod
    def _normalize_simplii_balance(value: str) -> str:
        """Convert Simplii trailing-minus balances to standard signed numbers."""
        cleaned = value.replace("$", "").rstrip("*")
        if cleaned.endswith("-"):
            return f"-{cleaned[:-1]}"
        return cleaned

    def _extract_simplii_page_transactions(
        self, page: fitz.Page
    ) -> list[dict[str, str]]:
        page_text = page.get_text("text")

        if self._page_is_excluded(page_text):
            logger.info(
                "Ignoring excluded report page %s for profile %s.",
                page.number + 1,
                self.profile.profile_id,
            )
            return []

        header = self._find_simplii_transaction_header(page)
        if header is None:
            return []

        candidate_words = [
            word for word in page.get_text("words") if word[1] >= header["bottom"] - 1
        ]
        lines = self._group_words_into_lines(candidate_words)

        rows: list[dict[str, str]] = []
        current_row: dict[str, str] | None = None
        last_row_y: float | None = None
        table_started = False

        for line in lines:
            y_center = line["y_center"]
            if y_center <= header["bottom"] + 1:
                continue

            cells = self._line_to_simplii_cells(line, header)
            joined_text = self._normalize_text(" ".join(cells.values()))
            lowered = joined_text.lower()

            if not joined_text:
                continue
            if lowered.startswith("end of transactions"):
                break
            if lowered.startswith("transactions continue"):
                break
            if lowered.startswith("page "):
                break

            funds_out_valid = bool(AMOUNT_RE.fullmatch(cells["funds_out"]))
            funds_in_valid = bool(AMOUNT_RE.fullmatch(cells["funds_in"]))
            balance_valid = bool(SIMPLII_BALANCE_RE.fullmatch(cells["balance"]))

            is_transaction = bool(
                DATE_RE.fullmatch(cells["trans"])
                and DATE_RE.fullmatch(cells["eff"])
                and cells["transaction"]
                and balance_valid
                and (funds_out_valid ^ funds_in_valid)
            )

            if is_transaction:
                table_started = True
                current_row = {
                    "Trans. date": cells["trans"],
                    "Eff. date": cells["eff"],
                    "Transaction": cells["transaction"],
                    "Funds out": (
                        cells["funds_out"].replace("$", "").rstrip("*")
                        if funds_out_valid
                        else ""
                    ),
                    "Funds in": (
                        cells["funds_in"].replace("$", "").rstrip("*")
                        if funds_in_valid
                        else ""
                    ),
                    "Balance": self._normalize_simplii_balance(cells["balance"]),
                }
                rows.append(current_row)
                last_row_y = y_center
                continue

            # BALANCE FORWARD has dates and a balance but no funds in/out.
            if (
                DATE_RE.fullmatch(cells["trans"])
                and DATE_RE.fullmatch(cells["eff"])
                and cells["transaction"].upper() == "BALANCE FORWARD"
            ):
                table_started = True
                last_row_y = y_center
                continue

            if not table_started or current_row is None or last_row_y is None:
                continue
            if y_center - last_row_y > self.continuation_gap:
                continue

            is_continuation = not any(
                (
                    cells["trans"],
                    cells["eff"],
                    cells["funds_out"],
                    cells["funds_in"],
                    cells["balance"],
                )
            )
            if not is_continuation or not cells["transaction"]:
                continue

            current_row["Transaction"] = self._normalize_text(
                f'{current_row["Transaction"]} {cells["transaction"]}'
            )
            last_row_y = y_center

        return rows

    @staticmethod
    def _extract_simplii_statement_period(
        page_text: str,
    ) -> tuple[date | None, date | None]:
        match = SIMPLII_STATEMENT_PERIOD_RE.search(
            page_text
        )

        if match is None:
            return None, None

        parsed_dates: list[date] = []

        for value in match.groups():
            parsed_date: date | None = None

            for date_format in (
                "%B %d, %Y",
                "%b %d, %Y",
            ):
                try:
                    parsed_date = datetime.strptime(
                        value,
                        date_format,
                    ).date()
                    break
                except ValueError:
                    continue

            if parsed_date is None:
                raise PDFProcessingError(
                    "Invalid Simplii statement-period date: "
                    f"{value!r}."
                )

            parsed_dates.append(parsed_date)

        statement_start, statement_end = parsed_dates

        if statement_start > statement_end:
            raise PDFProcessingError(
                "Simplii statement-period start date is later "
                "than its end date."
            )

        return statement_start, statement_end

    def _validate_simplii_totals(
        self,
        transactions: pd.DataFrame,
        statement_total_out: Decimal | None,
        statement_total_in: Decimal | None,
    ) -> None:
        if statement_total_out is None or statement_total_in is None:
            raise PDFProcessingError(
                "Simplii statement totals were not found. Extraction was not accepted "
                "because completeness could not be verified."
            )

        extracted_out = sum(
            (self._parse_amount(value) for value in transactions["Funds out"] if value),
            Decimal("0.00"),
        )
        extracted_in = sum(
            (self._parse_amount(value) for value in transactions["Funds in"] if value),
            Decimal("0.00"),
        )

        if extracted_out != statement_total_out or extracted_in != statement_total_in:
            raise PDFProcessingError(
                "Simplii extraction totals do not match the statement totals. "
                f"Extracted funds out: ${extracted_out:,.2f}; statement: "
                f"${statement_total_out:,.2f}. Extracted funds in: "
                f"${extracted_in:,.2f}; statement: ${statement_total_in:,.2f}. "
                "The PDF may contain an image-only or otherwise unreadable transaction page."
            )

    # TD Visa credit-card parser ---------------------------------------------

    @staticmethod
    def _normalize_td_date(value: str) -> str:
        compact = re.fullmatch(
            rf"({MONTHS})\s*(\d{{1,2}})",
            value.strip(),
            re.IGNORECASE,
        )
        if compact is None:
            return VisaPDFProcessor._normalize_text(value)
        return f"{compact.group(1).title()} {compact.group(2)}"

    @staticmethod
    def _normalize_td_activity(value: str) -> str:
        normalized = VisaPDFProcessor._normalize_text(value)
        # TD's embedded text layer can encode the visible word FINANCIAL as
        # FfNANCIAL. Keep this repair deliberately narrow.
        return normalized.replace("FfNANCIAL", "FINANCIAL")

    def _find_td_transaction_header(self, page: fitz.Page) -> dict | None:
        lines = self._group_words_into_lines(page.get_text("words"))
        required_tokens = {
            "transaction",
            "posting",
            "activity",
            "description",
            "amount",
            "date",
        }

        # TD places the transaction headings across three visual lines and
        # interleaves a right-side rewards panel between those lines. Search
        # windows up to five lines so the full heading is found reliably.
        for window_size in range(1, 6):
            for index in range(0, len(lines) - window_size + 1):
                nearby_lines = lines[index : index + window_size]
                nearby_words = [
                    word
                    for nearby_line in nearby_lines
                    for word in nearby_line["words"]
                ]
                nearby_text = " ".join(word[4] for word in nearby_words).lower()
                tokens = set(re.findall(r"[a-z]+", nearby_text))

                if not required_tokens.issubset(tokens):
                    continue

                transaction = next(
                    (word for word in nearby_words if word[4].lower() == "transaction"),
                    None,
                )
                posting = next(
                    (word for word in nearby_words if word[4].lower() == "posting"),
                    None,
                )
                activity = next(
                    (word for word in nearby_words if word[4].lower() == "activity"),
                    None,
                )
                amount = next(
                    (
                        word
                        for word in nearby_words
                        if word[4].lower().replace(" ", "")
                        in {"amount($)", "amount"}
                    ),
                    None,
                )

                if not all((transaction, posting, activity, amount)):
                    continue

                x_positions = [
                    transaction[0],
                    posting[0],
                    activity[0],
                    amount[0],
                ]
                if x_positions != sorted(x_positions):
                    continue

                # The transaction table ends just after the amount heading.
                # Everything farther right belongs to TD's rewards/payment
                # panels and must not be mixed into transaction cells.
                table_right = amount[2] + 4.0
                table_header_words = [
                    word for word in nearby_words if self._word_center_x(word) < table_right
                ]

                return {
                    "transaction_date": transaction[0],
                    "posting_date": posting[0],
                    "activity": activity[0],
                    "amount": amount[0],
                    "right": table_right,
                    "bottom": max(word[3] for word in table_header_words),
                }

        return None

    def _line_to_td_cells(self, line: dict, header: dict) -> dict[str, str]:
        cells = {
            "transaction_date": [],
            "posting_date": [],
            "activity": [],
            "amount": [],
        }

        for word in line["words"]:
            text = word[4]
            if text == "Ý":
                continue

            x_center = self._word_center_x(word)
            if x_center >= header["right"]:
                continue

            if x_center < header["posting_date"]:
                cells["transaction_date"].append(text)
            elif x_center < header["activity"]:
                cells["posting_date"].append(text)
            elif x_center < header["amount"]:
                cells["activity"].append(text)
            else:
                cells["amount"].append(text)

        return {
            key: self._normalize_text(" ".join(parts)) for key, parts in cells.items()
        }

    def _extract_td_page_transactions(
        self, page: fitz.Page
    ) -> list[dict[str, str]]:
        page_text = page.get_text("text")

        if self._page_is_excluded(page_text):
            logger.info(
                "Ignoring excluded report page %s for profile %s.",
                page.number + 1,
                self.profile.profile_id,
            )
            return []

        header = self._find_td_transaction_header(page)
        if header is None:
            return []

        candidate_words = [
            word
            for word in page.get_text("words")
            if word[1] >= header["bottom"] - 1
            and self._word_center_x(word) < header["right"]
        ]
        lines = self._group_words_into_lines(candidate_words)

        rows: list[dict[str, str]] = []
        current_row: dict[str, str] | None = None
        last_row_y: float | None = None
        table_started = False

        for line in lines:
            y_center = line["y_center"]
            if y_center <= header["bottom"] + 1:
                continue

            cells = self._line_to_td_cells(line, header)
            joined_text = self._normalize_text(" ".join(cells.values()))
            lowered = joined_text.lower()

            if not joined_text:
                continue
            if "total new balance" in lowered:
                break
            if lowered.startswith("td message centre"):
                break
            if "previous statement balance" in lowered:
                continue

            transaction_date = self._normalize_td_date(cells["transaction_date"])
            posting_date = self._normalize_td_date(cells["posting_date"])
            amount = cells["amount"]

            is_transaction = bool(
                TD_DATE_RE.fullmatch(cells["transaction_date"])
                and TD_DATE_RE.fullmatch(cells["posting_date"])
                and cells["activity"]
                and AMOUNT_RE.fullmatch(amount)
            )

            if is_transaction:
                table_started = True
                current_row = {
                    "Transaction date": transaction_date,
                    "Posting date": posting_date,
                    "Activity description": self._normalize_td_activity(
                        cells["activity"]
                    ),
                    "Amount($)": amount.replace("$", "").rstrip("*"),
                }
                rows.append(current_row)
                last_row_y = y_center
                continue

            if not table_started or current_row is None or last_row_y is None:
                continue
            if y_center - last_row_y > self.continuation_gap:
                continue

            is_continuation = not any(
                (
                    cells["transaction_date"],
                    cells["posting_date"],
                    cells["amount"],
                )
            )
            if not is_continuation or not cells["activity"]:
                continue

            current_row["Activity description"] = self._normalize_td_activity(
                f'{current_row["Activity description"]} {cells["activity"]}'
            )
            last_row_y = y_center

        return rows


    def _extract_td_statement_balances(
        self,
        page: fitz.Page,
    ) -> tuple[Decimal | None, Decimal | None]:
        # Read TD balance rows from their visual positions on the left panel.
        previous_balance: Decimal | None = None
        new_balance: Decimal | None = None

        # TD interleaves right-side rewards/payment-panel text into page.get_text().
        # Restrict this check to the left statement panel where the balance rows live.
        left_panel_words = [
            word
            for word in page.get_text("words")
            if self._word_center_x(word) < 350
        ]

        for line in self._group_words_into_lines(left_panel_words):
            line_text = self._normalize_text(
                " ".join(str(word[4]) for word in line["words"])
            )
            lowered = line_text.lower()

            if (
                "previous statement balance" not in lowered
                and "total new balance" not in lowered
            ):
                continue

            amount_tokens = [
                str(word[4])
                for word in line["words"]
                if AMOUNT_RE.fullmatch(str(word[4]))
            ]
            if not amount_tokens:
                continue

            amount = self._parse_amount(amount_tokens[-1])

            if "previous statement balance" in lowered:
                previous_balance = amount
            elif "total new balance" in lowered:
                new_balance = amount

        return previous_balance, new_balance

    def _validate_td_balance(
        self,
        transactions: pd.DataFrame,
        previous_balance: Decimal | None,
        new_balance: Decimal | None,
    ) -> None:
        if previous_balance is None or new_balance is None:
            raise PDFProcessingError(
                "TD Visa statement balances were not found. Extraction was not "
                "accepted because completeness could not be verified."
            )

        extracted_total = sum(
            (self._parse_amount(value) for value in transactions["Amount($)"] if value),
            Decimal("0.00"),
        )
        expected_total = new_balance - previous_balance

        if extracted_total != expected_total:
            raise PDFProcessingError(
                "TD Visa extraction does not reconcile with the statement balance. "
                f"Extracted transaction total: ${extracted_total:,.2f}; expected "
                f"change: ${expected_total:,.2f}. Previous balance: "
                f"${previous_balance:,.2f}; new balance: ${new_balance:,.2f}. "
                "The PDF may contain an image-only or otherwise unreadable "
                "transaction page."
            )


    # RBC Visa credit-card parser --------------------------------------------

    @staticmethod
    def _normalize_rbc_date(month: str, day: str) -> str:
        """Normalize RBC dates such as DEC 14 and JAN 04."""
        return f"{month.title()} {int(day)}"

    def _find_rbc_transaction_header(self, page: fitz.Page) -> dict | None:
        """Locate RBC's four-column transaction heading."""
        lines = self._group_words_into_lines(page.get_text("words"))
        required_tokens = {
            "transaction",
            "posting",
            "activity",
            "description",
            "amount",
            "date",
        }

        # RBC spreads the heading over several closely spaced visual lines.
        for window_size in range(1, 6):
            for index in range(0, len(lines) - window_size + 1):
                nearby_lines = lines[index : index + window_size]
                nearby_words = [
                    word
                    for nearby_line in nearby_lines
                    for word in nearby_line["words"]
                ]
                nearby_text = " ".join(
                    str(word[4]) for word in nearby_words
                ).lower()
                tokens = set(re.findall(r"[a-z]+", nearby_text))

                if not required_tokens.issubset(tokens):
                    continue

                transaction = next(
                    (
                        word
                        for word in nearby_words
                        if str(word[4]).lower() == "transaction"
                    ),
                    None,
                )
                posting = next(
                    (
                        word
                        for word in nearby_words
                        if str(word[4]).lower() == "posting"
                    ),
                    None,
                )
                amount = next(
                    (
                        word
                        for word in nearby_words
                        if str(word[4]).lower().replace(" ", "")
                        in {"amount", "amount($)"}
                    ),
                    None,
                )

                if not all((transaction, posting, amount)):
                    continue

                if not (
                    transaction[0] < posting[0] < amount[0]
                ):
                    continue

                amount_words = [
                    word
                    for word in nearby_words
                    if str(word[4]).lower().replace(" ", "")
                    in {"amount", "amount($)", "($)"}
                ]

                table_right = max(word[2] for word in amount_words) + 4.0

                table_header_words = [
                    word
                    for word in nearby_words
                    if self._word_center_x(word) < table_right
                ]

                return {
                    "right": table_right,
                    "bottom": max(word[3] for word in table_header_words),
                }

        return None

    def _extract_rbc_page_transactions(
        self,
        page: fitz.Page,
    ) -> list[dict[str, str]]:
        page_text = page.get_text("text")

        if self._page_is_excluded(page_text):
            logger.info(
                "Ignoring excluded report page %s for profile %s.",
                page.number + 1,
                self.profile.profile_id,
            )
            return []

        header = self._find_rbc_transaction_header(page)
        if header is None:
            return []

        # RBC places account/rewards information to the right of the
        # transaction table. Restrict extraction to the table width.
        candidate_words = [
            word
            for word in page.get_text("words")
            if word[1] >= header["bottom"] - 1
            and self._word_center_x(word) < header["right"]
        ]

        lines = self._group_words_into_lines(candidate_words)

        rows: list[dict[str, str]] = []
        current_row: dict[str, str] | None = None
        last_row_y: float | None = None
        table_started = False

        for line in lines:
            y_center = line["y_center"]

            if y_center <= header["bottom"] + 1:
                continue

            line_text = self._normalize_text(
                " ".join(str(word[4]) for word in line["words"])
            )

            if not line_text:
                continue

            if "subtotal of monthly activity" in line_text.lower():
                break

            row_match = RBC_ROW_RE.fullmatch(line_text)

            if row_match is not None:
                table_started = True

                transaction_month = row_match.group(1)
                transaction_day = row_match.group(2)
                posting_month = row_match.group(3)
                posting_day = row_match.group(4)
                activity = self._normalize_text(row_match.group(5))
                amount = row_match.group(6)

                current_row = {
                    "Transaction date": self._normalize_rbc_date(
                        transaction_month,
                        transaction_day,
                    ),
                    "Posting date": self._normalize_rbc_date(
                        posting_month,
                        posting_day,
                    ),
                    "Activity description": activity,
                    "Amount($)": amount.replace("$", "").rstrip("*"),
                }

                rows.append(current_row)
                last_row_y = y_center
                continue

            if not table_started or current_row is None or last_row_y is None:
                continue

            # RBC prints a 23-digit transaction/reference number below most
            # activity rows. It is metadata, not part of the description.
            if RBC_REFERENCE_RE.fullmatch(line_text):
                continue

            if y_center - last_row_y > self.continuation_gap:
                continue

            # Preserve a genuine wrapped description should one occur.
            if AMOUNT_RE.fullmatch(line_text):
                continue

            current_row["Activity description"] = self._normalize_text(
                f'{current_row["Activity description"]} {line_text}'
            )
            last_row_y = y_center

        return rows

    def _extract_rbc_statement_balances(
        self,
        page_text: str,
    ) -> tuple[Decimal | None, Decimal | None]:
        previous_balance: Decimal | None = None
        total_balance: Decimal | None = None

        previous_match = RBC_PREVIOUS_BALANCE_RE.search(page_text)
        total_match = RBC_TOTAL_BALANCE_RE.search(page_text)

        if previous_match is not None:
            previous_balance = self._parse_amount(previous_match.group(1))

        if total_match is not None:
            total_balance = self._parse_amount(total_match.group(1))

        return previous_balance, total_balance

    def _validate_rbc_balance(
        self,
        transactions: pd.DataFrame,
        previous_balance: Decimal | None,
        total_balance: Decimal | None,
    ) -> None:
        if previous_balance is None or total_balance is None:
            raise PDFProcessingError(
                "RBC Visa statement balances were not found. Extraction was not "
                "accepted because completeness could not be verified."
            )

        extracted_total = sum(
            (
                self._parse_amount(value)
                for value in transactions["Amount($)"]
                if value
            ),
            Decimal("0.00"),
        )

        expected_total = total_balance - previous_balance

        if extracted_total != expected_total:
            raise PDFProcessingError(
                "RBC Visa extraction does not reconcile with the statement balance. "
                f"Extracted transaction total: ${extracted_total:,.2f}; expected "
                f"change: ${expected_total:,.2f}. Previous balance: "
                f"${previous_balance:,.2f}; total balance: ${total_balance:,.2f}. "
                "The PDF may contain an image-only or otherwise unreadable "
                "transaction page."
            )

    # RBC Chequing parser ----------------------------------------------------

    @staticmethod
    def _normalize_rbc_chequing_date(day: str, month: str) -> str:
        return f"{int(day)} {month.title()}"

    def _find_rbc_chequing_header(self, page: fitz.Page) -> dict | None:
        """Locate the RBC Chequing transaction table columns."""
        lines = self._group_words_into_lines(page.get_text("words"))

        for line in lines:
            words = line["words"]
            tokens = {str(word[4]).lower() for word in words}

            required = {
                "date",
                "description",
                "withdrawals",
                "deposits",
                "balance",
            }
            if not required.issubset(tokens):
                continue

            def find_word(value: str) -> tuple | None:
                return next(
                    (
                        word
                        for word in words
                        if str(word[4]).lower() == value
                    ),
                    None,
                )

            date = find_word("date")
            description = find_word("description")
            withdrawals = find_word("withdrawals")
            deposits = find_word("deposits")
            balance = find_word("balance")

            if not all((date, description, withdrawals, deposits, balance)):
                continue

            positions = [
                date[0],
                description[0],
                withdrawals[0],
                deposits[0],
                balance[0],
            ]

            if positions != sorted(positions):
                continue

            return {
                "date": date[0],
                "description": description[0],
                "withdrawals": withdrawals[0],
                "deposits": deposits[0],
                "balance": balance[0],
                "bottom": max(word[3] for word in words),
            }

        return None

    def _line_to_rbc_chequing_cells(
        self,
        line: dict,
        header: dict,
    ) -> dict[str, str]:
        cells = {
            "date": [],
            "description": [],
            "withdrawals": [],
            "deposits": [],
            "balance": [],
        }

        for word in line["words"]:
            text = str(word[4])
            x_center = self._word_center_x(word)

            # Ignore RBC form/control text left of the Date column.
            if x_center < header["date"]:
                continue

            if x_center < header["description"]:
                cells["date"].append(text)
            elif x_center < header["withdrawals"]:
                cells["description"].append(text)
            elif x_center < header["deposits"]:
                cells["withdrawals"].append(text)
            elif x_center < header["balance"]:
                cells["deposits"].append(text)
            else:
                cells["balance"].append(text)

        return {
            key: self._normalize_text(" ".join(parts))
            for key, parts in cells.items()
        }

    def _extract_rbc_chequing_page_transactions(
        self,
        page: fitz.Page,
        current_date: str | None,
        pending_description: str,
    ) -> tuple[list[dict[str, str]], str | None, str]:
        """Extract RBC Chequing transactions and preserve state across pages."""
        if self._page_is_excluded(page.get_text("text")):
            return [], current_date, pending_description

        header = self._find_rbc_chequing_header(page)
        if header is None:
            return [], current_date, pending_description

        words = [
            word
            for word in page.get_text("words")
            if word[1] >= header["bottom"] - 1
        ]
        lines = self._group_words_into_lines(words)

        rows: list[dict[str, str]] = []

        for line in lines:
            if line["y_center"] <= header["bottom"] + 1:
                continue

            cells = self._line_to_rbc_chequing_cells(line, header)

            joined = self._normalize_text(
                " ".join(value for value in cells.values() if value)
            )
            lowered = joined.lower()

            if not joined:
                continue

            if "closing balance" in lowered:
                pending_description = ""
                break

            if lowered.startswith("please check this account statement"):
                pending_description = ""
                break

            if "opening balance" in lowered:
                pending_description = ""
                continue

            date_match = RBC_CHQ_DATE_RE.fullmatch(cells["date"])

            if cells["date"] and date_match is None:
                continue

            if date_match:
                current_date = self._normalize_rbc_chequing_date(
                    date_match.group(1),
                    date_match.group(2),
                )

            withdrawal_valid = bool(
                AMOUNT_RE.fullmatch(cells["withdrawals"])
            )
            deposit_valid = bool(
                AMOUNT_RE.fullmatch(cells["deposits"])
            )

            if withdrawal_valid and deposit_valid:
                raise PDFProcessingError(
                    "RBC Chequing row contains both a withdrawal and deposit."
                )

            description = cells["description"]

            # Description can begin on one visual line and finish on the next.
            if not withdrawal_valid and not deposit_valid:
                if description:
                    pending_description = self._normalize_text(
                        f"{pending_description} {description}"
                    )
                continue

            if current_date is None:
                raise PDFProcessingError(
                    "RBC Chequing transaction found before a date was established."
                )

            full_description = self._normalize_text(
                f"{pending_description} {description}"
            )

            if not full_description:
                raise PDFProcessingError(
                    "RBC Chequing transaction amount found without a description."
                )

            rows.append(
                {
                    "Date": current_date,
                    "Description": full_description,
                    "Withdrawals ($)": (
                        cells["withdrawals"].replace("$", "").rstrip("*")
                        if withdrawal_valid
                        else ""
                    ),
                    "Deposits ($)": (
                        cells["deposits"].replace("$", "").rstrip("*")
                        if deposit_valid
                        else ""
                    ),
                    "Balance ($)": (
                        cells["balance"].replace("$", "").rstrip("*")
                        if AMOUNT_RE.fullmatch(cells["balance"])
                        else ""
                    ),
                }
            )

            pending_description = ""

        return rows, current_date, pending_description

    def _extract_rbc_chequing_summary(
        self,
        page_text: str,
    ) -> tuple[
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
    ]:
        """Extract RBC Chequing opening, deposits, withdrawals, and closing totals."""
        normalized = self._normalize_text(page_text)

        def get_amount(pattern: re.Pattern[str]) -> Decimal | None:
            match = pattern.search(normalized)
            if match is None:
                return None
            return self._parse_amount(match.group(1))

        return (
            get_amount(RBC_CHQ_OPENING_BALANCE_RE),
            get_amount(RBC_CHQ_TOTAL_DEPOSITS_RE),
            get_amount(RBC_CHQ_TOTAL_WITHDRAWALS_RE),
            get_amount(RBC_CHQ_CLOSING_BALANCE_RE),
        )

    @staticmethod
    def _extract_rbc_chequing_statement_period(
        page_text: str,
    ) -> tuple[date | None, date | None]:
        normalized = VisaPDFProcessor._normalize_text(
            page_text
        )

        def parse_date(
            pattern: re.Pattern[str],
            label: str,
        ) -> date | None:
            match = pattern.search(normalized)

            if match is None:
                return None

            value = match.group(1)

            for date_format in (
                "%B %d, %Y",
                "%b %d, %Y",
            ):
                try:
                    return datetime.strptime(
                        value,
                        date_format,
                    ).date()
                except ValueError:
                    continue

            raise PDFProcessingError(
                f"Invalid RBC Chequing {label} date: "
                f"{value!r}."
            )

        statement_start = parse_date(
            RBC_CHQ_OPENING_DATE_RE,
            "opening-balance",
        )
        statement_end = parse_date(
            RBC_CHQ_CLOSING_DATE_RE,
            "closing-balance",
        )

        if (
            statement_start is not None
            and statement_end is not None
            and statement_start > statement_end
        ):
            raise PDFProcessingError(
                "RBC Chequing opening-balance date is later "
                "than its closing-balance date."
            )

        return statement_start, statement_end

    def _validate_rbc_chequing_totals(
        self,
        transactions: pd.DataFrame,
        opening_balance: Decimal | None,
        statement_deposits: Decimal | None,
        statement_withdrawals: Decimal | None,
        closing_balance: Decimal | None,
    ) -> None:
        """Verify RBC Chequing extraction against the statement summary."""
        if any(
            value is None
            for value in (
                opening_balance,
                statement_deposits,
                statement_withdrawals,
                closing_balance,
            )
        ):
            raise PDFProcessingError(
                "RBC Chequing statement totals were not found. Extraction was not "
                "accepted because completeness could not be verified."
            )

        extracted_withdrawals = sum(
            (
                self._parse_amount(value)
                for value in transactions["Withdrawals ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        extracted_deposits = sum(
            (
                self._parse_amount(value)
                for value in transactions["Deposits ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        if (
            extracted_deposits != statement_deposits
            or extracted_withdrawals != statement_withdrawals
        ):
            raise PDFProcessingError(
                "RBC Chequing extraction totals do not match the statement totals. "
                f"Extracted deposits: ${extracted_deposits:,.2f}; statement: "
                f"${statement_deposits:,.2f}. Extracted withdrawals: "
                f"${extracted_withdrawals:,.2f}; statement: "
                f"${statement_withdrawals:,.2f}."
            )

        calculated_closing = (
            opening_balance
            + statement_deposits
            - statement_withdrawals
        )

        if calculated_closing != closing_balance:
            raise PDFProcessingError(
                "RBC Chequing statement summary does not reconcile. "
                f"Opening balance: ${opening_balance:,.2f}; deposits: "
                f"${statement_deposits:,.2f}; withdrawals: "
                f"${statement_withdrawals:,.2f}; calculated closing balance: "
                f"${calculated_closing:,.2f}; statement closing balance: "
                f"${closing_balance:,.2f}."
            )

    # RBC LOC parser ---------------------------------------------------------

    def _find_rbc_loc_header(self, page: fitz.Page) -> dict | None:
        """Locate the RBC Royal Credit Line activity columns."""
        lines = self._group_words_into_lines(page.get_text("words"))

        for line in lines:
            words = line["words"]

            def find_word(predicate):
                return next(
                    (
                        word
                        for word in words
                        if predicate(str(word[4]).lower())
                    ),
                    None,
                )

            date = find_word(lambda value: value == "date")
            description = find_word(lambda value: value == "description")
            interest = find_word(
                lambda value: value.startswith("interest/fees/insurance")
            )
            withdrawals = find_word(lambda value: value == "withdrawals")
            payments = find_word(lambda value: value == "payments")
            balance = find_word(lambda value: value == "balance")

            if not all(
                (
                    date,
                    description,
                    interest,
                    withdrawals,
                    payments,
                    balance,
                )
            ):
                continue

            positions = [
                date[0],
                description[0],
                interest[0],
                withdrawals[0],
                payments[0],
                balance[0],
            ]

            if positions != sorted(positions):
                continue

            return {
                "date": date[0],
                "description": description[0],
                "interest": interest[0],
                "withdrawals": withdrawals[0],
                "payments": payments[0],
                "balance": balance[0],
                "bottom": max(word[3] for word in words),
            }

        return None

    def _line_to_rbc_loc_cells(
        self,
        line: dict,
        header: dict,
    ) -> dict[str, str]:
        cells = {
            "date": [],
            "description": [],
            "interest": [],
            "withdrawals": [],
            "payments": [],
            "balance": [],
        }

        for word in line["words"]:
            value = str(word[4])
            x_center = self._word_center_x(word)

            if x_center < header["date"]:
                continue

            if x_center < header["description"]:
                cells["date"].append(value)
            elif x_center < header["interest"]:
                cells["description"].append(value)
            elif x_center < header["withdrawals"]:
                cells["interest"].append(value)
            elif x_center < header["payments"]:
                cells["withdrawals"].append(value)
            elif x_center < header["balance"]:
                cells["payments"].append(value)
            else:
                cells["balance"].append(value)

        return {
            key: self._normalize_text(" ".join(parts))
            for key, parts in cells.items()
        }

    def _extract_rbc_loc_page_transactions(
        self,
        page: fitz.Page,
        current_date: str | None,
        pending_description: str,
    ) -> tuple[list[dict[str, str]], str | None, str]:
        """Extract RBC LOC transactions while retaining state across pages."""
        page_text = page.get_text("text")

        if self._page_is_excluded(page_text):
            return [], current_date, pending_description

        header = self._find_rbc_loc_header(page)
        if header is None:
            return [], current_date, pending_description

        words = [
            word
            for word in page.get_text("words")
            if word[1] >= header["bottom"] - 1
        ]

        lines = self._group_words_into_lines(words)
        rows: list[dict[str, str]] = []

        for line in lines:
            if line["y_center"] <= header["bottom"] + 1:
                continue

            cells = self._line_to_rbc_loc_cells(line, header)

            joined = self._normalize_text(
                " ".join(value for value in cells.values() if value)
            )
            lowered = joined.lower()

            if not joined:
                continue

            # Material following the account-activity table.
            if (
                lowered.startswith("this is a history of your interest")
                or lowered.startswith("please note the interest payment")
                or lowered.startswith("what is my minimum payment")
                or lowered.startswith("please retain this statement")
            ):
                pending_description = ""
                break

            # Do not create transactions from balance labels.
            if (
                "opening balance" in lowered
                or "closing balance" in lowered
                or lowered.startswith("principal balance")
            ):
                pending_description = ""
                continue

            date_match = RBC_CHQ_DATE_RE.fullmatch(cells["date"])

            if cells["date"] and date_match is None:
                continue

            if date_match is not None:
                current_date = self._normalize_rbc_chequing_date(
                    date_match.group(1),
                    date_match.group(2),
                )

            interest_valid = bool(
                AMOUNT_RE.fullmatch(cells["interest"])
            )
            withdrawal_valid = bool(
                AMOUNT_RE.fullmatch(cells["withdrawals"])
            )
            payment_valid = bool(
                AMOUNT_RE.fullmatch(cells["payments"])
            )

            has_transaction_amount = (
                interest_valid
                or withdrawal_valid
                or payment_valid
            )

            description = cells["description"]

            if not has_transaction_amount:
                if description:
                    pending_description = self._normalize_text(
                        f"{pending_description} {description}"
                    )
                continue

            if current_date is None:
                raise PDFProcessingError(
                    "RBC LOC transaction found before a date was established."
                )

            full_description = self._normalize_text(
                f"{pending_description} {description}"
            )

            if not full_description:
                raise PDFProcessingError(
                    "RBC LOC transaction amount found without a description."
                )

            def clean_amount(value: str, valid: bool) -> str:
                if not valid:
                    return ""
                return value.replace("$", "").rstrip("*")

            rows.append(
                {
                    "Date": current_date,
                    "Description": full_description,
                    "Interest/Fees/Insurance ($)": clean_amount(
                        cells["interest"],
                        interest_valid,
                    ),
                    "Withdrawals ($)": clean_amount(
                        cells["withdrawals"],
                        withdrawal_valid,
                    ),
                    "Payments ($)": clean_amount(
                        cells["payments"],
                        payment_valid,
                    ),
                    "Balance owing ($)": clean_amount(
                        cells["balance"],
                        bool(AMOUNT_RE.fullmatch(cells["balance"])),
                    ),
                }
            )

            pending_description = ""

        return rows, current_date, pending_description

    def _extract_rbc_loc_summary(
        self,
        page_text: str,
    ) -> tuple[
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
    ]:
        """Extract RBC LOC principal and statement totals."""
        normalized = self._normalize_text(page_text)

        balances = [
            self._parse_amount(value)
            for value in RBC_LOC_PRINCIPAL_BALANCE_RE.findall(normalized)
        ]

        opening_balance = balances[0] if balances else None
        closing_balance = balances[-1] if len(balances) >= 2 else None

        def get_amount(pattern: re.Pattern[str]) -> Decimal | None:
            match = pattern.search(normalized)
            if match is None:
                return None
            return self._parse_amount(match.group(1))

        return (
            opening_balance,
            get_amount(RBC_LOC_WITHDRAWALS_RE),
            get_amount(RBC_LOC_PAYMENTS_RE),
            get_amount(RBC_LOC_INTEREST_RE),
            get_amount(RBC_LOC_FEES_RE),
            closing_balance,
        )

    def _validate_rbc_loc_totals(
        self,
        transactions: pd.DataFrame,
        opening_balance: Decimal | None,
        statement_withdrawals: Decimal | None,
        statement_payments: Decimal | None,
        statement_interest: Decimal | None,
        statement_fees: Decimal | None,
        closing_balance: Decimal | None,
    ) -> None:
        """Verify RBC LOC extraction against the statement summary."""
        values = (
            opening_balance,
            statement_withdrawals,
            statement_payments,
            statement_interest,
            statement_fees,
            closing_balance,
        )

        if any(value is None for value in values):
            raise PDFProcessingError(
                "RBC LOC statement totals were not found. Extraction was not "
                "accepted because completeness could not be verified."
            )

        extracted_withdrawals = sum(
            (
                self._parse_amount(value)
                for value in transactions["Withdrawals ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        extracted_principal_payments = sum(
            (
                self._parse_amount(value)
                for value in transactions["Payments ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        extracted_interest_fees = sum(
            (
                self._parse_amount(value)
                for value in transactions["Interest/Fees/Insurance ($)"]
                if value
            ),
            Decimal("0.00"),
        )

        if extracted_withdrawals != statement_withdrawals:
            raise PDFProcessingError(
                "RBC LOC extracted withdrawals do not match the statement. "
                f"Extracted: ${extracted_withdrawals:,.2f}; statement: "
                f"${statement_withdrawals:,.2f}."
            )

        extracted_total_payments = (
            extracted_principal_payments
            + extracted_interest_fees
        )

        if extracted_total_payments != statement_payments:
            raise PDFProcessingError(
                "RBC LOC extracted payments do not match the statement. "
                f"Principal payments: ${extracted_principal_payments:,.2f}; "
                f"interest/fees/insurance: ${extracted_interest_fees:,.2f}; "
                f"combined: ${extracted_total_payments:,.2f}; statement: "
                f"${statement_payments:,.2f}."
            )

        calculated_closing = (
            opening_balance
            + statement_withdrawals
            + statement_interest
            + statement_fees
            - statement_payments
        )

        if calculated_closing != closing_balance:
            raise PDFProcessingError(
                "RBC LOC principal balance does not reconcile. "
                f"Opening: ${opening_balance:,.2f}; withdrawals: "
                f"${statement_withdrawals:,.2f}; interest: "
                f"${statement_interest:,.2f}; fees: ${statement_fees:,.2f}; "
                f"payments: ${statement_payments:,.2f}; calculated closing: "
                f"${calculated_closing:,.2f}; statement closing: "
                f"${closing_balance:,.2f}."
            )

    # Shared processing -------------------------------------------------------

    def extract_transactions(self, pdf_path: str | Path) -> ExtractionResult:
        source_path = Path(pdf_path)
        if not source_path.is_file():
            raise PDFProcessingError(f"PDF file not found: {source_path}")

        metadata = self._build_statement_metadata(source_path)
        ghostscript_path = self.detect_ghostscript()
        if ghostscript_path:
            logger.info("Ghostscript detected at %s.", ghostscript_path)
        else:
            logger.info(
                "Ghostscript was not found on PATH. It is not required for this "
                "PyMuPDF extraction path."
            )

        all_rows: list[dict[str, str]] = []
        source_pages: list[int] = []
        total_text_characters = 0
        simplii_total_out: Decimal | None = None
        simplii_total_in: Decimal | None = None
        simplii_statement_start: date | None = None
        simplii_statement_end: date | None = None
        td_previous_balance: Decimal | None = None
        td_new_balance: Decimal | None = None
        rbc_previous_balance: Decimal | None = None
        rbc_total_balance: Decimal | None = None

        capital_one_previous_balance: Decimal | None = None
        capital_one_new_balance: Decimal | None = None

        rbc_chq_opening_balance: Decimal | None = None
        rbc_chq_total_deposits: Decimal | None = None
        rbc_chq_total_withdrawals: Decimal | None = None
        rbc_chq_closing_balance: Decimal | None = None
        rbc_chq_statement_start: date | None = None
        rbc_chq_statement_end: date | None = None
        rbc_chq_current_date: str | None = None
        rbc_chq_pending_description = ""

        rbc_loc_opening_balance: Decimal | None = None
        rbc_loc_total_withdrawals: Decimal | None = None
        rbc_loc_total_payments: Decimal | None = None
        rbc_loc_total_interest: Decimal | None = None
        rbc_loc_total_fees: Decimal | None = None
        rbc_loc_closing_balance: Decimal | None = None
        rbc_loc_current_date: str | None = None
        rbc_loc_pending_description = ""

        try:
            with fitz.open(source_path) as document:
                for page_number, page in enumerate(document, start=1):
                    page_text = page.get_text("text")
                    total_text_characters += len(page_text.strip())

                    if self.profile.parser == "cibc_credit_card":
                        page_rows = self._extract_cibc_page_transactions(page)
                    elif self.profile.parser == "triangle_mastercard":
                        page_rows = self._extract_triangle_page_transactions(page)
                    elif self.profile.parser == "capital_one_mastercard":
                        (
                            page_previous_balance,
                            page_new_balance,
                        ) = self._extract_capital_one_statement_balances(
                            page
                        )

                        if page_previous_balance is not None:
                            capital_one_previous_balance = (
                                page_previous_balance
                            )

                        if page_new_balance is not None:
                            capital_one_new_balance = (
                                page_new_balance
                            )

                        page_rows = (
                            self._extract_capital_one_page_transactions(
                                page
                            )
                        )
                    elif self.profile.parser == "simplii_chequing_account":
                        (
                            page_statement_start,
                            page_statement_end,
                        ) = self._extract_simplii_statement_period(
                            page_text
                        )

                        if (
                            page_statement_start is not None
                            and page_statement_end is not None
                        ):
                            page_period = (
                                page_statement_start,
                                page_statement_end,
                            )

                            if simplii_statement_start is not None:
                                current_period = (
                                    simplii_statement_start,
                                    simplii_statement_end,
                                )

                                if page_period != current_period:
                                    raise PDFProcessingError(
                                        "Conflicting Simplii statement "
                                        "periods were found in the PDF."
                                    )

                            simplii_statement_start = (
                                page_statement_start
                            )
                            simplii_statement_end = (
                                page_statement_end
                            )

                        total_out_match = SIMPLII_TOTAL_OUT_RE.search(page_text)
                        total_in_match = SIMPLII_TOTAL_IN_RE.search(page_text)

                        if total_out_match:
                            simplii_total_out = self._parse_amount(
                                total_out_match.group(1)
                            )

                        if total_in_match:
                            simplii_total_in = self._parse_amount(
                                total_in_match.group(1)
                            )

                        page_rows = (
                            self._extract_simplii_page_transactions(
                                page
                            )
                        )
                    elif self.profile.parser == "td_visa_credit_card":
                        (
                            page_previous_balance,
                            page_new_balance,
                        ) = self._extract_td_statement_balances(page)
                        if page_previous_balance is not None:
                            td_previous_balance = page_previous_balance
                        if page_new_balance is not None:
                            td_new_balance = page_new_balance
                        page_rows = self._extract_td_page_transactions(page)
                    elif self.profile.parser == "rbc_visa_credit_card":
                        (
                            page_previous_balance,
                            page_total_balance,
                        ) = self._extract_rbc_statement_balances(page_text)
                        if page_previous_balance is not None:
                            rbc_previous_balance = page_previous_balance
                        if page_total_balance is not None:
                            rbc_total_balance = page_total_balance
                        page_rows = self._extract_rbc_page_transactions(page)
                    elif self.profile.parser == "rbc_chequing_account":
                        (
                            page_statement_start,
                            page_statement_end,
                        ) = (
                            self._extract_rbc_chequing_statement_period(
                                page_text
                            )
                        )

                        if page_statement_start is not None:
                            if (
                                rbc_chq_statement_start is not None
                                and page_statement_start
                                != rbc_chq_statement_start
                            ):
                                raise PDFProcessingError(
                                    "Conflicting RBC Chequing opening "
                                    "dates were found in the PDF."
                                )

                            rbc_chq_statement_start = (
                                page_statement_start
                            )

                        if page_statement_end is not None:
                            if (
                                rbc_chq_statement_end is not None
                                and page_statement_end
                                != rbc_chq_statement_end
                            ):
                                raise PDFProcessingError(
                                    "Conflicting RBC Chequing closing "
                                    "dates were found in the PDF."
                                )

                            rbc_chq_statement_end = (
                                page_statement_end
                            )

                        (
                            page_opening,
                            page_deposits,
                            page_withdrawals,
                            page_closing,
                        ) = self._extract_rbc_chequing_summary(page_text)

                        if page_opening is not None:
                            rbc_chq_opening_balance = page_opening
                        if page_deposits is not None:
                            rbc_chq_total_deposits = page_deposits
                        if page_withdrawals is not None:
                            rbc_chq_total_withdrawals = page_withdrawals
                        if page_closing is not None:
                            rbc_chq_closing_balance = page_closing

                        (
                            page_rows,
                            rbc_chq_current_date,
                            rbc_chq_pending_description,
                        ) = self._extract_rbc_chequing_page_transactions(
                            page,
                            rbc_chq_current_date,
                            rbc_chq_pending_description,
                        )
                    elif self.profile.parser == "rbc_loc":
                        (
                            page_opening,
                            page_withdrawals,
                            page_payments,
                            page_interest,
                            page_fees,
                            page_closing,
                        ) = self._extract_rbc_loc_summary(page_text)

                        if page_opening is not None:
                            rbc_loc_opening_balance = page_opening
                        if page_withdrawals is not None:
                            rbc_loc_total_withdrawals = page_withdrawals
                        if page_payments is not None:
                            rbc_loc_total_payments = page_payments
                        if page_interest is not None:
                            rbc_loc_total_interest = page_interest
                        if page_fees is not None:
                            rbc_loc_total_fees = page_fees
                        if page_closing is not None:
                            rbc_loc_closing_balance = page_closing

                        (
                            page_rows,
                            rbc_loc_current_date,
                            rbc_loc_pending_description,
                        ) = self._extract_rbc_loc_page_transactions(
                            page,
                            rbc_loc_current_date,
                            rbc_loc_pending_description,
                        )
                    else:
                        raise PDFProcessingError(
                            f"Unsupported parser '{self.profile.parser}'."
                        )

                    if page_rows:
                        all_rows.extend(page_rows)
                        source_pages.append(page_number)
        except (RuntimeError, ValueError) as exc:
            raise PDFProcessingError(
                f"Could not read PDF {source_path}: {exc}"
            ) from exc

        if not all_rows:
            if total_text_characters == 0:
                raise PDFProcessingError(
                    "No readable text was found in the PDF. The statement appears "
                    "to be image-only and requires OCR before table extraction."
                )

            if self.profile.parser == "td_visa_credit_card":
                raise PDFProcessingError(
                    "No readable TD Visa transaction table was found. The transaction "
                    "page may be image-only or otherwise unreadable."
                )

            if self.profile.parser == "rbc_visa_credit_card":
                raise PDFProcessingError(
                    "No readable RBC Visa transaction table was found. The transaction "
                    "page may be image-only or otherwise unreadable."
                )

            if (
                self.profile.parser == "capital_one_mastercard"
                and self._is_capital_one_zero_activity_statement(source_path)
            ):
                return ExtractionResult(
                    transactions=pd.DataFrame(
                        columns=self.required_headers
                    ),
                    source_pages=tuple(),
                    ghostscript_path=ghostscript_path,
                    metadata=metadata,
                )

            raise PDFProcessingError(
                f"The PDF contains readable text, but no transaction table matching "
                f"profile '{self.profile.display_name}' was found."
            )

        if self.profile.parser == "simplii_chequing_account":
            if (
                simplii_statement_start is None
                or simplii_statement_end is None
            ):
                raise PDFProcessingError(
                    "Simplii statement period was not found. "
                    "Transaction years cannot be resolved safely."
                )

            metadata = StatementMetadata(
                source_file=metadata.source_file,
                profile_id=metadata.profile_id,
                institution=metadata.institution,
                document_type=metadata.document_type,
                statement_start_date=simplii_statement_start,
                statement_end_date=simplii_statement_end,
            )

        if self.profile.parser == "rbc_chequing_account":
            if (
                rbc_chq_statement_start is None
                or rbc_chq_statement_end is None
            ):
                raise PDFProcessingError(
                    "RBC Chequing statement period was not found. "
                    "Transaction years cannot be resolved safely."
                )

            metadata = StatementMetadata(
                source_file=metadata.source_file,
                profile_id=metadata.profile_id,
                institution=metadata.institution,
                document_type=metadata.document_type,
                statement_start_date=rbc_chq_statement_start,
                statement_end_date=rbc_chq_statement_end,
            )

        transactions = pd.DataFrame(all_rows, columns=self.required_headers)

        if self.profile.parser == "capital_one_mastercard":
            self._validate_capital_one_balance(
                transactions,
                capital_one_previous_balance,
                capital_one_new_balance,
            )

        if self.profile.parser == "simplii_chequing_account":
            self._validate_simplii_totals(
                transactions,
                simplii_total_out,
                simplii_total_in,
            )
        elif self.profile.parser == "td_visa_credit_card":
            self._validate_td_balance(
                transactions,
                td_previous_balance,
                td_new_balance,
            )
        elif self.profile.parser == "rbc_visa_credit_card":
            self._validate_rbc_balance(
                transactions,
                rbc_previous_balance,
                rbc_total_balance,
            )
        elif self.profile.parser == "rbc_chequing_account":
            self._validate_rbc_chequing_totals(
                transactions,
                rbc_chq_opening_balance,
                rbc_chq_total_deposits,
                rbc_chq_total_withdrawals,
                rbc_chq_closing_balance,
            )
        elif self.profile.parser == "rbc_loc":
            self._validate_rbc_loc_totals(
                transactions,
                rbc_loc_opening_balance,
                rbc_loc_total_withdrawals,
                rbc_loc_total_payments,
                rbc_loc_total_interest,
                rbc_loc_total_fees,
                rbc_loc_closing_balance,
            )

        return ExtractionResult(
            transactions=transactions,
            source_pages=tuple(source_pages),
            ghostscript_path=ghostscript_path,
            metadata=metadata,
        )

    def _capital_one_summary_amount(
        self,
        line: dict,
        *,
        minimum_x: float,
    ) -> Decimal | None:
        """Return the right-most Capital One monetary value on a line."""
        candidates: list[tuple[float, Decimal]] = []

        for word in line["words"]:
            x_center = self._word_center_x(word)

            if x_center < minimum_x:
                continue

            normalized = self._normalize_capital_one_amount(
                str(word[4])
            )

            if normalized is None:
                continue

            candidates.append(
                (
                    x_center,
                    Decimal(normalized),
                )
            )

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda item: item[0],
        )[1]

    def _extract_capital_one_modern_balances(
        self,
        page: fitz.Page,
    ) -> tuple[Decimal | None, Decimal | None]:
        """
        Extract Previous Balance and New Balance from the modern
        right-hand Account Activity summary.
        """
        lines = self._group_words_into_lines(
            page.get_text("words")
        )

        sequence = (
            "previous",
            "payments",
            "credits",
            "transactions",
            "fees",
            "interest",
            "new",
        )

        values: dict[str, Decimal] = {}
        state = 0

        for line in lines:
            tokens = [
                re.sub(
                    r"[^A-Za-z]",
                    "",
                    str(word[4]),
                ).lower()
                for word in line["words"]
                if self._word_center_x(word) >= 320
            ]

            tokens = [
                token
                for token in tokens
                if token
            ]

            if not tokens:
                continue

            amount = self._capital_one_summary_amount(
                line,
                minimum_x=525,
            )

            if amount is None:
                continue

            expected = sequence[state]

            if expected == "previous":
                matched = (
                    "previous" in tokens
                    and "balance" in tokens
                )
            elif expected == "new":
                matched = (
                    "new" in tokens
                    and "balance" in tokens
                )
            else:
                matched = expected in tokens

            if not matched:
                continue

            values[expected] = amount
            state += 1

            if state == len(sequence):
                return (
                    values["previous"],
                    values["new"],
                )

        return None, None

    def _extract_capital_one_legacy_balances(
        self,
        page: fitz.Page,
    ) -> tuple[Decimal | None, Decimal | None]:
        """
        Extract Previous Balance and New Balance from the legacy
        horizontal account-summary block.
        """
        lines = self._group_words_into_lines(
            page.get_text("words")
        )

        candidates: list[
            tuple[int, float, Decimal, Decimal]
        ] = []

        for start in range(len(lines)):
            for size in range(1, 4):
                window = lines[start : start + size]

                if len(window) != size:
                    continue

                if (
                    window[-1]["y_center"]
                    - window[0]["y_center"]
                    > 30
                ):
                    continue

                header_text = self._normalize_text(
                    " ".join(
                        str(word[4])
                        for candidate_line in window
                        for word in candidate_line["words"]
                    )
                ).lower()

                if not (
                    "previous balance" in header_text
                    and "payments" in header_text
                    and "credits" in header_text
                    and "transactions" in header_text
                    and "new balance" in header_text
                ):
                    continue

                header_bottom = max(
                    word[3]
                    for candidate_line in window
                    for word in candidate_line["words"]
                )

                best_amounts: list[
                    tuple[float, Decimal]
                ] = []

                for candidate_line in lines:
                    if (
                        candidate_line["y_center"]
                        <= header_bottom
                    ):
                        continue

                    if (
                        candidate_line["y_center"]
                        - header_bottom
                        > 50
                    ):
                        break

                    amounts: list[
                        tuple[float, Decimal]
                    ] = []

                    for word in candidate_line["words"]:
                        normalized = (
                            self._normalize_capital_one_amount(
                                str(word[4])
                            )
                        )

                        if normalized is None:
                            continue

                        amounts.append(
                            (
                                self._word_center_x(word),
                                Decimal(normalized),
                            )
                        )

                    if len(amounts) > len(best_amounts):
                        best_amounts = amounts

                if len(best_amounts) < 5:
                    continue

                best_amounts.sort(
                    key=lambda item: item[0]
                )

                candidates.append(
                    (
                        len(best_amounts),
                        window[0]["y_center"],
                        best_amounts[0][1],
                        best_amounts[-1][1],
                    )
                )

        if not candidates:
            return None, None

        candidates.sort(
            key=lambda item: (
                -item[0],
                item[1],
            )
        )

        _, _, previous_balance, new_balance = (
            candidates[0]
        )

        return previous_balance, new_balance

    def _extract_capital_one_statement_balances(
        self,
        page: fitz.Page,
    ) -> tuple[Decimal | None, Decimal | None]:
        """Extract Capital One balances from either known statement era."""
        previous, new = (
            self._extract_capital_one_modern_balances(page)
        )

        if (
            previous is not None
            and new is not None
        ):
            return previous, new

        return self._extract_capital_one_legacy_balances(
            page
        )

    def _validate_capital_one_balance(
        self,
        transactions: pd.DataFrame,
        previous_balance: Decimal | None,
        new_balance: Decimal | None,
    ) -> None:
        """
        Require extracted Capital One activity to reconcile the
        statement's Previous Balance to its New Balance.
        """
        if (
            previous_balance is None
            or new_balance is None
        ):
            raise PDFProcessingError(
                "Capital One statement balances were not found. "
                "Extraction was not accepted because completeness "
                "could not be verified."
            )

        extracted_activity = sum(
            (
                Decimal(str(value))
                for value in transactions["Amount"]
            ),
            Decimal("0.00"),
        )

        if (
            previous_balance + extracted_activity
            != new_balance
        ):
            raise PDFProcessingError(
                "Capital One extraction does not reconcile to the "
                "statement balance. The PDF may contain an unreadable "
                "or missed transaction row."
            )

    def _is_capital_one_zero_activity_statement(
        self,
        pdf_path: str | Path,
    ) -> bool:
        """
        Return True only when a Capital One statement explicitly verifies
        zero financial activity in its statement summary.

        This is intentionally narrow. A readable statement with no extracted
        rows is still treated as an extraction failure unless the Capital One
        summary itself shows the relevant activity categories as zero.
        """
        if self.profile.parser != "capital_one_mastercard":
            return False

        required_phrases = (
            "previous balance",
            "payments and credits",
            "transactions",
            "interest charges",
            "new balance",
        )

        with fitz.open(pdf_path) as document:
            for page in document:
                lines = self._group_words_into_lines(
                    page.get_text("words")
                )

                for index, line in enumerate(lines):
                    line_text = self._normalize_text(
                        " ".join(
                            str(word[4])
                            for word in line["words"]
                        )
                    ).lower()

                    if not all(
                        phrase in line_text
                        for phrase in required_phrases
                    ):
                        continue

                    base_y = line["y_center"]
                    amounts: list[Decimal] = []

                    for candidate in lines[index : index + 4]:
                        if candidate["y_center"] - base_y > 35:
                            break

                        for word in candidate["words"]:
                            raw = str(word[4]).strip()

                            if not AMOUNT_RE.fullmatch(raw):
                                continue

                            try:
                                amounts.append(
                                    self._parse_amount(raw)
                                )
                            except (InvalidOperation, ValueError):
                                continue

                    # The legacy Capital One account summary contains multiple
                    # financial totals. Requiring at least five parsed amounts
                    # and requiring every one to be zero prevents a generic
                    # "no rows" result from being accepted as an empty statement.
                    if (
                        len(amounts) >= 5
                        and all(
                            amount == Decimal("0.00")
                            for amount in amounts
                        )
                    ):
                        return True

        return False

    def save_transactions(
        self,
        pdf_path: str | Path,
        output_folder: str | Path | None,
        result: ExtractionResult,
    ) -> Path:
        output_path = (
            Path(output_folder)
            if output_folder is not None
            else self.profile.resolve_output_folder()
        )
        output_path.mkdir(parents=True, exist_ok=True)

        source_name = Path(pdf_path).stem
        csv_path = output_path / f"{source_name}_transactions.csv"
        result.transactions.to_csv(csv_path, index=False)
        return csv_path

    def process_pdf(
        self,
        pdf_path: str | Path,
        output_folder: str | Path | None = None,
    ) -> tuple[ExtractionResult, Path]:
        result = self.extract_transactions(pdf_path)
        csv_path = self.save_transactions(pdf_path, output_folder, result)
        return result, csv_path

    def _is_rbc_loc_annual_summary(
        self,
        pdf_path: str | Path,
    ) -> bool:
        """Return True for RBC LOC annual summaries without transaction tables."""
        if self.profile.parser != "rbc_loc":
            return False

        with fitz.open(pdf_path) as document:
            text = self._normalize_text(
                " ".join(page.get_text("text") for page in document)
            )

        lowered = text.lower()

        return (
            "year opening principal balance" in lowered
            and "year closing principal balance" in lowered
            and "interest paid" in lowered
            and "details of your account activity" not in lowered
        )

    def _find_pdf_files(self, source_folder: Path) -> list[Path]:
        iterator = (
            source_folder.rglob(self.profile.file_pattern)
            if self.profile.recursive
            else source_folder.glob(self.profile.file_pattern)
        )
        return sorted(
            (path for path in iterator if path.is_file()),
            key=lambda path: str(path).casefold(),
        )

    def process_folder(
        self,
        input_folder: str | Path | None = None,
        output_folder: str | Path | None = None,
    ) -> BatchResult:
        """Process profile-matching PDFs and continue after individual failures."""
        source_folder = (
            Path(input_folder)
            if input_folder is not None
            else self.profile.resolve_input_folder()
        )
        if not source_folder.is_dir():
            raise PDFProcessingError(f"Input folder not found: {source_folder}")

        pdf_files = self._find_pdf_files(source_folder)
        if not pdf_files:
            raise PDFProcessingError(
                f"No PDF files matching '{self.profile.file_pattern}' found in: "
                f"{source_folder}"
            )

        destination = (
            Path(output_folder)
            if output_folder is not None
            else self.profile.resolve_output_folder()
        )
        destination.mkdir(parents=True, exist_ok=True)

        file_results: list[BatchFileResult] = []

        for pdf_file in pdf_files:
            if self._is_rbc_loc_annual_summary(pdf_file):
                file_results.append(
                    BatchFileResult(
                        pdf_file=pdf_file,
                        status="Skipped",
                        transaction_count=0,
                        source_pages=(),
                        output_csv=None,
                        error="Annual RBC LOC summary; no transaction table.",
                    )
                )
                continue

            file_destination = destination
            if self.profile.preserve_subfolders:
                file_destination = destination / pdf_file.parent.relative_to(source_folder)

            try:
                result, csv_path = self.process_pdf(pdf_file, file_destination)
                file_results.append(
                    BatchFileResult(
                        pdf_file=pdf_file,
                        status="Success",
                        transaction_count=len(result.transactions),
                        source_pages=result.source_pages,
                        output_csv=csv_path,
                        error=None,
                    )
                )
            except PDFProcessingError as exc:
                logger.warning("Could not process %s: %s", pdf_file.name, exc)
                file_results.append(
                    BatchFileResult(
                        pdf_file=pdf_file,
                        status="Failed",
                        transaction_count=0,
                        source_pages=(),
                        output_csv=None,
                        error=str(exc),
                    )
                )
            except Exception as exc:
                logger.exception("Unexpected error while processing %s", pdf_file.name)
                file_results.append(
                    BatchFileResult(
                        pdf_file=pdf_file,
                        status="Failed",
                        transaction_count=0,
                        source_pages=(),
                        output_csv=None,
                        error=f"Unexpected error: {exc}",
                    )
                )

        summary_rows = [
            {
                "Profile ID": self.profile.profile_id,
                "Profile Version": self.profile.profile_version,
                "Institution": self.profile.institution,
                "PDF File": item.pdf_file.name,
                "Status": item.status,
                "Transactions": item.transaction_count,
                "Pages": ", ".join(map(str, item.source_pages)),
                "Output CSV": str(item.output_csv) if item.output_csv else "",
                "Error": item.error or "",
            }
            for item in file_results
        ]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_csv = destination / f"batch_processing_summary_{timestamp}.csv"
        pd.DataFrame(summary_rows).to_csv(summary_csv, index=False)

        return BatchResult(
            files=tuple(file_results),
            summary_csv=summary_csv,
            profile_id=self.profile.profile_id,
            profile_name=self.profile.display_name,
        )
