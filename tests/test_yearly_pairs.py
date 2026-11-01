import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
import pandas as pd
from src.bill_extractor.profile_loader import discover_profiles
from src.bill_extractor.transaction_normalizer import NORMALIZED_COLUMNS
from src.bill_extractor.yearly_consolidator import write_paired_yearly_transaction_csvs, ConsolidationError
from src.bill_extractor.institution_collector import collect_profile_account_statement_csvs, _normalized_statement_paths

class YearlyPairs(unittest.TestCase):
    def frames(self, profile):
        dates = ['2023-01-02', '2022-12-31', '2023-01-03']
        normal = pd.DataFrame('', index=range(3), columns=NORMALIZED_COLUMNS)
        normal['transaction_date'] = dates
        normal['description'] = ['A', 'B', 'C']
        normal['source_file'] = 'January.pdf'
        normal['profile_id'] = profile.profile_id
        normal['institution'] = profile.institution
        raw = pd.DataFrame({c: [f'{c}-A', f'{c}-B', f'{c}-C'] for c in profile.required_headers})
        return normal, raw

    def test_all_profiles_and_repeat_collection(self):
        profiles = discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')
        self.assertEqual(len(profiles), 13)
        for profile in profiles:
            with self.subTest(profile=profile.profile_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'account'
                folder=root/'2023'/'January'; folder.mkdir(parents=True)
                profile=replace(profile, output_folder=root)
                normal, raw=self.frames(profile)
                columns=profile.normalized_output_columns or NORMALIZED_COLUMNS
                normal.loc[:,list(columns)].to_csv(folder/'January_normalized_transactions.csv', index=False)
                raw.to_csv(folder/'January_transactions.csv', index=False)
                for repeat in range(2):
                    result=collect_profile_account_statement_csvs(profile)
                    self.assertEqual(result.combined_transaction_count,3)
                    self.assertEqual(len(_normalized_statement_paths(root)),1)
                    for year, indices in [(2022,[1]),(2023,[0,2])]:
                        actual=pd.read_csv(root/str(year)/f'account_{year}_transactions.csv',dtype=str,keep_default_na=False)
                        pd.testing.assert_frame_equal(actual,raw.iloc[indices].reset_index(drop=True))
                        norm=pd.read_csv(root/str(year)/f'account_{year}_normalized_transactions.csv',dtype=str,keep_default_na=False)
                        self.assertEqual(list(norm.columns),list(columns))
                        self.assertEqual(norm['transaction_date'].tolist(),normal.iloc[indices]['transaction_date'].tolist())

    def test_mismatch_leaves_old_outputs(self):
        profile=discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')[0]
        normal,raw=self.frames(profile)
        with tempfile.TemporaryDirectory() as tmp:
            write_paired_yearly_transaction_csvs([normal],[raw],output_root=tmp,institution_slug='bank')
            before={p:p.read_bytes() for p in Path(tmp).rglob('*.csv')}
            with self.assertRaises(ConsolidationError):
                write_paired_yearly_transaction_csvs([normal],[raw.iloc[:1]],output_root=tmp,institution_slug='bank')
            self.assertEqual(before,{p:p.read_bytes() for p in Path(tmp).rglob('*.csv')})
            write_paired_yearly_transaction_csvs([normal.iloc[:1]],[raw.iloc[:1]],output_root=tmp,institution_slug='bank')
            self.assertEqual(len(list(Path(tmp).rglob('*.csv'))),2)

    def test_failed_pair_commit_restores_both_formats(self):
        profile=discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')[0]
        normal,raw=self.frames(profile)
        with tempfile.TemporaryDirectory() as tmp:
            write_paired_yearly_transaction_csvs([normal],[raw],output_root=tmp,institution_slug='bank')
            before={p:p.read_bytes() for p in Path(tmp).rglob('*.csv')}
            original_replace=Path.replace
            def fail_one(source,target):
                if source.suffix=='.tmp' and target.name=='bank_2023_transactions.csv':
                    raise OSError('simulated write failure')
                return original_replace(source,target)
            with patch.object(Path,'replace',fail_one), self.assertRaises(OSError):
                write_paired_yearly_transaction_csvs([normal],[raw],output_root=tmp,institution_slug='bank')
            self.assertEqual(before,{p:p.read_bytes() for p in Path(tmp).rglob('*.csv')})

    def test_workflow_loader_excludes_yearly(self):
        from src.bill_extractor.workflow import _load_normalized_statement_frames
        profile=discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')[0]
        normal,raw=self.frames(profile)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/'monthly';folder.mkdir()
            normal.to_csv(folder/'Jan_normalized_transactions.csv',index=False)
            raw.to_csv(folder/'Jan_transactions.csv',index=False)
            write_paired_yearly_transaction_csvs([normal],[raw],output_root=root,institution_slug='bank')
            frames=_load_normalized_statement_frames(root,(profile,))
            self.assertEqual(len(frames),1)
            self.assertEqual(Path(frames[0].attrs['raw_csv_path']),folder/'Jan_transactions.csv')

if __name__=='__main__': unittest.main()
