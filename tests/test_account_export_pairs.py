from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from src.bill_extractor.institution_collector import (
    InstitutionCollectionError, collect_profile_account_statement_csvs,
    _normalized_statement_paths,
)
from src.bill_extractor.profile_loader import load_profile
from src.bill_extractor.transaction_normalizer import NORMALIZED_COLUMNS
from src.bill_extractor.workflow import _load_normalized_statement_frames
from src.bill_extractor.yearly_consolidator import write_paired_yearly_transaction_csvs
from test_gui_workflow import GUI_MODULE


class AccountExportPairsTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows may supply a short (8.3) TEMP path; match the collector's
        # resolved paths for assertions and the simulated commit failure.
        self.root = Path(self.temp.name).resolve() / 'simplii'
        self.root.mkdir()
        self.profile = replace(load_profile('config/profiles/simplii_bank_account.json'), output_folder=self.root)
        self.raw_path = self.root / 'April_transactions.csv'
        self.normal_path = self.root / 'April_normalized_transactions.csv'
        self.original_all = self.root / 'simplii_all_transactions.csv'
        self.normal_all = self.root / 'simplii_all_normalized_transactions.csv'
        self.manifest = self.root / '.account_collection_manifest.json'
        self.normal = pd.DataFrame('', index=range(3), columns=NORMALIZED_COLUMNS)
        self.normal['transaction_date'] = '2021-04-15'
        self.normal['description'] = ['WITHDRAWAL', 'DEPOSIT', 'WITHDRAWAL']
        self.normal['withdrawal'] = ['10.00', '', '10.00']
        self.normal['deposit'] = ['', '10.00', '']
        self.normal['balance'] = ['90.00', '100.00', '90.00']
        self.normal['profile_id'] = self.profile.profile_id
        self.normal['institution'] = self.profile.institution
        self.normal['source_file'] = 'April.pdf'
        self.raw = pd.DataFrame([
            ['Apr 15', '', 'WITHDRAWAL', '10.00', '', '90.00'],
            ['Apr 15', '', 'DEPOSIT', '', '10.00', '100.00'],
            ['Apr 15', '', 'WITHDRAWAL', '10.00', '', '90.00'],
        ], columns=self.profile.required_headers)
        self.raw.to_csv(self.raw_path, index=False)
        self.normal.to_csv(self.normal_path, index=False)

    def seed_legacy(self):
        self.normal.to_csv(self.original_all, index=False)
        self.manifest.write_text(json.dumps({
            'version': 1, 'entries': [], 'managed_artifacts': [{
                'kind': 'combined_transactions', 'path': self.original_all.name,
                'transaction_count': 3,
            }],
        }))

    def snapshots(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def read(self, path):
        return pd.read_csv(path, dtype=str, keep_default_na=False)

    def test_upgrade_repeat_and_scanners_preserve_all_rows(self):
        self.seed_legacy()
        monthly_before = [self.raw_path.read_bytes(), self.normal_path.read_bytes()]
        for _ in range(2):
            result = collect_profile_account_statement_csvs(self.profile)
            self.assertEqual(result.original_csv_path, self.original_all)
            self.assertEqual(result.combined_csv_path, self.normal_all)
            self.assertEqual(result.combined_transaction_count, 3)
            expected = self.raw.assign(profile_name=self.profile.display_name)
            pd.testing.assert_frame_equal(self.read(self.original_all), expected)
            pd.testing.assert_frame_equal(self.read(self.normal_all), self.normal)
            pd.testing.assert_frame_equal(self.read(self.root/'2021/simplii_2021_transactions.csv'), expected)
            self.assertEqual(_normalized_statement_paths(self.root), [self.normal_path])
            frames = _load_normalized_statement_frames(self.root, (self.profile,))
            self.assertEqual(len(frames), 1)
            self.assertEqual(len(frames[0]), 3)
            self.assertEqual(monthly_before, [self.raw_path.read_bytes(), self.normal_path.read_bytes()])
            messages = '\n'.join(GUI_MODULE.account_collection_result_messages(result))
            self.assertIn('Original-column all-years CSV:', messages)
            self.assertIn('Normalized all-years CSV:', messages)
            self.assertIn('profile_name as the last column', messages)

    def test_legacy_normalized_only_moves_to_explicit_filename(self):
        self.seed_legacy()
        self.raw_path.unlink()
        result = collect_profile_account_statement_csvs(self.profile)
        self.assertIsNone(result.original_csv_path)
        self.assertTrue(result.yearly_refresh_warnings)
        self.assertFalse(self.original_all.exists())
        pd.testing.assert_frame_equal(self.read(self.normal_all), self.normal)
        self.raw.to_csv(self.raw_path, index=False)
        result = collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(result.original_csv_path, self.original_all)
        self.assertEqual(result.yearly_refresh_warnings, ())

    def test_missing_original_preserves_previously_generated_originals(self):
        collect_profile_account_statement_csvs(self.profile)
        before = self.original_all.read_bytes()
        self.raw_path.unlink()
        result = collect_profile_account_statement_csvs(self.profile)
        self.assertIsNone(result.original_csv_path)
        self.assertTrue(result.yearly_refresh_warnings)
        self.assertEqual(self.original_all.read_bytes(), before)
        self.assertTrue(self.normal_all.exists())

    def test_new_normalized_filename_collision_is_not_overwritten(self):
        self.seed_legacy()
        self.normal_all.write_text('private,user,file\n')
        before = self.snapshots()
        with self.assertRaisesRegex(InstitutionCollectionError, 'unmanaged'):
            collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(self.snapshots(), before)

    def test_upgrade_failure_rolls_back_both_formats_and_manifest(self):
        self.seed_legacy()
        before = self.snapshots()
        original_replace = Path.replace
        def fail_manifest(source, target):
            if source.suffix == '.tmp' and target == self.manifest:
                raise OSError('simulated commit failure')
            return original_replace(source, target)
        with patch.object(Path, 'replace', fail_manifest):
            with self.assertRaises(InstitutionCollectionError):
                collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(self.snapshots(), before)

    def test_normalized_only_migration_failure_restores_legacy_file(self):
        self.seed_legacy()
        self.raw_path.unlink()
        before = self.snapshots()
        with patch('src.bill_extractor.institution_collector._write_manifest_temp',
                   side_effect=OSError('simulated staging failure')):
            with self.assertRaises(OSError):
                collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(self.snapshots(), before)

    def test_yearly_profile_column_uses_each_rows_profile(self):
        other = self.normal.copy()
        other['profile_id'] = 'second_profile'
        names = {self.profile.profile_id: self.profile.display_name,
                 'second_profile': 'Second Account'}
        paths = write_paired_yearly_transaction_csvs(
            [self.normal, other], [self.raw, self.raw.copy()],
            output_root=self.root/'shared', institution_slug='shared', profile_names=names,
        )
        raw = self.read(paths[2021])
        self.assertEqual(raw.columns[-1], 'profile_name')
        self.assertEqual(raw.profile_name.tolist(), [self.profile.display_name]*3 + ['Second Account']*3)
        self.assertEqual(len(raw), 6)

    def test_empty_account_writes_both_header_only_formats(self):
        self.raw_path.unlink()
        self.normal_path.unlink()
        result = collect_profile_account_statement_csvs(self.profile)
        self.assertEqual(result.combined_transaction_count, 0)
        self.assertEqual(list(self.read(self.original_all).columns), [*self.profile.required_headers, 'profile_name'])
        self.assertEqual(list(self.read(self.normal_all).columns), list(NORMALIZED_COLUMNS))


if __name__ == '__main__':
    unittest.main()
