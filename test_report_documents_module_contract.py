"""Compare actual DOCX OOXML/media against the unmodified previous renderer."""
import ast
import builtins
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import subprocess
import symtable
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from flask import g
from PIL import Image
import test_report_write_module_contract as fixtures

ROOT = Path(__file__).resolve().parent
BASE = '3604351'


class ReportDocumentsModuleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = subprocess.check_output(['git','show',BASE+':app.py'],cwd=ROOT,text=True,encoding='utf-8')
        cls.original = {node.name:node for node in ast.parse(cls.source).body if isinstance(node,ast.FunctionDef)}
        cls.tables = {table.get_name():table for table in symtable.symtable(cls.source,'app.py','exec').get_children()}

    def setUp(self):
        self.fixture = fixtures.ReportWriteModuleContractTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module,self.http,self.report_id = self.fixture.module,self.fixture.http,self.fixture.report_id
        self.temp = Path(self.fixture.fixture.f.temp.name)
        with self.context() as connection:
            self.report = dict(connection.execute('select * from service_reports where id=?',(self.report_id,)).fetchone())
            self.order = dict(self.module.require_service_order(self.fixture.fixture.f.order))

    @contextmanager
    def context(self):
        with self.module.app.test_request_context():
            connection = self.module.db()
            g.user = connection.execute('select * from users where id=?',(self.fixture.fixture.f.people['Manager'],)).fetchone()
            yield connection

    def reference(self,name):
        def dependencies(table):
            result = {symbol.get_name() for symbol in table.get_symbols() if symbol.is_global() and symbol.is_referenced()}
            for child in table.get_children():
                result |= dependencies(child)
            return result-set(vars(builtins))
        namespace = {key:getattr(self.module,key) for key in dependencies(self.tables[name]) if key != name}
        selected = ast.Module(body=[self.original[name]],type_ignores=[])
        exec(compile(selected,'baseline-report-documents.py','exec'),namespace)
        return namespace[name]

    def parts(self,payload):
        with ZipFile(BytesIO(payload)) as archive:
            return {name:archive.read(name) for name in archive.namelist()}

    def compare(self,report=None,order=None):
        report,order = report or self.report,order or self.order
        with self.context():
            expected = self.parts(self.reference('build_service_report_docx')(report,order))
            actual = self.parts(self.module.build_service_report_docx(report,order))
        self.assertEqual(actual.keys(),expected.keys())
        for name in actual:
            self.assertEqual(actual[name],expected[name],name)
        return actual

    def attachment(self,name,content,category='site'):
        with self.context() as connection:
            aid = connection.execute('insert into service_report_attachments (report_id,category,original_filename,stored_filename,content_type,uploaded_by,uploaded_at) values (?,?,?,?,?,?,?)',(self.report_id,category,name,name,'image/png',self.fixture.fixture.f.people['Manager'],self.module.now())).lastrowid
            row = connection.execute('select * from service_report_attachments where id=?',(aid,)).fetchone()
            path = Path(self.module.report_attachment_path(row))
            path.parent.mkdir(parents=True,exist_ok=True)
            if content is not None:
                path.write_bytes(content)
            connection.commit()
        return aid

    def test_docx_all_parts_equal_with_workers_and_both_part_tables(self):
        parts = self.compare()
        xml = parts['word/document.xml'].decode()
        for text in ('现场服务日报','Original service','Original worker task','PART-A','OLD','NEW'):
            self.assertIn(text,xml)

    def test_photo_media_equal_for_alpha_large_corrupt_and_missing_images(self):
        stream = BytesIO()
        Image.new('RGBA',(1500,1000),(20,40,60,120)).save(stream,format='PNG')
        self.attachment('large.png',stream.getvalue(),'arrival')
        self.attachment('broken.png',b'not an image','site')
        self.attachment('missing.png',None,'site')
        parts = self.compare()
        media = [value for name,value in parts.items() if name.startswith('word/media/')]
        self.assertEqual(len(media),1)
        with Image.open(BytesIO(media[0])) as image:
            self.assertEqual(image.format,'JPEG')
            self.assertEqual(image.mode,'RGB')
            self.assertLessEqual(max(image.size),960)
        self.assertIn('图片无法嵌入',parts['word/document.xml'].decode())

    def test_invalid_xml_control_characters_are_removed_identically(self):
        report = dict(self.report,service_description='Before\x00After\x01End')
        order = dict(self.order,client_name='Site\x02Name')
        parts = self.compare(report,order)
        ElementTree.fromstring(parts['word/document.xml'])
        self.assertIn('BeforeAfterEnd',parts['word/document.xml'].decode())

    def test_letter_page_margins_and_minimum_table_rows_are_preserved(self):
        with patch.object(self.module,'service_report_workers',return_value=[]):
            with patch.object(self.module,'report_parts',return_value=[]):
                parts = self.compare()
        namespace = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        root = ElementTree.fromstring(parts['word/document.xml'])
        width = root.find('.//w:pgSz',namespace)
        self.assertEqual(width.attrib['{'+namespace['w']+'}w'],'12240')
        self.assertEqual(width.attrib['{'+namespace['w']+'}h'],'15840')
        margins = root.find('.//w:pgMar',namespace)
        self.assertEqual(margins.attrib['{'+namespace['w']+'}top'],'792')
        tables = root.findall('.//w:body/w:tbl',namespace)
        self.assertEqual(len(tables[0].findall('w:tr',namespace)),3)
        self.assertEqual(len(tables[4].findall('w:tr',namespace)),6)
        self.assertEqual(len(tables[5].findall('w:tr',namespace)),6)

    def test_filename_collision_matches_original(self):
        for number in ('CLIENT-ORDER','客户 / order'):
            order = dict(self.order,client_order_number=number)
            first = self.reference('service_report_docx_filename')(order,self.report)
            actual = self.module.service_report_docx_filename(order,self.report,{first})
            expected = self.reference('service_report_docx_filename')(order,self.report,{first})
            self.assertEqual(actual,expected)
            self.assertNotEqual(actual,first)
            self.assertTrue(actual.endswith(f'-{self.report_id}.docx'))

    def test_attachment_packaging_resolves_builder_and_unique_names(self):
        with self.context() as connection:
            connection.execute('insert into service_reports (service_order_id,report_date,actual_work_date,created_by,created_at,updated_at) values (?,?,?,?,?,?)',(self.order['id'],self.report['report_date'],self.report['report_date'],self.fixture.fixture.f.people['Manager'],self.module.now(),self.module.now()))
            connection.commit()
        with patch.object(self.module,'build_service_report_docx',side_effect=lambda report,order:str(report['id']).encode()) as builder:
            with self.context():
                actual = self.module.service_order_report_word_attachments(self.order)
                expected = self.reference('service_order_report_word_attachments')(self.order)
        self.assertEqual(actual,expected)
        self.assertEqual(len(actual),2)
        self.assertEqual(len({row['filename'] for row in actual}),2)
        self.assertEqual(builder.call_count,4)

    def test_attachment_packaging_failure_keeps_contextual_error(self):
        with patch.object(self.module,'build_service_report_docx',side_effect=ValueError('simulated')):
            with self.context():
                with self.assertRaisesRegex(ValueError,'Word 文件生成失败') as actual:
                    self.module.service_order_report_word_attachments(self.order)
                with self.assertRaises(ValueError) as expected:
                    self.reference('service_order_report_word_attachments')(self.order)
        self.assertEqual(str(actual.exception),str(expected.exception))

    def test_real_http_export_matches_original_docx(self):
        response = self.http.post(f'/service-reports/{self.report_id}/export')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        with self.context():
            expected = self.parts(self.reference('build_service_report_docx')(self.report,self.order))
        self.assertEqual(self.parts(response.data),expected)
        response.close()
