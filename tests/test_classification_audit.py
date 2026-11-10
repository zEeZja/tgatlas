import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from src.classification_audit import run

class AuditTests(unittest.TestCase):
    def test_report_uses_new_rules_without_modifying_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'channels.json';report=Path(tmp)/'review.csv'
            rows={'channels':[{'name':'Мурманск сейчас','username':'murmansknow','subcategory':'other'},{'name':'Белая Каска','username':'test','subcategory':'other'}]}
            source.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
            original=source.read_bytes()
            with redirect_stdout(io.StringIO()):counts=run(source,report)
            self.assertEqual(counts['topic_unknown'],1)
            self.assertEqual(counts['geography_unknown'],1)
            self.assertEqual(source.read_bytes(),original)
            with report.open(encoding='utf-8-sig') as f:items=list(csv.DictReader(f,delimiter=';'))
            self.assertEqual(items[0]['new_topic'],'Новости')
            self.assertEqual(items[0]['new_regions'],'Мурманская область')
            with self.assertRaises(ValueError):run(source,source)
