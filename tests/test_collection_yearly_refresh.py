from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from src.bill_extractor.institution_collector import (
    collect_profile_account_statement_csvs, InstitutionCollectionError,
)
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import NORMALIZED_COLUMNS
from test_gui_workflow import GUI_MODULE


class CollectionYearlyRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'account'
        self.root.mkdir()
        self.profile = replace(
            load_profile('config/profiles/simplii_bank_account.json'),
            output_folder=self.root,
        )
        self.normal_path = self.root / 'January_normalized_transactions.csv'
        self.raw_path = self.root / 'January_transactions.csv'
        self.write_pair()

    def write_pair(self, year=2023):
        normal = pd.DataFrame('', index=range(1), columns=NORMALIZED_COLUMNS)
        normal.loc[0, ['transaction_date', 'description', 'withdrawal',
                       'profile_id', 'institution', 'source_file']] = [
            f'{year}-01-04', 'TEST', '10.00', self.profile.profile_id,
            self.profile.institution, 'January.pdf',
        ]
        normal.to_csv(self.normal_path, index=False)
        pd.DataFrame([['Jan 04', '', 'TEST', '10.00', '', '90.00']],
                     columns=self.profile.required_headers).to_csv(self.raw_path, index=False)

    def outputs(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and p not in (self.raw_path, self.normal_path)}

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.root.rglob('*.tmp')), [])
        self.assertEqual(list(self.root.rglob('*.bak')), [])

    def test_missing_original_preserves_yearly_and_warns_then_recovers(self):
        first = collect_profile_account_statement_csvs(self.profile)
        annual_before = {p: p.read_bytes() for p in self.root.glob('2023/*.csv')}
        self.write_pair(2024)
        self.raw_path.unlink()
        result = collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(result.combined_transaction_count, 1)
        self.assertEqual(len(result.yearly_refresh_warnings), 1)
        self.assertIn('original statement CSV is missing', result.yearly_refresh_warnings[0])
        self.assertEqual(annual_before, {p: p.read_bytes() for p in annual_before})
        self.assertFalse((self.root / '2024').exists())
        combined = pd.read_csv(first.combined_csv_path)
        self.assertEqual(combined.loc[0, 'transaction_date'], '2024-01-04')
        messages = '\n'.join(GUI_MODULE.account_collection_result_messages(result))
        self.assertIn('NOT refreshed', messages)
        self.assertIn('January_transactions.csv', messages)
        self.assertNotIn('CSVs refreshed from current statements', messages)

        self.write_pair(2024)
        result = collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(result.yearly_refresh_warnings, ())
        self.assertEqual(list(self.root.glob('2023/*.csv')), [])
        self.assertEqual(len(list(self.root.glob('2024/*.csv'))), 2)
        self.assert_no_temporary_files()

    def test_bad_original_does_not_corrupt_annual_outputs(self):
        collect_profile_account_statement_csvs(self.profile)
        annual_before = {p: p.read_bytes() for p in self.root.glob('2023/*.csv')}
        for value, reason in [
            ('wrong,headers\n1,2\n', 'unexpected columns'),
            (','.join(self.profile.required_headers) + '\n', 'row counts differ'),
            ('"unterminated', 'could not be read'),
        ]:
            with self.subTest(reason=reason):
                self.raw_path.write_text(value)
                result = collect_profile_account_statement_csvs(self.profile)
                self.assertIn(reason, result.yearly_refresh_warnings[0])
                self.assertEqual(annual_before, {p: p.read_bytes() for p in annual_before})
                self.assertEqual(result.combined_transaction_count, 1)
                self.assert_no_temporary_files()

    def test_late_commit_failure_restores_yearly_all_years_and_manifest(self):
        collect_profile_account_statement_csvs(self.profile)
        before = self.outputs()
        self.write_pair(2024)
        original_replace = Path.replace

        def fail_manifest(source, target):
            if source.suffix == '.tmp' and target.name == '.account_collection_manifest.json':
                raise OSError('simulated manifest failure')
            return original_replace(source, target)

        with patch.object(Path, 'replace', fail_manifest):
            with self.assertRaises(InstitutionCollectionError):
                collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(self.outputs(), before)
        self.assert_no_temporary_files()

    def test_staging_failure_leaves_all_outputs_unchanged(self):
        collect_profile_account_statement_csvs(self.profile)
        before = self.outputs()
        self.write_pair(2024)
        for method in ('_write_combined_temp', '_write_manifest_temp'):
            with self.subTest(method=method), patch(
                'src.bill_extractor.institution_collector.' + method,
                side_effect=OSError('simulated staging failure'),
            ):
                with self.assertRaises(OSError):
                    collect_profile_account_statement_csvs(self.profile)
            self.assertEqual(self.outputs(), before)
            self.assert_no_temporary_files()


if __name__ == '__main__':
    unittest.main()
