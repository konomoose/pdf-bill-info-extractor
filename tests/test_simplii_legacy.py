import sys
import tempfile
import unittest
from pathlib import Path

import fitz
import pandas as pd
from datetime import date
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.bill_extractor.pdf_processor import VisaPDFProcessor, PDFProcessingError
from src.bill_extractor.transaction_normalizer import normalize_transactions, NormalizationError


class SimpliiLayouts(unittest.TestCase):
    def setUp(self):
        self.processor = VisaPDFProcessor(profile_path='config/profiles/simplii_bank_account.json')

    def statement(self, modern=False, total='10.00', missing_effective=False):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((20, 40), 'statement period: December 31, 2020 - January 29, 2021', fontsize=9)
        def row(y, entries):
            for x, value in entries:
                page.insert_text((x, y), value, fontsize=9)
        if modern:
            row(100, [(17,'Trans.'),(65,'Eff.'),(115,'Transaction'),(361,'Funds out'),(457,'Funds in'),(545,'Balance')])
            row(111, [(17,'date'),(65,'date')])
            desc = 115
        else:
            row(100, [(17,'date'),(82,'transaction'),(361,'funds out'),(457,'funds in'),(545,'balance')])
            desc = 82
        dates = [(17,'Jan 04')]
        if modern and not missing_effective:
            dates.append((65,'Jan 04'))
        row(130, dates + [(desc,'BALANCE FORWARD'),(545,'100.00')])
        row(150, dates + [(desc,'TEST PAYMENT'),(375,'10.00'),(545,'90.00')])
        row(162, [(desc,'CONTINUATION')])
        row(180, dates + [(desc,'TEST DEPOSIT'),(470,'20.00'),(545,'110.00')])
        row(210, [(20,'end of transactions')])
        row(230, [(20,f'total funds out {total}')])
        row(245, [(20,'total funds in 20.00')])
        return doc

    def test_old_and_new_layouts_keep_schema_and_continuations(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as tmp:
                with self.statement(modern) as doc:
                    path=Path(tmp)/'unrelated-name.pdf'
                    doc.save(path)
                result=self.processor.extract_transactions(path)
                rows=result.transactions
                self.assertEqual(len(rows),2)
                self.assertEqual(list(rows.columns),self.processor.required_headers)
                self.assertEqual(rows.iloc[0]['Transaction'],'TEST PAYMENT CONTINUATION')
                self.assertEqual(rows['Eff. date'].tolist(),['Jan 04']*2 if modern else ['']*2)
                self.assertEqual(result.metadata.statement_end_date.year,2021)

    def test_incomplete_legacy_table_still_fails_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.statement(total='11.00') as doc:
                path=Path(tmp)/'sample.pdf'; doc.save(path)
            with self.assertRaises(PDFProcessingError):
                self.processor.extract_transactions(path)

    def test_modern_table_still_requires_effective_dates(self):
        with self.statement(modern=True,missing_effective=True) as doc:
            self.assertEqual(self.processor._extract_simplii_page_transactions(doc[0]),[])

    def test_single_date_month_end_grace_is_bounded(self):
        metadata = replace(self.processor._build_statement_metadata(Path('sample.pdf')),
                           statement_start_date=date(2020,12,31),
                           statement_end_date=date(2021,1,29))
        rows=pd.DataFrame([['Jan 31','','TEST','1.00','','99.00']],
                          columns=self.processor.required_headers)
        result=normalize_transactions(rows,metadata)
        self.assertEqual(result.iloc[0]['transaction_date'],'2021-01-31')
        self.assertEqual(result.iloc[0]['effective_date'],'')
        for invalid in ('Jan 30','Feb 01','Jan 32'):
            rows.loc[0,'Trans. date']=invalid
            with self.subTest(date=invalid), self.assertRaises(NormalizationError):
                normalize_transactions(rows,metadata)
        rows.loc[0,'Trans. date']='Jan 31'
        with self.assertRaises(NormalizationError):
            normalize_transactions(rows,replace(metadata,statement_end_date=date(2021,1,27)))


if __name__ == '__main__':
    unittest.main()
