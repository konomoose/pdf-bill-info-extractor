from __future__ import annotations

import argparse
import re
from pathlib import Path

import fitz

HEADER_TOKENS = {"transaction", "posting", "activity", "description", "amount", "date"}
STOP_PHRASES = ("total new balance", "td message centre")


def mask_text(text: str) -> str:
    """Mask digits while preserving punctuation and word/token layout."""
    return re.sub(r"\d", "#", text)


def group_words(words: list[tuple], tolerance: float = 2.5) -> list[dict]:
    ordered = sorted(words, key=lambda w: (((w[1] + w[3]) / 2), w[0]))
    lines: list[dict] = []

    for word in ordered:
        y_center = (word[1] + word[3]) / 2
        if not lines or abs(y_center - lines[-1]["y_center"]) > tolerance:
            lines.append({"y_center": y_center, "words": [word]})
        else:
            lines[-1]["words"].append(word)
            lines[-1]["y_center"] = sum(
                (item[1] + item[3]) / 2 for item in lines[-1]["words"]
            ) / len(lines[-1]["words"])

    for line in lines:
        line["words"].sort(key=lambda w: w[0])

    return lines


def line_text(line: dict) -> str:
    return " ".join(str(word[4]) for word in line["words"])


def find_header_window(lines: list[dict]) -> tuple[int, int] | None:
    # Try 1 to 5 adjacent visual lines because PDF headers are often split.
    for window_size in range(1, 6):
        for start in range(0, len(lines) - window_size + 1):
            end = start + window_size
            combined = " ".join(line_text(line) for line in lines[start:end]).lower()
            if HEADER_TOKENS.issubset(set(re.findall(r"[a-z]+", combined))):
                return start, end
    return None


def diagnose(pdf_path: Path) -> None:
    with fitz.open(pdf_path) as document:
        page = document[0]
        words = page.get_text("words")
        lines = group_words(words)

        print(f"PDF: {pdf_path.name}")
        print(f"Pages: {len(document)}")
        print(
            f"Page 1: {len(page.get_text('text'))} text characters, "
            f"{len(words)} positioned words, {len(page.get_images(full=True))} images"
        )
        print(
            f"Page size: width={page.rect.width:.2f}, "
            f"height={page.rect.height:.2f}"
        )
        print()

        header_window = find_header_window(lines)
        if header_window is None:
            print("HEADER WINDOW: NOT FOUND")
            print()
            print("Lines containing any expected header token:")
            for index, line in enumerate(lines):
                lowered = line_text(line).lower()
                if any(token in lowered for token in HEADER_TOKENS):
                    print(
                        f"line={index:03d} y={line['y_center']:.2f} "
                        f"text={mask_text(line_text(line))!r}"
                    )
            return

        start, end = header_window
        print(f"HEADER WINDOW: lines {start} through {end - 1}")
        print()

        # Include only the transaction-table area, not personal data above it.
        for index in range(start, len(lines)):
            line = lines[index]
            original = line_text(line)
            lowered = original.lower()

            print(
                f"LINE {index:03d} y={line['y_center']:.2f} "
                f"text={mask_text(original)!r}"
            )
            for word in line["words"]:
                print(
                    "  "
                    f"x0={word[0]:7.2f} y0={word[1]:7.2f} "
                    f"x1={word[2]:7.2f} y1={word[3]:7.2f} "
                    f"text={mask_text(str(word[4]))!r}"
                )

            if any(phrase in lowered for phrase in STOP_PHRASES):
                break


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a digit-masked TD Visa page-1 table-layout diagnostic. "
            "Only the table area from the detected header onward is printed."
        )
    )
    parser.add_argument("pdf", type=Path, help="Path to the original text-readable PDF")
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    diagnose(args.pdf)


if __name__ == "__main__":
    main()
