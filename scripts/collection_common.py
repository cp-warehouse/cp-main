"""Local DB transport and atomic JSON checkpoints; no schema changes."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import random
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def literal(value):
    return "convert_from(decode('%s','hex'),'UTF8')" % str(value).encode().hex()


def js(value):
    return literal(json.dumps(value, ensure_ascii=False)) + '::jsonb'


def execute(sql):
    result = subprocess.run(['docker','compose','-f',str(ROOT/'infra/postgres/compose.yaml'),
        'exec','-T','db','psql','-X','-qAt','-v','ON_ERROR_STOP=1',
        '-U','company_dev','-d',os.environ.get('COLLECTOR_DB_NAME','company_analysis')],
        input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('DB 오류: 기존 PostgreSQL과 company 메타데이터/news_document 테이블을 확인하세요')
    return result.stdout.strip()


def rows(sql):
    return json.loads(execute("SELECT COALESCE(json_agg(t),'[]'::json) FROM (" + sql + ') t;'))


def bounded(maximum):
    def parse(value):
        import argparse
        try:
            value = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError('정수가 필요합니다') from None
        if not 1 <= value <= maximum:
            raise argparse.ArgumentTypeError(f'1~{maximum} 범위로 지정하세요')
        return value
    return parse


@contextmanager
def lock(kind):
    folder = ROOT/'data/processed/collectors'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/(kind+'.lock')).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'{kind} 수집기가 이미 실행 중입니다') from None
        yield


class Stop(RuntimeError):
    pass


class Pace:
    def __init__(self, interval, every=50, low=5, high=10, budget=None):
        self.interval, self.every, self.low, self.high = interval, every, low, high
        self.budget, self.count, self.last = budget, 0, None

    def before(self):
        if self.budget is not None and self.count >= self.budget:
            raise Stop('요청 예산 도달')
        if self.last is not None:
            delay = max(0, self.interval-(time.monotonic()-self.last))
            if self.count % self.every == 0:
                delay += random.uniform(self.low, self.high)
            time.sleep(delay)
        self.count += 1
        self.last = time.monotonic()


def pacing(parser, minimum):
    parser.add_argument('--interval', type=float, default=minimum)
    parser.add_argument('--pause-every', type=bounded(1000), default=50)
    parser.add_argument('--pause-min', type=float, default=5)
    parser.add_argument('--pause-max', type=float, default=10)


def validate_pacing(parser, args, minimum):
    if not minimum <= args.interval <= 60 or not 0 <= args.pause_min <= args.pause_max <= 60:
        parser.error(f'interval {minimum}~60, pause 0~60 및 min <= max 필요')


STATE = ROOT/'data/processed/collectors/batches'
_batches = {}


def save_batch(batch):
    STATE.mkdir(parents=True,exist_ok=True)
    path = STATE/(batch['batch_id']+'.json')
    temporary = path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(batch,handle,ensure_ascii=False,indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def create_batch(kind, settings, items):
    batch_id = str(uuid.uuid4())
    batch = dict(batch_id=batch_id,kind=kind,status='running',settings=settings,
                 database=os.environ.get('COLLECTOR_DB_NAME','company_analysis'),
                 created_at=time.time(),items=[dict(item,status='pending',attempts=0,error=None) for item in items])
    _batches[batch_id]=batch
    save_batch(batch)
    return batch_id


def resume_batch(batch_id, kind):
    batch_id = str(uuid.UUID(batch_id))
    batch=json.loads((STATE/(batch_id+'.json')).read_text())
    if batch['kind']!=kind or batch['database']!=os.environ.get('COLLECTOR_DB_NAME','company_analysis'):
        raise ValueError('수집기 종류 또는 DB가 다른 체크포인트')
    batch['status']='running'
    _batches[batch_id]=batch
    save_batch(batch)
    return batch


def reserved_keys(kind):
    result=set()
    for path in STATE.glob('*.json'):
        batch=json.loads(path.read_text())
        if batch['kind']==kind and batch['database']==os.environ.get('COLLECTOR_DB_NAME','company_analysis'):
            result.update(item['key'] for item in batch['items'] if item['status']!='done')
    return result


def pending(batch_id):
    return [dict(item,item_key=item['key']) for item in _batches[batch_id]['items'] if item['status']!='done']


def mark(batch_id, key, status, details, error=None):
    batch=_batches[batch_id]
    item=next(item for item in batch['items'] if item['key']==key)
    item.update(status=status,details=details,error=error,updated_at=time.time())
    if status=='running':
        item['attempts']+=1
    save_batch(batch)


def finish(batch_id, stopped=False):
    batch=_batches[batch_id]
    counts={}
    for item in batch['items']:
        counts[item['status']]=counts.get(item['status'],0)+1
    status='stopped' if stopped else ('success' if counts.get('done',0)==len(batch['items']) else 'partial')
    batch.update(status=status,finished_at=time.time())
    save_batch(batch)
    print(json.dumps(dict(batch_id=batch_id,status=status,items=counts,checkpoint=str(STATE/(batch_id+'.json'))),ensure_ascii=False),flush=True)
    return 0 if status=='success' else 1


def last_attempts(kind):
    result={}
    for path in STATE.glob('*.json'):
        batch=json.loads(path.read_text())
        if batch['kind']==kind and batch['database']==os.environ.get('COLLECTOR_DB_NAME','company_analysis'):
            for item in batch['items']:
                if item.get('attempts',0):
                    result[item['key']]=max(result.get(item['key'],0),item.get('updated_at',batch['created_at']))
    return result
