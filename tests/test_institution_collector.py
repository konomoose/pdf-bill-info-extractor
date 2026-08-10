from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import pandas as pd

from src.bill_extractor.institution_collector import (
    InstitutionCollectionError,
    collect_institution_statement_csvs,
    profiles_for_institution,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
)
from src.bill_extractor.transaction_normalizer import (
    NORMALIZED_COLUMNS,
)


RAW_COLUMNS = (
    "Transaction date",
    "Posting date",
    "Description",
    "Amount",
)


def make_profile(
    root: Path,
    output_root: Path,
    *,
    profile_id: str,
    institution: str = "Test Bank",
    display_name: str | None = None,
) -> ExtractionProfile:
    return ExtractionProfile(
        profile_id=profile_id,
        display_name=display_name or profile_id,
        profile_version=1,
        institution=institution,
        document_type="credit_card_statement",
        parser="capital_one_mastercard",
        input_folder=root / "unused_input",
        output_folder=output_root,
        file_pattern="*.pdf",
        recursive=True,
        preserve_subfolders=True,
        required_headers=RAW_COLUMNS,
        excluded_page_phrases=(),
        line_tolerance=2.5,
        continuation_gap=18.0,
        source_path=Path("config/profiles/test.json"),
    )


def write_file(
    path: Path,
    content: bytes,
) -> Path:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_bytes(content)
    return path


def write_normalized(
    output_root: Path,
    relative_path: str,
    content: bytes,
) -> Path:
    return write_file(
        output_root / relative_path,
        content,
    )


def read_manifest(path: Path) -> dict[str, object]:
    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def make_row(
    transaction_date: str,
    description: str,
    *,
    posting_date: str = "",
    effective_date: str = "",
    amount: str = "",
    withdrawal: str = "",
    deposit: str = "",
    payment: str = "",
    interest_fees_insurance: str = "",
    balance: str = "",
    category: str = "",
    institution: str = "Test Bank",
    account_type: str = "credit_card_statement",
    profile_id: str = "profile_v1",
    source_file: str = "private/source.pdf",
) -> dict[str, str]:
    row = {
        column: ""
        for column in NORMALIZED_COLUMNS
    }
    row.update(
        {
            "transaction_date": transaction_date,
            "posting_date": posting_date,
            "effective_date": effective_date,
            "description": description,
            "category": category,
            "amount": amount,
            "withdrawal": withdrawal,
            "deposit": deposit,
            "payment": payment,
            "interest_fees_insurance": interest_fees_insurance,
            "balance": balance,
            "institution": institution,
            "account_type": account_type,
            "profile_id": profile_id,
            "source_file": source_file,
        }
    )
    return row


def normalized_bytes(
    rows: list[dict[str, str]],
) -> bytes:
    frame = pd.DataFrame(
        rows,
        columns=list(NORMALIZED_COLUMNS),
    )
    return frame.to_csv(
        index=False,
        lineterminator="\n",
    ).encode("utf-8")


def read_combined(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
    )


COMBINED_COLUMNS = [
    "institution",
    "profile_id",
    "profile_display_name",
    "account_type",
    "source_statement",
    "source_relative_path",
    "transaction_date",
    "posting_date",
    "effective_date",
    "description",
    "category",
    "amount",
    "withdrawal",
    "deposit",
    "payment",
    "interest_fees_insurance",
    "balance",
]


class InstitutionCollectorTest(unittest.TestCase):
    def test_groups_profiles_by_normalized_institution(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_profile(
                root,
                root / "first",
                profile_id="first_v1",
                institution="Test  Bank",
            )
            second = make_profile(
                root,
                root / "second",
                profile_id="second_v1",
                institution=" test bank ",
            )
            other = make_profile(
                root,
                root / "other",
                profile_id="other_v1",
                institution="Other Bank",
            )

            result = profiles_for_institution(
                [other, second, first],
                "TEST BANK",
            )

            self.assertEqual(
                [
                    profile.profile_id
                    for profile in result
                ],
                ["first_v1", "second_v1"],
            )

    def test_collects_multiple_profiles_flat_with_identity(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            card_root = root / "card_output"
            chequing_root = root / "chequing_output"
            other_root = root / "other_output"
            collection_base = root / "collections"

            card = make_profile(
                root,
                card_root,
                profile_id="card_profile_v1",
            )
            chequing = make_profile(
                root,
                chequing_root,
                profile_id="chequing_profile_v1",
            )
            other = make_profile(
                root,
                other_root,
                profile_id="other_profile_v1",
                institution="Other Bank",
            )

            card_source = write_normalized(
                card_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-03-14",
                            "Card purchase",
                            amount="10.00",
                            profile_id="card_profile_v1",
                        )
                    ]
                ),
            )
            chequing_source = write_normalized(
                chequing_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-03-15",
                            "Chequing deposit",
                            deposit="50.00",
                            account_type=(
                                "bank_account_statement"
                            ),
                            profile_id=(
                                "chequing_profile_v1"
                            ),
                        )
                    ]
                ),
            )
            write_normalized(
                other_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-03-16",
                            "Other bank",
                            amount="1.00",
                            institution="Other Bank",
                            profile_id="other_profile_v1",
                        )
                    ]
                ),
            )

            write_file(
                card_root
                / "2025"
                / "statement_transactions.csv",
                b"raw bytes\n",
            )
            write_file(
                card_root
                / "2025"
                / "card_2025_transactions.csv",
                b"yearly bytes\n",
            )
            write_file(
                card_root
                / "workflow_summary_20250101.csv",
                b"summary bytes\n",
            )
            write_file(
                card_root / "notes.csv",
                b"unrelated bytes\n",
            )

            result = collect_institution_statement_csvs(
                [other, chequing, card],
                "Test Bank",
                collection_base=collection_base,
            )

            self.assertEqual(
                result.collected_count,
                2,
            )
            self.assertEqual(
                result.stale_removed_count,
                0,
            )
            self.assertEqual(
                result.institution_slug,
                "test_bank",
            )

            collected_paths = sorted(
                result.all_statements_folder.iterdir(),
                key=lambda path: path.name,
            )
            self.assertEqual(
                len(collected_paths),
                2,
            )
            self.assertTrue(
                all(
                    path.is_file()
                    for path in collected_paths
                )
            )
            self.assertTrue(
                all(
                    "__2025__" in path.name
                    for path in collected_paths
                )
            )
            self.assertTrue(
                any(
                    "card_profile_v1" in path.name
                    for path in collected_paths
                )
            )
            self.assertTrue(
                any(
                    "chequing_profile_v1" in path.name
                    for path in collected_paths
                )
            )
            self.assertEqual(
                len(
                    {
                        path.name
                        for path in collected_paths
                    }
                ),
                2,
            )
            self.assertEqual(
                {
                    path.read_bytes()
                    for path in collected_paths
                },
                {
                    card_source.read_bytes(),
                    chequing_source.read_bytes(),
                },
            )

            manifest = read_manifest(
                result.manifest_path
            )
            entries = manifest["entries"]
            self.assertEqual(
                len(entries),
                2,
            )
            combined = read_combined(
                result.combined_csv_path
            )
            self.assertEqual(
                list(combined.columns),
                COMBINED_COLUMNS,
            )
            self.assertEqual(
                result.combined_transaction_count,
                2,
            )
            self.assertEqual(
                list(combined["description"]),
                [
                    "Card purchase",
                    "Chequing deposit",
                ],
            )
            self.assertNotIn(
                "source_file",
                combined.columns,
            )
            self.assertEqual(
                [
                    entry["destination_path"]
                    for entry in entries
                ],
                sorted(
                    entry["destination_path"]
                    for entry in entries
                ),
            )

    def test_projected_normalized_source_csv_is_expanded_for_combined_csv(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "csv_output" / "tangerine_chequing"
            collection_base = root / "collections"
            profile = replace(
                make_profile(
                    root,
                    output_root,
                    profile_id="tangerine_chequing_account_v1",
                    institution="Tangerine Bank",
                    display_name="Tangerine Chequing",
                ),
                document_type="bank_account_statement",
                normalized_output_columns=(
                    "transaction_date",
                    "description",
                    "withdrawal",
                    "deposit",
                    "balance",
                ),
            )

            write_normalized(
                output_root,
                "statement_normalized_transactions.csv",
                (
                    "transaction_date,description,withdrawal,deposit,balance\n"
                    "2026-01-10,Coffee,12.34,,987.66\n"
                ).encode("utf-8"),
            )

            result = collect_institution_statement_csvs(
                [profile],
                "Tangerine Bank",
                collection_base=collection_base,
            )

            combined = read_combined(
                result.combined_csv_path
            )

            self.assertEqual(
                result.combined_transaction_count,
                1,
            )
            self.assertEqual(
                combined.loc[0, "institution"],
                "Tangerine Bank",
            )
            self.assertEqual(
                combined.loc[0, "profile_id"],
                "tangerine_chequing_account_v1",
            )
            self.assertEqual(
                combined.loc[0, "account_type"],
                "bank_account_statement",
            )
            self.assertEqual(
                combined.loc[0, "withdrawal"],
                "12.34",
            )

    def test_single_profile_institution_is_collected(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "single_output"
            profile = make_profile(
                root,
                output_root,
                profile_id="single_profile_v1",
                institution="Solo Bank",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "solo_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-04-01",
                            "Solo",
                            amount="2.00",
                            institution="Solo Bank",
                            profile_id="single_profile_v1",
                        )
                    ]
                ),
            )

            result = collect_institution_statement_csvs(
                [profile],
                "Solo Bank",
                collection_base=root / "collections",
            )

            self.assertEqual(
                result.profile_ids,
                ("single_profile_v1",),
            )
            self.assertEqual(
                result.collected_count,
                1,
            )
            self.assertEqual(
                result.combined_transaction_count,
                1,
            )
            self.assertEqual(
                len(
                    list(
                        result.all_statements_folder.iterdir()
                    )
                ),
                1,
            )

    def test_repeated_run_is_byte_idempotent(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-05-01",
                            "Same",
                            amount="3.00",
                        )
                    ]
                ),
            )

            first = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            first_manifest = (
                first.manifest_path.read_bytes()
            )
            first_combined = (
                first.combined_csv_path.read_bytes()
            )
            first_files = {
                path.name: path.read_bytes()
                for path in first.all_statements_folder.iterdir()
            }

            second = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            second_manifest = (
                second.manifest_path.read_bytes()
            )
            second_combined = (
                second.combined_csv_path.read_bytes()
            )
            second_files = {
                path.name: path.read_bytes()
                for path in second.all_statements_folder.iterdir()
            }

            self.assertEqual(
                second.collected_count,
                1,
            )
            self.assertEqual(
                second.stale_removed_count,
                0,
            )
            self.assertEqual(
                first_manifest,
                second_manifest,
            )
            self.assertEqual(
                first_combined,
                second_combined,
            )
            self.assertEqual(
                first_files,
                second_files,
            )

    def test_stale_managed_copies_are_removed(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            removed_source = write_normalized(
                output_root,
                (
                    "2025/"
                    "removed_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-06-01",
                            "Removed",
                            amount="4.00",
                        )
                    ]
                ),
            )
            remaining_source = write_normalized(
                output_root,
                (
                    "2025/"
                    "remaining_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-06-02",
                            "Remaining",
                            amount="5.00",
                        )
                    ]
                ),
            )

            first = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            manifest = read_manifest(
                first.manifest_path
            )
            removed_destination = next(
                first.collection_root
                / entry["destination_path"]
                for entry in manifest["entries"]
                if entry["source_relative_path"]
                == "2025/removed_normalized_transactions.csv"
            )

            unrelated = write_file(
                first.all_statements_folder
                / "manual_file.csv",
                b"manual bytes\n",
            )
            removed_source.unlink()

            second = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )

            self.assertEqual(
                second.collected_count,
                1,
            )
            self.assertEqual(
                second.stale_removed_count,
                1,
            )
            combined = read_combined(
                second.combined_csv_path
            )
            self.assertEqual(
                list(combined["description"]),
                ["Remaining"],
            )
            self.assertFalse(
                removed_destination.exists()
            )
            self.assertTrue(
                unrelated.is_file()
            )
            self.assertEqual(
                {
                    path.read_bytes()
                    for path in second.all_statements_folder.iterdir()
                    if path.is_file()
                    and path.name != "manual_file.csv"
                },
                {
                    remaining_source.read_bytes()
                },
            )

    def test_stale_copy_removed_when_profile_leaves_institution(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_root = root / "first"
            second_root = root / "second"
            first = make_profile(
                root,
                first_root,
                profile_id="first_v1",
            )
            second = make_profile(
                root,
                second_root,
                profile_id="second_v1",
            )

            write_normalized(
                first_root,
                (
                    "2025/"
                    "first_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-07-01",
                            "First",
                            amount="6.00",
                            profile_id="first_v1",
                        )
                    ]
                ),
            )
            write_normalized(
                second_root,
                (
                    "2025/"
                    "second_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-07-02",
                            "Second",
                            amount="7.00",
                            profile_id="second_v1",
                        )
                    ]
                ),
            )

            collect_institution_statement_csvs(
                [first, second],
                "Test Bank",
                collection_base=root / "collections",
            )
            changed_second = make_profile(
                root,
                second_root,
                profile_id="second_v1",
                institution="Other Bank",
            )

            result = collect_institution_statement_csvs(
                [first, changed_second],
                "Test Bank",
                collection_base=root / "collections",
            )

            self.assertEqual(
                result.collected_count,
                1,
            )
            self.assertEqual(
                result.stale_removed_count,
                1,
            )
            combined = read_combined(
                result.combined_csv_path
            )
            self.assertEqual(
                list(combined["description"]),
                ["First"],
            )
            self.assertEqual(
                len(
                    [
                        path
                        for path in result.all_statements_folder.iterdir()
                        if path.is_file()
                    ]
                ),
                1,
            )

    def test_missing_manifest_does_not_delete_unrelated_files(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-08-01",
                            "Source",
                            amount="8.00",
                        )
                    ]
                ),
            )

            collection_root = (
                root
                / "collections"
                / "test_bank"
            )
            unrelated = write_file(
                collection_root
                / "all_statements"
                / "manual_file.csv",
                b"manual bytes\n",
            )

            result = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )

            self.assertTrue(
                unrelated.is_file()
            )
            self.assertEqual(
                result.collected_count,
                1,
            )

    def test_malformed_manifest_fails_without_deletion(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-09-01",
                            "Source",
                            amount="9.00",
                        )
                    ]
                ),
            )

            collection_root = (
                root
                / "collections"
                / "test_bank"
            )
            unrelated = write_file(
                collection_root
                / "all_statements"
                / "manual_file.csv",
                b"manual bytes\n",
            )
            write_file(
                collection_root
                / ".collection_manifest.json",
                b"{bad json",
            )

            with self.assertRaisesRegex(
                InstitutionCollectionError,
                "manifest is malformed",
            ):
                collect_institution_statement_csvs(
                    [profile],
                    "Test Bank",
                    collection_base=root / "collections",
                )

            self.assertTrue(
                unrelated.is_file()
            )
            self.assertEqual(
                unrelated.read_bytes(),
                b"manual bytes\n",
            )

    def test_malformed_managed_artifacts_do_not_authorize_deletion(
        self,
    ) -> None:
        cases = [
            (
                "missing kind",
                [
                    {
                        "path": "manual.csv",
                    }
                ],
            ),
            (
                "wrong kind",
                [
                    {
                        "kind": "statement_copy",
                        "path": "manual.csv",
                    }
                ],
            ),
            (
                "missing path",
                [
                    {
                        "kind": "combined_transactions",
                    }
                ],
            ),
            (
                "wrong path",
                [
                    {
                        "kind": "combined_transactions",
                        "path": "manual.csv",
                    }
                ],
            ),
            (
                "conflicting combined paths",
                [
                    {
                        "kind": "combined_transactions",
                        "path": "test_bank_all_transactions.csv",
                    },
                    {
                        "kind": "combined_transactions",
                        "path": "manual.csv",
                    },
                ],
            ),
        ]

        for label, artifacts in cases:
            with self.subTest(label=label):
                with TemporaryDirectory() as directory:
                    root = Path(directory)
                    output_root = root / "output"
                    profile = make_profile(
                        root,
                        output_root,
                        profile_id="profile_v1",
                    )

                    write_normalized(
                        output_root,
                        (
                            "2025/"
                            "statement_normalized_transactions.csv"
                        ),
                        normalized_bytes(
                            [
                                make_row(
                                    "2025-09-01",
                                    "Source",
                                    amount="9.00",
                                )
                            ]
                        ),
                    )

                    collection_root = (
                        root
                        / "collections"
                        / "test_bank"
                    )
                    manual = write_file(
                        collection_root
                        / "manual.csv",
                        b"manual bytes\n",
                    )
                    write_file(
                        collection_root
                        / ".collection_manifest.json",
                        json.dumps(
                            {
                                "all_statements_folder": "all_statements",
                                "managed_artifacts": artifacts,
                                "entries": [],
                                "institution": "Test Bank",
                                "institution_key": "test bank",
                                "institution_slug": "test_bank",
                                "version": 1,
                            },
                            indent=2,
                            sort_keys=True,
                        ).encode("utf-8"),
                    )

                    with self.assertRaisesRegex(
                        InstitutionCollectionError,
                        "manifest is malformed",
                    ):
                        collect_institution_statement_csvs(
                            [profile],
                            "Test Bank",
                            collection_base=root / "collections",
                        )

                    self.assertTrue(
                        manual.is_file()
                    )
                    self.assertEqual(
                        manual.read_bytes(),
                        b"manual bytes\n",
                    )
                    self.assertFalse(
                        (
                            collection_root
                            / "test_bank_all_transactions.csv"
                        ).exists()
                    )

    def test_refuses_to_overwrite_unmanaged_collision(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-10-01",
                            "Source",
                            amount="10.00",
                        )
                    ]
                ),
            )

            first = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            manifest = read_manifest(
                first.manifest_path
            )
            destination = (
                first.collection_root
                / manifest["entries"][0]["destination_path"]
            )
            first.manifest_path.unlink()
            destination.write_bytes(
                b"manual collision\n"
            )

            with self.assertRaisesRegex(
                InstitutionCollectionError,
                "unmanaged collection file",
            ):
                collect_institution_statement_csvs(
                    [profile],
                    "Test Bank",
                    collection_base=root / "collections",
                )

            self.assertEqual(
                destination.read_bytes(),
                b"manual collision\n",
            )

    def test_unsafe_collection_paths_are_rejected(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            cases = [
                output_root,
                output_root / "nested",
                root,
            ]

            for collection_base in cases:
                with self.subTest(
                    collection_base=collection_base
                ):
                    with self.assertRaisesRegex(
                        InstitutionCollectionError,
                        "separate from profile output roots",
                    ):
                        collect_institution_statement_csvs(
                            [profile],
                            "Test Bank",
                            collection_base=collection_base,
                        )

    def test_combined_csv_sorting_provenance_and_no_dedup(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            card_root = root / "card_output"
            bank_root = root / "bank_output"
            loc_root = root / "loc_output"

            card = make_profile(
                root,
                card_root,
                profile_id="card_profile_v1",
                display_name="Card Profile",
            )
            bank = make_profile(
                root,
                bank_root,
                profile_id="bank_profile_v1",
                display_name="Bank Profile",
            )
            loc = make_profile(
                root,
                loc_root,
                profile_id="loc_profile_v1",
                display_name="LOC Profile",
            )

            write_normalized(
                card_root,
                (
                    "2025/"
                    "card_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-03-03",
                            "Later card",
                            posting_date="2025-03-04",
                            amount="10.00",
                            profile_id="card_profile_v1",
                        ),
                        make_row(
                            "2025-03-02",
                            "Duplicate",
                            posting_date="2025-03-03",
                            amount="5.00",
                            profile_id="card_profile_v1",
                        ),
                    ]
                ),
            )
            write_normalized(
                bank_root,
                (
                    "2025/"
                    "shared_name_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-03-01",
                            "Bank first",
                            withdrawal="1.00",
                            account_type=(
                                "bank_account_statement"
                            ),
                            profile_id="bank_profile_v1",
                        ),
                        make_row(
                            "2025-03-01",
                            "Bank second",
                            withdrawal="2.00",
                            account_type=(
                                "bank_account_statement"
                            ),
                            profile_id="bank_profile_v1",
                        ),
                        make_row(
                            "2025-03-02",
                            "Duplicate",
                            posting_date="2025-03-03",
                            amount="5.00",
                            profile_id="bank_profile_v1",
                        ),
                    ]
                ),
            )
            write_normalized(
                loc_root,
                (
                    "2025/"
                    "shared_name_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-03-02",
                            "LOC payment",
                            payment="3.00",
                            interest_fees_insurance="",
                            balance="100.00",
                            account_type=(
                                "line_of_credit_statement"
                            ),
                            profile_id="loc_profile_v1",
                        )
                    ]
                ),
            )

            result = collect_institution_statement_csvs(
                [loc, card, bank],
                "Test Bank",
                collection_base=root / "collections",
            )
            combined = read_combined(
                result.combined_csv_path
            )

            self.assertEqual(
                list(combined.columns),
                COMBINED_COLUMNS,
            )
            self.assertNotIn(
                "source_file",
                combined.columns,
            )
            self.assertEqual(
                result.combined_transaction_count,
                6,
            )
            self.assertEqual(
                list(combined["description"]),
                [
                    "Bank first",
                    "Bank second",
                    "LOC payment",
                    "Duplicate",
                    "Duplicate",
                    "Later card",
                ],
            )
            self.assertEqual(
                list(
                    combined.loc[
                        combined["profile_id"].eq(
                            "bank_profile_v1"
                        ),
                        "description",
                    ]
                )[:2],
                ["Bank first", "Bank second"],
            )
            self.assertEqual(
                list(
                    combined.loc[
                        combined["description"].eq(
                            "Duplicate"
                        ),
                        "profile_id",
                    ]
                ),
                [
                    "bank_profile_v1",
                    "card_profile_v1",
                ],
            )
            self.assertEqual(
                combined.loc[
                    combined["profile_id"].eq(
                        "card_profile_v1"
                    ),
                    "profile_display_name",
                ].iloc[0],
                "Card Profile",
            )
            self.assertEqual(
                set(combined["source_relative_path"]),
                {
                    "2025/card_normalized_transactions.csv",
                    "2025/shared_name_normalized_transactions.csv",
                },
            )
            self.assertIn(
                (
                    "card_profile_v1::2025/"
                    "card_normalized_transactions.csv"
                ),
                set(combined["source_statement"]),
            )
            self.assertIn(
                (
                    "loc_profile_v1::2025/"
                    "shared_name_normalized_transactions.csv"
                ),
                set(combined["source_statement"]),
            )
            self.assertEqual(
                combined.loc[
                    combined["description"].eq(
                        "LOC payment"
                    ),
                    "amount",
                ].iloc[0],
                "",
            )
            self.assertEqual(
                combined.loc[
                    combined["description"].eq(
                        "Bank first"
                    ),
                    "deposit",
                ].iloc[0],
                "",
            )

    def test_repeated_identical_rows_are_preserved(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )
            duplicate = make_row(
                "2025-04-01",
                "Same",
                amount="1.23",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        duplicate,
                        duplicate.copy(),
                    ]
                ),
            )

            result = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            combined = read_combined(
                result.combined_csv_path
            )

            self.assertEqual(
                len(combined),
                2,
            )
            self.assertEqual(
                list(combined["description"]),
                ["Same", "Same"],
            )

    def test_manifest_tracks_combined_csv_without_absolute_paths(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "statement_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-05-01",
                            "Portable",
                            amount="1.00",
                            source_file=(
                                root
                                / "private"
                                / "statement.pdf"
                            ).as_posix(),
                        )
                    ]
                ),
            )

            result = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            manifest_text = result.manifest_path.read_text(
                encoding="utf-8",
            )
            combined_text = result.combined_csv_path.read_text(
                encoding="utf-8",
            )
            manifest = json.loads(manifest_text)

            self.assertNotIn(
                root.resolve().as_posix(),
                manifest_text,
            )
            self.assertNotIn(
                root.resolve().as_posix(),
                combined_text,
            )
            self.assertNotIn(
                "source_path",
                manifest_text,
            )
            self.assertEqual(
                manifest["managed_artifacts"],
                [
                    {
                        "kind": "combined_transactions",
                        "path": "test_bank_all_transactions.csv",
                        "transaction_count": 1,
                    }
                ],
            )

    def test_header_only_combined_csv_for_empty_institution(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = make_profile(
                root,
                root / "output",
                profile_id="profile_v1",
            )

            result = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            combined = read_combined(
                result.combined_csv_path
            )

            self.assertEqual(
                result.collected_count,
                0,
            )
            self.assertEqual(
                result.combined_transaction_count,
                0,
            )
            self.assertEqual(
                list(combined.columns),
                COMBINED_COLUMNS,
            )
            self.assertTrue(combined.empty)

    def test_malformed_source_csv_fails_safely(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )
            write_file(
                output_root
                / "2025"
                / "bad_normalized_transactions.csv",
                b'"unterminated\n',
            )

            with self.assertRaisesRegex(
                InstitutionCollectionError,
                "Could not read normalized source CSV",
            ):
                collect_institution_statement_csvs(
                    [profile],
                    "Test Bank",
                    collection_base=root / "collections",
                )

    def test_missing_normalized_schema_column_fails_safely(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )
            frame = pd.DataFrame(
                [
                    {
                        "transaction_date": "2025-06-01",
                        "description": "Missing columns",
                    }
                ]
            )
            write_file(
                output_root
                / "2025"
                / "bad_normalized_transactions.csv",
                frame.to_csv(
                    index=False,
                    lineterminator="\n",
                ).encode("utf-8"),
            )

            with self.assertRaisesRegex(
                InstitutionCollectionError,
                "unexpected schema",
            ):
                collect_institution_statement_csvs(
                    [profile],
                    "Test Bank",
                    collection_base=root / "collections",
                )

    def test_previous_combined_survives_failed_refresh(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "good_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-07-01",
                            "Good",
                            amount="1.00",
                        )
                    ]
                ),
            )
            first = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            previous_combined = (
                first.combined_csv_path.read_bytes()
            )
            previous_manifest = (
                first.manifest_path.read_bytes()
            )

            write_file(
                output_root
                / "2025"
                / "bad_normalized_transactions.csv",
                b"bad,data\n1,2\n",
            )

            with self.assertRaisesRegex(
                InstitutionCollectionError,
                "unexpected schema",
            ):
                collect_institution_statement_csvs(
                    [profile],
                    "Test Bank",
                    collection_base=root / "collections",
                )

            self.assertEqual(
                first.combined_csv_path.read_bytes(),
                previous_combined,
            )
            self.assertEqual(
                first.manifest_path.read_bytes(),
                previous_manifest,
            )

    def test_combined_generation_failure_does_not_commit_copies(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            profile = make_profile(
                root,
                output_root,
                profile_id="profile_v1",
            )
            write_normalized(
                output_root,
                (
                    "2025/"
                    "first_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-08-01",
                            "First",
                            amount="1.00",
                        )
                    ]
                ),
            )
            first = collect_institution_statement_csvs(
                [profile],
                "Test Bank",
                collection_base=root / "collections",
            )
            previous_combined = (
                first.combined_csv_path.read_bytes()
            )
            previous_manifest = (
                first.manifest_path.read_bytes()
            )

            write_normalized(
                output_root,
                (
                    "2025/"
                    "second_normalized_transactions.csv"
                ),
                normalized_bytes(
                    [
                        make_row(
                            "2025-08-02",
                            "Second",
                            amount="2.00",
                        )
                    ]
                ),
            )

            with patch(
                "src.bill_extractor.institution_collector."
                "_write_combined_temp",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "disk full",
                ):
                    collect_institution_statement_csvs(
                        [profile],
                        "Test Bank",
                        collection_base=root / "collections",
                    )

            copied_names = {
                path.name
                for path in first.all_statements_folder.iterdir()
            }
            self.assertFalse(
                any(
                    "second_normalized_transactions"
                    in name
                    for name in copied_names
                )
            )
            self.assertEqual(
                first.combined_csv_path.read_bytes(),
                previous_combined,
            )
            self.assertEqual(
                first.manifest_path.read_bytes(),
                previous_manifest,
            )

    def test_unknown_institution_has_clear_error(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = make_profile(
                root,
                root / "output",
                profile_id="profile_v1",
            )

            with self.assertRaisesRegex(
                InstitutionCollectionError,
                "No configured profiles",
            ):
                collect_institution_statement_csvs(
                    [profile],
                    "Missing Bank",
                    collection_base=root / "collections",
                )


if __name__ == "__main__":
    unittest.main()
