from __future__ import annotations

import argparse
from pathlib import Path

import fitz


def analyze_pdf(pdf_path: Path) -> None:
    print()
    print("=" * 70)
    print(pdf_path)

    with fitz.open(pdf_path) as document:
        print(f"Pages: {len(document)}")

        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            words = page.get_text("words")
            images = page.get_images(full=True)

            print(
                f"Page {page_number}: "
                f"{len(text)} text characters, "
                f"{len(words)} positioned words, "
                f"{len(images)} images"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report whether PDF pages contain readable text."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default="input",
        help="Folder to scan recursively (default: input).",
    )
    args = parser.parse_args()

    source_folder = Path(args.folder)
    if not source_folder.is_dir():
        raise SystemExit(f"Folder not found: {source_folder}")

    pdf_files = sorted(
        path for path in source_folder.rglob("*.pdf") if path.is_file()
    )
    if not pdf_files:
        raise SystemExit(f"No PDF files found under: {source_folder}")

    for pdf_path in pdf_files:
        analyze_pdf(pdf_path)


if __name__ == "__main__":
    main()
