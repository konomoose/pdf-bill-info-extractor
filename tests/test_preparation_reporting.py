import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from unittest.mock import patch
import csv
import fitz
from src.bill_extractor.pdf_preparation import prepare_account_pdfs
from src.bill_extractor.pdf_security import PDFPasswordRequiredError

class PreparationReporting(unittest.TestCase):
    def pdf(self,path,text=None):
        path.parent.mkdir(parents=True,exist_ok=True)
        with fitz.open() as doc:
            page=doc.new_page()
            if text:page.insert_text((72,72),text)
            doc.save(path)

    def test_real_failures_verified_skips_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source_input/simplii/2022'
            self.pdf(source/'good.pdf','Sample transaction text')
            self.pdf(source/'image-only.pdf')
            self.pdf(source/'different.pdf','Original sample')
            self.pdf(root/'editable_input/simplii/2022/different.pdf','Changed sample')
            for expected in [(1,0),(0,1)]:
                result=prepare_account_pdfs('simplii',['PrivateName'],project_root=root)
                self.assertEqual((result.security_created_count,result.security_skipped_count),expected)
                self.assertEqual(result.security_failed_count,2)
                self.assertEqual(len(result.security_failure_messages),2)
                self.assertTrue(any('Reason: Page 1' in m for m in result.security_failure_messages))
                self.assertTrue(any('image-only.pdf' in m and 'image-only' in m for m in result.security_failure_messages))
                self.assertTrue(any('different.pdf' in m and 'could not be verified' in m for m in result.security_failure_messages))
                with result.report_path.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
                self.assertEqual(len(rows),3)
                self.assertEqual(sum(r['redaction_status']=='not_attempted' for r in rows),2)
                self.assertNotIn('Sample transaction text',result.report_path.read_text())
            self.assertEqual(len(list((root/'preparation_reports/simplii').glob('*.csv'))),2)

    def test_password_and_unwritable_report_remain_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.pdf(root/'source_input/simplii/a.pdf','Test')
            (root/'preparation_reports').write_text('block directory creation')
            with patch('src.bill_extractor.pdf_preparation.remove_pdf_security',side_effect=PDFPasswordRequiredError('The supplied PDF password was not accepted.')):
                result=prepare_account_pdfs('simplii',['PrivateName'],password='secret123',project_root=root)
            self.assertEqual(result.security_password_required_count,1)
            self.assertIn('a.pdf',result.security_failure_messages[0])
            self.assertIsNone(result.report_path)
            self.assertTrue(result.report_error)
            self.assertNotIn('secret123',str(result))

if __name__=='__main__':unittest.main()
