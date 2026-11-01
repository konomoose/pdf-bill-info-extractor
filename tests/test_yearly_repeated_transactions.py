from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from src.bill_extractor.analysis_summary import build_analysis
from src.bill_extractor.institution_collector import collect_profile_account_statement_csvs
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import NORMALIZED_COLUMNS
from src.bill_extractor.yearly_consolidator import (
    consolidate_normalized_transactions,
    write_paired_yearly_transaction_csvs,
    write_yearly_transaction_csvs,
)


class YearlyRepeatedTransactionsTest(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile('config/profiles/simplii_bank_account.json')
        # Two real matching withdrawals, separated by an equal deposit.
        self.normal = pd.DataFrame('', index=range(3), columns=NORMALIZED_COLUMNS)
        self.normal['transaction_date'] = '2021-04-15'
        self.normal['description'] = ['TEST WITHDRAWAL', 'TEST DEPOSIT', 'TEST WITHDRAWAL']
        self.normal['withdrawal'] = ['10.00', '', '10.00']
        self.normal['deposit'] = ['', '10.00', '']
        self.normal['balance'] = ['90.00', '100.00', '90.00']
        self.normal['source_file'] = '2021/April.pdf'
        self.normal['profile_id'] = self.profile.profile_id
        self.normal['institution'] = self.profile.institution
        self.normal['account_type'] = self.profile.document_type
        self.raw = pd.DataFrame([
            ['Apr 15', '', 'TEST WITHDRAWAL', '10.00', '', '90.00'],
            ['Apr 15', '', 'TEST DEPOSIT', '', '10.00', '100.00'],
            ['Apr 15', '', 'TEST WITHDRAWAL', '10.00', '', '90.00'],
        ], columns=self.profile.required_headers)

    def test_nonadjacent_equal_transactions_in_one_statement_are_preserved(self):
        result = consolidate_normalized_transactions([self.normal])[2021]
        pd.testing.assert_frame_equal(result, self.normal)
        self.assertEqual(sum(Decimal(v) for v in result.withdrawal if v), Decimal('20.00'))

    def test_repeated_statement_copy_does_not_multiply_transactions(self):
        result = consolidate_normalized_transactions([
            self.normal.iloc[:0], self.normal, self.normal.copy(),
        ])[2021]
        pd.testing.assert_frame_equal(result, self.normal)

    def test_different_source_keeps_all_occurrences(self):
        other = self.normal.copy()
        other['source_file'] = '2021/Another-statement.pdf'
        result = consolidate_normalized_transactions([self.normal, other])[2021]
        self.assertEqual(len(result), 6)
        self.assertEqual(result.groupby('source_file').size().tolist(), [3, 3])

    def test_both_csv_writers_preserve_rows_and_headers(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            paths = write_yearly_transaction_csvs(
                [self.normal, self.normal.copy()], output_root=root/'legacy',
                institution_slug='simplii',
            )
            actual = pd.read_csv(paths[2021], dtype=str, keep_default_na=False)
            pd.testing.assert_frame_equal(actual, self.normal)
            for _ in range(2):
                paths = write_paired_yearly_transaction_csvs(
                    [self.normal.iloc[:0], self.normal, self.normal.copy()],
                    [self.raw.iloc[:0], self.raw, self.raw.copy()],
                    output_root=root/'paired', institution_slug='simplii',
                )
                raw = pd.read_csv(paths[2021], dtype=str, keep_default_na=False)
                normalized = pd.read_csv(
                    paths[2021].with_name('simplii_2021_normalized_transactions.csv'),
                    dtype=str, keep_default_na=False,
                )
                pd.testing.assert_frame_equal(raw, self.raw)
                pd.testing.assert_frame_equal(normalized, self.normal)

    def test_collection_through_analysis_keeps_counts_and_money_totals(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            output = root/'csv_output'/'simplii'
            statement_folder = output/'2021'
            statement_folder.mkdir(parents=True)
            profile = replace(self.profile, output_folder=output)
            self.normal.to_csv(statement_folder/'April_normalized_transactions.csv', index=False)
            self.raw.to_csv(statement_folder/'April_transactions.csv', index=False)
            for _ in range(2):
                result = collect_profile_account_statement_csvs(profile)
                self.assertEqual(result.combined_transaction_count, 3)
                self.assertEqual(result.yearly_refresh_warnings, ())
                build_analysis([profile], output_root=root/'analysis')
                analysis = pd.read_csv(
                    root/'analysis'/profile.profile_id/'simplii_all_years_analysis.csv',
                    dtype=str, keep_default_na=False,
                )
                self.assertEqual(len(analysis), 3)
                self.assertEqual(sum(map(Decimal, analysis.money_in)), Decimal('10.00'))
                self.assertEqual(sum(map(Decimal, analysis.money_out)), Decimal('20.00'))
                self.assertEqual(sum(map(Decimal, analysis.net_amount)), Decimal('-10.00'))


if __name__ == '__main__':
    unittest.main()
