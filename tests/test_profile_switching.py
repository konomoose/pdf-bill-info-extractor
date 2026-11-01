import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock, patch
from dataclasses import replace
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('extractor_ui',Path(__file__).resolve().parents[1]/'visa_pdf_extractor-v3.py')
ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)
from src.bill_extractor.profile_loader import discover_profiles

class Var:
    def __init__(self,v=''):self.v=v
    def set(self,v):self.v=v
    def get(self):return self.v

class ProfileSwitchTests(unittest.TestCase):
    def test_main_profile_replaces_browsed_paths(self):
        profiles=discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')
        app=SimpleNamespace(clear_results=Mock(),_update_preparation_paths=Mock(),_load_redaction_terms_for_preparation_account=Mock())
        for name in ('pdf_file_var','pdf_folder_var','output_folder_var','preparation_account_var','profile_info_var','folder_mode_info_var','status_var'):
            setattr(app,name,Var('previous manual path'))
        with tempfile.TemporaryDirectory() as tmp:
            for p in profiles:
                p=replace(p,input_folder=Path(tmp)/p.profile_id/'input',output_folder=Path(tmp)/p.profile_id/'output')
                ui.PDFBillExtractorApp._apply_profile(app,p,clear_results=True)
                self.assertEqual(app.pdf_folder_var.get(),str(p.resolve_input_folder()))
                self.assertEqual(app.output_folder_var.get(),str(p.resolve_output_folder()))
                self.assertEqual(app.pdf_file_var.get(),'')

    def test_preparation_selection_switches_matching_profile(self):
        profiles=discover_profiles(Path(__file__).resolve().parents[1]/'config/profiles')
        target=next(p for p in profiles if p.profile_id=='simplii_chequing_account_v1')
        app=SimpleNamespace(profiles=profiles,active_profile=profiles[0],preparation_account_var=Var('simplii'),profile_var=Var(),_apply_profile=Mock())
        ui.PDFBillExtractorApp._preparation_account_changed(app)
        self.assertEqual(app.profile_var.get(),target.display_name)
        app._apply_profile.assert_called_once_with(target,clear_results=True)

if __name__=='__main__':unittest.main()
