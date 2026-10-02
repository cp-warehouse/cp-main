"""Extract three consolidated financial facts from an OpenDART filing document."""
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ingest_financial

ROOT = Path(__file__).resolve().parents[1]
UNIT_SCALE = {'원': Decimal(1), '천원': Decimal(1000), '백만원': Decimal(1000000)}


class FilingTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.history = []
        self.context = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'table' and self.table is None:
            self.table, self.context = [], list(self.history[-12:])
        elif self.table is not None and tag == 'tr':
            self.row = []
        elif self.row is not None and tag in ('td', 'th'):
            self.cell = []

    def handle_data(self, data):
        text = ' '.join(data.split())
        if not text:
            return
        self.history.append(text)
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.cell is not None and tag in ('td', 'th'):
            self.row.append(' '.join(self.cell))
            self.cell = None
        elif self.row is not None and tag == 'tr':
            if any(self.row):
                self.table.append(self.row)
            self.row = None
        elif self.table is not None and tag == 'table':
            self.tables.append((self.context, self.table))
            self.table = None


def compact(value):
    return re.sub(r'\s+', '', value or '')


def label_key(value):
    value = compact(value)
    value = re.sub(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX0-9.]+', '', value)
    value = re.sub(r'\(주석.*$', '', value)
    return value


def amount(value):
    value = compact(value)
    negative = value.startswith('(') and value.endswith(')')
    value = value.strip('()').replace(',', '')
    if not re.fullmatch(r'\d+', value):
        raise ValueError('공시 금액 형식 오류')
    return -Decimal(value) if negative else Decimal(value)


def current_amount(row):
    values = []
    for cell in row[1:]:
        try:
            values.append(amount(cell))
        except ValueError:
            pass
    if len(values) < 2:
        raise ValueError('당기·전기 금액을 찾지 못함')
    return values[-2]


def date_forms(value):
    parsed = date.fromisoformat(value)
    return {
        f'{parsed.year}년{parsed.month}월{parsed.day}일',
        f'{parsed.year}년{parsed.month:02d}월{parsed.day:02d}일',
    }


def find_statement(tables, markers, required_labels, config):
    matches = []
    for context, rows in tables:
        context_text = compact(' '.join(context))
        labels = {label_key(row[0]) for row in rows if row}
        if (any(compact(marker) in context_text for marker in markers)
                and all(any(label in labels for label in alternatives)
                        for alternatives in required_labels)):
            matches.append((context_text, rows))
    if len(matches) != 1:
        raise ValueError('대상 연결재무제표 표가 정확히 1개가 아님')
    context, rows = matches[0]
    if compact(f"(단위:{config['unit']})") not in context:
        raise ValueError('공시 단위 검증 실패')
    if not any(form in context for form in date_forms(config['period_end'])):
        raise ValueError('공시 종료일 검증 실패')
    return rows


def target_row(rows, alternatives):
    matches = [row for row in rows if row and label_key(row[0]) in alternatives]
    if len(matches) != 1:
        raise ValueError('공시 계정 행이 정확히 1개가 아님')
    return matches[0]


def transform(path, corp_code, config):
    raw = path.read_bytes()
    meta = json.loads(path.with_name('metadata.json').read_text())
    receipt = config['receipt_no']
    if (meta.get('endpoint') != 'document.xml'
            or meta.get('params', {}).get('rcept_no') != receipt
            or hashlib.sha256(raw).hexdigest() != meta.get('sha256')):
        raise ValueError('공시 원문·요청 정보 불일치')
    stamp = datetime.fromisoformat(meta['collected_at'])
    if stamp.tzinfo is None:
        raise ValueError('수집 시각의 시간대 누락')
    if not zipfile.is_zipfile(path):
        raise ValueError('공시 원문 응답이 ZIP이 아님')
    with zipfile.ZipFile(path) as archive:
        if config['member'] not in archive.namelist():
            raise ValueError('설정한 공시 XML 파일 누락')
        document = archive.read(config['member']).decode('utf-8', 'strict')
    parser = FilingTableParser()
    parser.feed(document)

    assets_names = {'자산총계'}
    revenue_names = set(config['revenue_names'])
    operating_names = {'영업이익', '영업손익'}
    assets_table = find_statement(
        parser.tables, {'연결재무상태표'}, [assets_names], config)
    income_table = find_statement(
        parser.tables, {'연결손익계산서', '연결포괄손익계산서'},
        [revenue_names, operating_names], config)
    income_context = next(compact(' '.join(context)) for context, rows in parser.tables
                          if rows is income_table)
    if not any(form in income_context for form in date_forms(config['period_start'])):
        raise ValueError('공시 시작일 검증 실패')

    scale = UNIT_SCALE[config['unit']]
    selections = [
        ('자산총계', target_row(assets_table, assets_names), 'instant'),
        ('영업수익', target_row(income_table, revenue_names), 'annual'),
        ('영업이익', target_row(income_table, operating_names), 'annual'),
    ]
    records = []
    for canonical_name, row, basis in selections:
        start = config['period_end'] if basis == 'instant' else config['period_start']
        records.append({
            'corp_code': corp_code,
            'account_code': None,
            'account_name': canonical_name,
            'source_account_name': row[0].strip(),
            'value': str(current_amount(row) * scale),
            'currency': 'KRW',
            'fiscal_year': config['fiscal_year'],
            'report_code': config['report_code'],
            'statement_scope': 'consolidated',
            'period_start': start,
            'period_end': config['period_end'],
            'period_basis': basis,
            'source_url': f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}',
            'source_key': f'opendart:document:{receipt}:CFS',
            'source_revision': f"{receipt}:{meta['sha256']}",
            'source_record_key': f'document:{canonical_name}',
            'extractor_version': 'document-financial-v1',
            'raw_uri': str(path.resolve().relative_to(ROOT)),
            'collected_at': meta['collected_at'],
        })
    return records


def load(records):
    ingest_financial.load(records)
