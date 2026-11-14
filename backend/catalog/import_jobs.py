"""Durable private file queue; SQL transaction receipts resolve crash ambiguity."""
import json,os,secrets,uuid,threading,time
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .admin_importer import canonical,digest,ImportRejected

MAX_JOBS=2000
TERMINAL={'SUCCEEDED','FAILED','NEEDS_REVIEW','CANCELLED','EXPIRED'}
def stamp():return datetime.now(timezone.utc).isoformat()
class ImportJobs:
    def __init__(self,folder,importer,authorized):
        self.folder=Path(folder);self.importer=importer;self.authorized=authorized
        self.folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.folder.is_symlink():raise RuntimeError('Unsafe import queue')
        self._mutex=threading.RLock();self._work_mutex=threading.Lock();self._started=False
    def path(self,key):
        if type(key) is not str or str(uuid.UUID(key))!=key:raise ValueError('Job ID')
        return self.folder/(key+'.json')
    def write(self,path,doc):
        if path.is_symlink():raise RuntimeError('Unsafe queue entry')
        tmp=self.folder/(secrets.token_hex(16)+'.tmp')
        try:
            with tmp.open('x',encoding='utf-8') as f:
                os.chmod(tmp,0o600);json.dump(doc,f,ensure_ascii=False,default=str,allow_nan=False);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
            if os.name!='nt':
                fd=os.open(self.folder,os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
        finally:tmp.unlink(missing_ok=True)
    @contextmanager
    def locked(self):
        with self._mutex:
            p=self.folder/'queue.lock'
            if p.is_symlink():raise RuntimeError('Unsafe lock')
            with p.open('a+b') as f:
                os.chmod(p,0o600)
                if os.name!='nt':
                    import fcntl
                    fcntl.flock(f,fcntl.LOCK_EX)
                try:yield
                finally:
                    if os.name!='nt':fcntl.flock(f,fcntl.LOCK_UN)
    def read(self,key):
        path=self.path(key)
        if path.is_symlink():raise ValueError('Unsafe job')
        return json.loads(path.read_text(encoding='utf-8'))
    @staticmethod
    def public(doc):return {k:v for k,v in doc.items() if k not in ('payloads','owner')}
    def preview(self,owner,payloads,plans):
        with self.locked():
            if len(list(self.folder.glob('*.json')))>=MAX_JOBS:raise ImportRejected('QUEUE_CAPACITY_REACHED')
            key=str(uuid.uuid4());now=stamp()
            seal=digest(dict(owner=owner,payloads=payloads,plans=plans))
            doc=dict(job_id=key,owner=owner,kind='tmdb_catalog',status='PREVIEW',created_at=now,updated_at=now,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),digest=seal,plans=plans)
            self.write(self.folder/(key+'.payload'),dict(payloads=payloads))
            self.write(self.path(key),doc)
            return self.public(doc)
    def get(self,key,owner):
        with self.locked():
            doc=self.read(key)
            if doc['owner']!=owner:raise PermissionError()
            return self.public(doc)
    def list(self,owner):
        with self.locked():
            docs=[self.read(p.stem) for p in self.folder.glob('*.json') if p.stem.count('-')==4]
            return [self.public(d) for d in sorted(docs,key=lambda d:d['created_at'],reverse=True) if d['owner']==owner][:50]
    def approve(self,key,owner,seal):
        with self.locked():
            doc=self.read(key)
            if doc['owner']!=owner:raise PermissionError()
            if type(seal) is not str or not secrets.compare_digest(seal,doc['digest']):raise ImportRejected('PREVIEW_DIGEST_MISMATCH')
            if doc['status']!='PREVIEW':return self.public(doc)
            if datetime.fromisoformat(doc['expires_at'])<datetime.now(timezone.utc):raise ImportRejected('PREVIEW_EXPIRED')
            doc.update(status='QUEUED',approved_at=stamp(),updated_at=stamp());self.write(self.path(key),doc)
            return self.public(doc)
    def cancel(self,key,owner):
        with self.locked():
            doc=self.read(key)
            if doc['owner']!=owner:raise PermissionError()
            if doc['status'] not in ('PREVIEW','QUEUED'):raise ImportRejected('JOB_ALREADY_STARTED')
            doc.update(status='CANCELLED',updated_at=stamp());self.write(self.path(key),doc);return self.public(doc)
    def reconcile(self,key,owner):
        with self.locked():
            doc=self.read(key)
            if doc['owner']!=owner:raise PermissionError()
            if doc['status']!='NEEDS_REVIEW':raise ImportRejected('JOB_NOT_AWAITING_REVIEW')
        receipt=self.importer.receipt(key)
        with self.locked():
            doc=self.read(key)
            if receipt:
                doc.update(status='SUCCEEDED',result=dict(receipt,reconciled=True),updated_at=stamp());doc.pop('error_code',None)
                self.write(self.path(key),doc)
            return dict(self.public(doc),receipt_found=bool(receipt))
    def process_one(self):
        if not self._work_mutex.acquire(blocking=False):return False
        try:
            path=self.folder/'worker.lock'
            if path.is_symlink():raise RuntimeError('Unsafe lock')
            with path.open('a+b') as f:
                os.chmod(path,0o600)
                if os.name!='nt':
                    import fcntl
                    try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:return False
                try:return self._process_one()
                finally:
                    if os.name!='nt':fcntl.flock(f,fcntl.LOCK_UN)
        finally:self._work_mutex.release()
    def _process_one(self):
        with self.locked():
            candidates=[self.read(p.stem) for p in sorted(self.folder.glob('*.json')) if p.stem.count('-')==4]
            doc=next((d for d in candidates if d['status'] in ('QUEUED','RUNNING')),None)
            if not doc:return False
            path=self.path(doc['job_id']);interrupted=doc['status']=='RUNNING'
            doc.update(status='RUNNING',updated_at=stamp());self.write(path,doc)
        try:
            raw_path=self.folder/(doc['job_id']+'.payload')
            if raw_path.is_symlink():raise ImportRejected('UNSAFE_SOURCE')
            payloads=doc.get('payloads') or json.loads(raw_path.read_text(encoding='utf-8'))['payloads']
            if digest(dict(owner=doc['owner'],payloads=payloads,plans=doc['plans']))!=doc['digest']:raise ImportRejected('SEALED_SOURCE_CHANGED')
            if not self.authorized(doc['owner']):raise ImportRejected('ADMIN_ACCESS_REVOKED')
            prior=self.importer.receipt(doc['job_id'])
            if prior:result=dict(prior,reconciled=True)
            elif interrupted:raise ImportRejected('COMMIT_REQUIRES_RECONCILIATION')
            else:result=self.importer.apply(doc['job_id'],payloads,lambda rows:self.write(self.folder/(doc['job_id']+'.before'),dict(before=rows,job_id=doc['job_id'],created_at=stamp())),expected_plans=doc['plans'])
            doc.update(status='SUCCEEDED',result=result,updated_at=stamp())
        except Exception as error:
            code=str(error) if isinstance(error,ImportRejected) else 'IMPORT_FAILED'
            doc.update(status='NEEDS_REVIEW' if code=='COMMIT_REQUIRES_RECONCILIATION' else 'FAILED',error_code=code,updated_at=stamp())
        with self.locked():self.write(path,doc)
        return True
    def start(self):
        if self._started:return
        self._started=True
        def loop():
            while True:
                try:worked=self.process_one()
                except Exception:worked=False
                time.sleep(0.1 if worked else 1)
        threading.Thread(target=loop,name='catalog-import-worker',daemon=True).start()
