import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
import pandas as pd
from src.bill_extractor.profile_loader import discover_profiles
from src.bill_extractor.transaction_normalizer import NORMALIZED_COLUMNS, PROFILE_MAPPINGS
from src.bill_extractor.analysis_summary import build_analysis, standardize, ANALYSIS_COLUMNS, AnalysisError

PROFILES=discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')

class AnalysisTests(unittest.TestCase):
    def fixture(self, original, root):
        profile=replace(original,output_folder=root/'csv'/original.profile_id,
                        input_folder=root/'editable'/original.profile_id)
        source=profile.resolve_output_folder()
        slug=source.name
        normal=pd.DataFrame('',index=range(3),columns=NORMALIZED_COLUMNS)
        normal['transaction_date']=['2022-12-31','2023-01-01','2023-01-02']
        normal['description']=['Purchase','Payment','Purchase']
        normal['profile_id']=profile.profile_id
        normal['source_file']=['dec.pdf','jan.pdf','jan.pdf']
        mapping=PROFILE_MAPPINGS[profile.profile_id]
        if 'amount' in mapping:
            normal['amount']=['20.15','-100.01','20.15']
        else:
            normal['withdrawal']=['20.15','','20.15']
            if 'payment' in mapping:
                normal['payment']=['','100.01','']
            else:
                normal['deposit']=['','100.01','']
        columns=profile.normalized_output_columns or NORMALIZED_COLUMNS
        for year,indices in [(2022,[0]),(2023,[1,2])]:
            folder=source/str(year);folder.mkdir(parents=True)
            normal.iloc[indices].loc[:,list(columns)].to_csv(folder/f'{slug}_{year}_normalized_transactions.csv',index=False)
            # Annual original-column data must never be read as normalized data.
            (folder/f'{slug}_{year}_transactions.csv').write_text('original,columns\nunchanged,yes\n')
        month=source/'2023'/'January';month.mkdir()
        normal.iloc[1:].loc[:,list(columns)].to_csv(month/'January_normalized_transactions.csv',index=False)
        return profile,normal

    def test_all_profiles_headers_totals_repeats_and_source_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); profiles=[]
            for profile in PROFILES:
                profiles.append(self.fixture(profile,root)[0])
            before={p:p.read_bytes() for p in (root/'csv').rglob('*.csv')}
            for profile in profiles:
                with self.subTest(profile=profile.profile_id):
                    result=build_analysis([profile],output_root=root/'analysis')
                    self.assertEqual((result.money_in,result.money_out,result.net_amount),('100.01','40.30','59.71'))
                    self.assertEqual(result.transaction_count,3)
                    saved=result.csv_path.read_bytes()
                    self.assertEqual(build_analysis([profile,profile],output_root=root/'analysis').csv_path.read_bytes(),saved)
                    for path in (root/'analysis'/profile.profile_id).rglob('*.csv'):
                        self.assertEqual(tuple(pd.read_csv(path).columns),ANALYSIS_COLUMNS)
            overall=build_analysis(profiles+[profiles[0]],overall=True,output_root=root/'analysis')
            self.assertEqual(overall.transaction_count,39)
            self.assertEqual(overall.profile_count,13)
            self.assertEqual(overall.money_in,'1300.13')
            self.assertEqual(overall.money_out,'523.90')
            final=overall.csv_path.read_bytes()
            self.assertEqual(build_analysis(profiles,overall=True,output_root=root/'analysis').csv_path.read_bytes(),final)
            self.assertEqual(before,{p:p.read_bytes() for p in (root/'csv').rglob('*.csv')})

    def test_missing_profile_or_invalid_year_does_not_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a,_=self.fixture(PROFILES[0],root)
            b,_=self.fixture(PROFILES[1],root)
            result=build_analysis([a],overall=True,output_root=root/'analysis')
            before={p:p.read_bytes() for p in (root/'analysis').rglob('*.csv')}
            for path in b.resolve_output_folder().glob('????/*_normalized_transactions.csv'):path.unlink()
            with self.assertRaises(AnalysisError):build_analysis([a,b],overall=True,output_root=root/'analysis')
            self.assertEqual(before,{p:p.read_bytes() for p in (root/'analysis').rglob('*.csv')})
            p=next(a.resolve_output_folder().glob('2022/*_normalized_transactions.csv'))
            p.write_text(p.read_text().replace('2022-12-31','2023-12-31'))
            with self.assertRaises(AnalysisError):build_analysis([a],output_root=root/'analysis')
            self.assertEqual(before,{p:p.read_bytes() for p in (root/'analysis').rglob('*.csv')})

    def test_loc_fees_reversals_blank_balance_and_decimal_validation(self):
        profile=next(p for p in PROFILES if p.profile_id=='rbc_loc_v1')
        normal=pd.DataFrame('',index=range(2),columns=NORMALIZED_COLUMNS)
        normal['transaction_date']=['2023-01-01']*2
        normal['description']=['Loan','Reversal']
        normal['withdrawal']=['100.00','-20.00']
        normal['interest_fees_insurance']=['5.25','']
        normal['payment']=['50.00','']
        out=standardize(normal,profile,'test.csv')
        self.assertEqual(out['money_out'].tolist(),['105.25','0.00'])
        self.assertEqual(out['money_in'].tolist(),['50.00','20.00'])
        self.assertEqual(out['balance'].tolist(),['',''])
        normal.loc[0,'withdrawal']='NaN'
        with self.assertRaises(AnalysisError):standardize(normal,profile,'test.csv')

    def test_real_equal_transactions_preserved_and_profiles_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a,normal=self.fixture(PROFILES[0],root);b,_=self.fixture(PROFILES[1],root)
            path=next(a.resolve_output_folder().glob('2023/*_normalized_transactions.csv'))
            frame=pd.read_csv(path,dtype=str,keep_default_na=False)
            pd.concat([frame,frame.iloc[[1]]]).to_csv(path,index=False)
            result=build_analysis([a,b],overall=True,output_root=root/'analysis')
            self.assertEqual(result.transaction_count,7) # Don't silently drop a real second purchase.
            result=build_analysis([b],overall=True,output_root=root/'analysis')
            self.assertEqual(result.transaction_count,3)
            selected=pd.read_csv(root/'analysis'/'overall_profiles.csv')
            self.assertEqual(selected.profile_id.tolist(),[b.profile_id])

    def test_stale_analysis_cleanup_preserves_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile,_=self.fixture(PROFILES[0],root)
            build_analysis([profile],output_root=root/'analysis')
            dest=root/'analysis'/profile.profile_id
            extra=dest/'yearly'/'my_notes.csv';extra.write_text('leave alone')
            for path in profile.resolve_output_folder().glob('2022/*_normalized_transactions.csv'):
                path.unlink()
            result=build_analysis([profile],output_root=root/'analysis')
            self.assertEqual(result.transaction_count,2)
            self.assertFalse(list((dest/'yearly').glob('*_2022_analysis.csv')))
            self.assertEqual(extra.read_text(),'leave alone')

    def test_commit_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a,_=self.fixture(PROFILES[0],root)
            build_analysis([a],overall=True,output_root=root/'analysis')
            before={p:p.read_bytes() for p in (root/'analysis').rglob('*.csv')}
            original=Path.replace
            def fail(source,target):
                if source.suffix=='.tmp' and target.name=='overall_summary.csv':raise OSError('simulated')
                return original(source,target)
            with patch.object(Path,'replace',fail),self.assertRaises(OSError):
                build_analysis([a],overall=True,output_root=root/'analysis')
            self.assertEqual(before,{p:p.read_bytes() for p in (root/'analysis').rglob('*.csv')})

if __name__=='__main__':unittest.main()
