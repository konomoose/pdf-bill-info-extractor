from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
from typing import TextIO

from .profile_loader import (
    ExtractionProfile,
    ProfileError,
    discover_profiles,
)
from .workflow import (
    WorkflowError,
    WorkflowResult,
    run_extraction_workflow,
)


class CLIError(ValueError):
    """Raised when command-line profile selection is invalid."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-bill-extractor",
        description=(
            "Extract statement transactions, create normalized "
            "per-statement CSVs, and create consolidated yearly CSVs."
        ),
    )

    selection = parser.add_mutually_exclusive_group()

    selection.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available extraction profiles without processing PDFs.",
    )
    selection.add_argument(
        "--all",
        dest="all_profiles",
        action="store_true",
        help="Process every available extraction profile.",
    )
    selection.add_argument(
        "--profile",
        dest="profile_ids",
        action="append",
        metavar="PROFILE_ID",
        help=(
            "Process one profile. Repeat this option to process "
            "multiple profiles."
        ),
    )

    parser.add_argument(
        "--summary-root",
        type=Path,
        default=None,
        help=(
            "Folder for the workflow summary CSV. "
            "Defaults to csv_output."
        ),
    )

    return parser


def select_profiles(
    profiles: Sequence[ExtractionProfile],
    *,
    process_all: bool,
    requested_ids: Sequence[str] | None,
) -> tuple[ExtractionProfile, ...]:
    if process_all:
        return tuple(profiles)

    if not requested_ids:
        raise CLIError(
            "Choose --list-profiles, --all, or at least one "
            "--profile PROFILE_ID."
        )

    profiles_by_id = {
        profile.profile_id: profile
        for profile in profiles
    }

    unknown = sorted(
        {
            profile_id
            for profile_id in requested_ids
            if profile_id not in profiles_by_id
        }
    )

    if unknown:
        raise CLIError(
            "Unknown profile ID"
            + ("s" if len(unknown) != 1 else "")
            + ": "
            + ", ".join(unknown)
            + "."
        )

    selected: list[ExtractionProfile] = []
    seen: set[str] = set()

    for profile_id in requested_ids:
        if profile_id in seen:
            continue

        selected.append(
            profiles_by_id[profile_id]
        )
        seen.add(profile_id)

    return tuple(selected)


def print_profiles(
    profiles: Sequence[ExtractionProfile],
    output: TextIO,
) -> None:
    print("AVAILABLE PROFILES", file=output)

    for profile in profiles:
        print(
            f"{profile.profile_id} | "
            f"{profile.display_name}",
            file=output,
        )

    print(
        f"Total profiles: {len(profiles)}",
        file=output,
    )


def print_result(
    result: WorkflowResult,
    output: TextIO,
) -> None:
    print("WORKFLOW COMPLETE", file=output)
    print(
        f"Files:        {len(result.files)}",
        file=output,
    )
    print(
        f"Successful:   {result.successful_count}",
        file=output,
    )
    print(
        f"Failed:       {result.failed_count}",
        file=output,
    )
    print(
        f"Skipped:      {result.skipped_count}",
        file=output,
    )
    print(
        f"Transactions: {result.transaction_count}",
        file=output,
    )
    print(
        f"Yearly CSVs:  {len(result.yearly_outputs)}",
        file=output,
    )
    print(
        f"Summary CSV:  {result.summary_csv}",
        file=output,
    )

    failures = [
        item
        for item in result.files
        if item.status == "Failed"
    ]

    if failures:
        print("FAILURES", file=output)

        for item in failures:
            source_name = (
                item.pdf_file.name
                if item.pdf_file is not None
                else "(profile)"
            )

            print(
                f"{item.profile_id} | "
                f"{source_name} | "
                f"{item.error or 'Unknown error'}",
                file=output,
            )


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        profiles = discover_profiles()
    except ProfileError as exc:
        print(
            f"Profile error: {exc}",
            file=error_output,
        )
        return 2

    if not profiles:
        print(
            "No extraction profiles were found.",
            file=error_output,
        )
        return 2

    if args.list_profiles:
        print_profiles(profiles, output)
        return 0

    try:
        selected_profiles = select_profiles(
            profiles,
            process_all=args.all_profiles,
            requested_ids=args.profile_ids,
        )
    except CLIError as exc:
        print(
            f"Selection error: {exc}",
            file=error_output,
        )
        return 2

    print(
        "Selected profiles: "
        f"{len(selected_profiles)}",
        file=output,
    )

    try:
        result = run_extraction_workflow(
            selected_profiles,
            summary_root=args.summary_root,
        )
    except (
        WorkflowError,
        ProfileError,
        OSError,
        ValueError,
    ) as exc:
        print(
            f"Workflow error: {exc}",
            file=error_output,
        )
        return 1

    print_result(result, output)

    return 1 if result.failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
