from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
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

logger = logging.getLogger(__name__)

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
DATE_RE = re.compile(rf"^{MONTHS}\s+\d{{1,2}}$")
AMOUNT_RE = re.compile(r"^-?\$?\d[\d,]*\.\d{2}(?:\*+)?$")
SIMPLII_TOTAL_OUT_RE = re.compile(
    r"total\s+funds\s+out\s+([\d,]+\.\d{2})", re.IGNORECASE
)
SIMPLII_TOTAL_IN_RE = re.compile(
    r"total\s+funds\s+in\s+([\d,]+\.\d{2})", re.IGNORECASE
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
SUPPORTED_PARSERS = {
    "cibc_credit_card",
    "simplii_chequing_account",
    "td_visa_credit_card",
}


class PDFProcessingError(RuntimeError):
    """Raised when a statement cannot be processed reliably."""


@dataclass(frozen=True)
class ExtractionResult:
    transactions: pd.DataFrame
    source_pages: tuple[int, ...]
    ghostscript_path: str | None


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
        lowered = page_text.lower()
        return all(phrase.lower() in lowered for phrase in self.excluded_page_phrases)

    # CIBC credit-card parser -------------------------------------------------

    def _find_cibc_transaction_header(self, page: fitz.Page) -> dict | None:
        lines = self._group_words_into_lines(page.get_text("words"))

        for index, line in enumerate(lines):
            line_text = " ".join(word[4] for word in line["words"]).lower()

            if not all(
                token in line_text for token in ("description", "spend", "categories")
            ):
                continue

            if "amount" not in line_text:
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

            if not all((trans, post, description, spend, amount)):
                continue

            x_positions = [trans[0], post[0], description[0], spend[0], amount[0]]
            if x_positions != sorted(x_positions):
                continue

            return {
                "trans": trans[0],
                "post": post[0],
                "description": description[0],
                "spend": spend[0],
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
            balance_valid = bool(AMOUNT_RE.fullmatch(cells["balance"]))

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
                    "Balance": cells["balance"].replace("$", "").rstrip("*"),
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

    # Shared processing -------------------------------------------------------

    def extract_transactions(self, pdf_path: str | Path) -> ExtractionResult:
        source_path = Path(pdf_path)
        if not source_path.is_file():
            raise PDFProcessingError(f"PDF file not found: {source_path}")

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
        td_previous_balance: Decimal | None = None
        td_new_balance: Decimal | None = None

        try:
            with fitz.open(source_path) as document:
                for page_number, page in enumerate(document, start=1):
                    page_text = page.get_text("text")
                    total_text_characters += len(page_text.strip())

                    if self.profile.parser == "cibc_credit_card":
                        page_rows = self._extract_cibc_page_transactions(page)
                    elif self.profile.parser == "simplii_chequing_account":
                        total_out_match = SIMPLII_TOTAL_OUT_RE.search(page_text)
                        total_in_match = SIMPLII_TOTAL_IN_RE.search(page_text)
                        if total_out_match:
                            simplii_total_out = self._parse_amount(total_out_match.group(1))
                        if total_in_match:
                            simplii_total_in = self._parse_amount(total_in_match.group(1))
                        page_rows = self._extract_simplii_page_transactions(page)
                    else:
                        (
                            page_previous_balance,
                            page_new_balance,
                        ) = self._extract_td_statement_balances(page)
                        if page_previous_balance is not None:
                            td_previous_balance = page_previous_balance
                        if page_new_balance is not None:
                            td_new_balance = page_new_balance
                        page_rows = self._extract_td_page_transactions(page)

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

            raise PDFProcessingError(
                f"The PDF contains readable text, but no transaction table matching "
                f"profile '{self.profile.display_name}' was found."
            )

        transactions = pd.DataFrame(all_rows, columns=self.required_headers)

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

        return ExtractionResult(
            transactions=transactions,
            source_pages=tuple(source_pages),
            ghostscript_path=ghostscript_path,
        )

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
