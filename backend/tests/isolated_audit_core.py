"""SYNTHETIC offline audit data core. No database, config, IPC or test runner."""
import base64
import datetime as dt
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile

VERSION = 'MI2-AUDIT-v1'
STATE_FORMAT = 'MI2-STATE-v2'
STATES = ('CREATED','BASELINE_BUILDING','BASELINE_COMMITTED','ARMED','RUNNING','QUIESCING','POSTCHECK','SEALED_PASS','SEALED_BLOCKED')
class AuditError(ValueError):
    pass

def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def typed(value):
    if value is None: return ['NULL']
    if type(value) is bool: return ['BOOL', value]
    if type(value) is int: return ['INT', str(value)]
    if type(value) is Decimal:
        if not value.is_finite(): raise AuditError('Nonfinite decimal')
        t=value.as_tuple()
        return ['DECIMAL', t.sign, ''.join(map(str,t.digits)), t.exponent]
    if type(value) is str: return ['TEXT', value]
    if type(value) is bytes: return ['BYTES', base64.b64encode(value).decode('ascii')]
    if type(value) is dt.datetime:
        if value.tzinfo is not None: raise AuditError('Only explicit naive synthetic datetime supported')
        return ['DATETIME_NAIVE',value.isoformat(timespec='microseconds'),value.fold]
    if type(value) is dt.date: return ['DATE',value.isoformat()]
    raise AuditError('Unsupported type')

def columns_checked(columns):
    if type(columns) not in (tuple,list) or not columns: raise AuditError('Columns required')
    seen=set(); result=[]
    for c in columns:
        if type(c) not in (tuple,list) or len(c)!=2 or any(type(x) is not str or not x for x in c): raise AuditError('Invalid column')
        if c[0] in seen: raise AuditError('Duplicate column')
        if c[1] not in {'NULL','BOOL','INT','DECIMAL','TEXT','BYTES','DATE','DATETIME_NAIVE'}: raise AuditError('Unknown column type')
        seen.add(c[0]);result.append(list(c))
    return result

def serialize(columns,row):
    cols=columns_checked(columns)
    if type(row) not in (tuple,list) or len(row)!=len(cols): raise AuditError('Row shape mismatch')
    vals=[typed(v) for v in row]
    for c,v in zip(cols,vals):
        if v[0]!='NULL' and c[1]!=v[0]: raise AuditError('Column type mismatch')
    return packed([VERSION,cols,vals])

def digest(key,batch,database,table,columns,purpose,row):
    if type(key) is not bytes or len(key)<32: raise AuditError('Synthetic key too short')
    if any(type(x) is not str or not x for x in (batch,database,table)) or purpose not in ('PK','ROW','SCHEMA','STATE'): raise AuditError('Invalid domain')
    domain=packed([VERSION,batch,database,table,columns_checked(columns),purpose])
    body=serialize(columns,row)
    message=len(domain).to_bytes(8,'big')+domain+len(body).to_bytes(8,'big')+body
    return hmac.new(key,message,hashlib.sha256).hexdigest()

def unique_object(pairs):
    d={}
    for k,v in pairs:
        if k in d: raise AuditError('Duplicate JSON key')
        d[k]=v
    return d

class Batch:
    """Caller owns synthetic key in memory; no key storage or online ARM facility."""
    def __init__(self,batch):
        if type(batch) is not str or not batch: raise AuditError('Batch required')
        self.batch=batch;self.history=['CREATED'];self.baseline=None;self.proof=False
    @property
    def state(self): return self.history[-1]
    def commit_baseline(self,summary):
        if self.state!='BASELINE_BUILDING' or type(summary) is not str or len(summary)!=64 or any(c not in '0123456789abcdef' for c in summary): raise AuditError('Baseline not ready')
        self.baseline=summary
        self.move('BASELINE_COMMITTED')
    def move(self,target,proof=False):
        if self.state.startswith('SEALED_'): raise AuditError('Already sealed')
        if target=='SEALED_BLOCKED': self.history.append(target);return
        expected=STATES[STATES.index(self.state)+1]
        if target!=expected: raise AuditError('Invalid transition')
        if target=='BASELINE_COMMITTED' and self.baseline is None: raise AuditError('Missing baseline')
        if target in ('ARMED','RUNNING','SEALED_PASS'):
            if self.baseline is None or proof is not True: raise AuditError('Missing synthetic precondition proof')
            self.proof=True
        self.history.append(target)
    def block(self):
        self.move('SEALED_BLOCKED')
    @staticmethod
    def safe_path(path):
        raw=Path(path).absolute()
        for part in (raw, *raw.parents):
            if part.exists() or part.is_symlink():
                st=part.lstat()
                if part.is_symlink() or getattr(st,'st_file_attributes',0) & 1024:
                    raise AuditError('Reparse path rejected')
        resolved=raw.resolve()
        if resolved.drive.upper()=='C:' or not resolved.parent.is_dir():
            raise AuditError('Approved non-C directory required')
        return resolved

    def recover_and_seal_blocked(cls_path, key, batch):
        # Explicit writing recovery; recover itself remains read-only.
        state=Batch.recover(cls_path,key,batch)
        if state.state=='SEALED_BLOCKED':state.save(cls_path,key)
        return state

    def save(self,path,key):
        path=self.safe_path(path)
        if path.drive.upper()=='C:' or not path.is_absolute() or not path.parent.is_dir(): raise AuditError('Non-C existing directory required')
        if type(key) is not bytes or len(key)<32: raise AuditError('Key required')
        candidate=Batch(self.batch)
        for target in self.history[1:]:
            if target=='BASELINE_COMMITTED':candidate.commit_baseline(self.baseline)
            else:candidate.move(target,proof=self.proof)
        if candidate.history!=self.history or candidate.baseline!=self.baseline or candidate.proof!=self.proof:
            raise AuditError('Invalid candidate state')
        data={'version':VERSION,'batch':self.batch,'history':self.history,'baseline':self.baseline,'proof':self.proof,'synthetic':True}
        authenticated={'format':STATE_FORMAT,'data':data,'committed':True}
        tag=hmac.new(key,b'MI2-STATE-V2\x00'+packed(authenticated),hashlib.sha256).hexdigest()
        envelope=packed(dict(authenticated,hmac=tag))
        existing=None
        if path.exists():
            previous=Batch.recover(path,key,self.batch,_raw=True)
            # Validate candidate through the same authenticated state-machine reader
            # before any publication; compare immutable history and core fields.
            old_history=previous.history
            if self.history[:len(old_history)]!=old_history:
                raise AuditError('History rollback or branch change')
            if previous.baseline is not None and self.baseline!=previous.baseline:
                raise AuditError('Baseline changed')
            if previous.proof and not self.proof:
                raise AuditError('Proof rollback')
            if self.history==old_history and (self.baseline!=previous.baseline or self.proof!=previous.proof):
                raise AuditError('Core fields changed without transition')
            if previous.state.startswith('SEALED_'):
                if self.history!=old_history or self.baseline!=previous.baseline or self.proof!=previous.proof:
                    raise AuditError('Sealed state immutable')
            # Replay proposed history rather than trusting mutable object fields.
            candidate=Batch(self.batch)
            for target in self.history[1:]:
                if target=='BASELINE_COMMITTED':candidate.commit_baseline(self.baseline)
                else:candidate.move(target,proof=self.proof)
            if candidate.baseline!=self.baseline or candidate.proof!=self.proof:
                raise AuditError('Invalid candidate core fields')
            existing=path.read_bytes()
            if previous.state.startswith('SEALED_'):
                if existing!=envelope:raise AuditError('Sealed bytes immutable')
                return
        name=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
                name=f.name;f.write(envelope);f.flush();os.fsync(f.fileno())
            self.safe_path(path)
            if existing is None:
                # Exclusive first publication: never replace an unrelated creation.
                os.link(name,path)
                os.unlink(name)
            else:
                # Controlled synthetic directory only: cross-process TOCTOU remains.
                if not path.exists() or path.read_bytes()!=existing:
                    raise AuditError('Concurrent target change')
                Batch.recover(path,key,self.batch)
                os.replace(name,path)
        except BaseException:
            if name is not None:
                try: os.unlink(name)
                except OSError: pass
            raise
    @classmethod
    def recover(cls,path,key,batch, *, _raw=False):
        try:
            p=cls.safe_path(path)
            if p.drive.upper()=='C:': raise AuditError('C drive rejected')
            if type(key) is not bytes or len(key)<32: raise AuditError('Key required')
            obj=json.loads(p.read_bytes(),object_pairs_hook=unique_object)
            if type(obj) is not dict or set(obj)!={'format','data','hmac','committed'} or obj['format']!=STATE_FORMAT or obj['committed'] is not True: raise AuditError('Envelope invalid')
            d=obj['data']
            if type(d) is not dict or set(d)!={'version','batch','history','baseline','proof','synthetic'}: raise AuditError('Fields invalid')
            if d['version']!=VERSION or d['batch']!=batch or d['synthetic'] is not True or type(d['proof']) is not bool: raise AuditError('Domain mismatch')
            expected=hmac.new(key,b'MI2-STATE-V2\x00'+packed({'format':obj['format'],'data':d,'committed':obj['committed']}),hashlib.sha256).hexdigest()
            if type(obj['hmac']) is not str or not hmac.compare_digest(expected,obj['hmac']): raise AuditError('Integrity failed')
            hist=d['history']
            if type(hist) is not list or not hist or hist[0]!='CREATED': raise AuditError('History invalid')
            state=cls(batch)
            for target in hist[1:]:
                if target=='BASELINE_COMMITTED':state.commit_baseline(d['baseline'])
                else:state.move(target,proof=d['proof'])
            if state.baseline!=d['baseline'] or state.proof!=d['proof']:raise AuditError('Inconsistent markers')
            if not _raw and not state.state.startswith('SEALED_'):state.block()
            return state
        except AuditError: raise
        except Exception: raise AuditError('Invalid or incomplete state') from None
