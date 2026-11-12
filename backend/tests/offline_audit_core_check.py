"""SYNTHETIC only. Run with -I -B and explicit non-C output directory."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from decimal import Decimal
import datetime as dt

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);parser.add_argument('--negative-control',action='store_true');args=parser.parse_args()
    out=Path(args.output_dir).resolve()
    if not out.is_dir() or out.drive.upper()=='C:' or not out.is_absolute():raise ValueError('Approved non-C existing directory required')
    spec=importlib.util.spec_from_file_location('synthetic_core',Path(__file__).with_name('isolated_audit_core.py'))
    core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
    key=os.urandom(32);results=[]
    def check(name,fn):
        try:
            if fn() is not True: raise ValueError('Synthetic predicate false')
            results.append({'case':name,'expected':'predicate true','actual':'true','status':'PASS'})
        except Exception as error:
            results.append({'case':name,'expected':'predicate true','actual':type(error).__name__,'status':'FAIL'})
    def denied(fn):
        try:fn()
        except core.AuditError:return True
        return False
    cols=[['v','TEXT']]
    def dg(row=('SYNTHETIC',),**kw):
        d=dict(key=key,batch='SYNTHETIC-BATCH',database='SYNTHETIC-DB',table='SYNTHETIC-TABLE',columns=cols,purpose='ROW',row=row);d.update(kw);return core.digest(**d)
    check('stable recalculation',lambda:dg()==dg())
    check('column order',lambda:core.serialize([['a','INT'],['b','INT']],[1,2])!=core.serialize([['b','INT'],['a','INT']],[1,2]))
    check('NULL empty zero',lambda:len({core.packed(core.typed(x)) for x in (None,'',0)})==3)
    check('decimal scale',lambda:core.typed(Decimal('1.00'))!=core.typed(Decimal('1.0')))
    check('decimal precision',lambda:core.typed(Decimal('12345678901234567890.01'))!=core.typed(Decimal('12345678901234567890.02')))
    check('binary distinct',lambda:core.typed(b'abc')!=core.typed('abc'))
    check('unicode exact',lambda:dg(('é',))!=dg(('e\u0301',)))
    check('whitespace exact',lambda:dg((' x ',))!=dg(('x',)))
    check('bool int date datetime',lambda:len({core.packed(core.typed(x)) for x in (True,1,dt.date(2020,1,1),dt.datetime(2020,1,1))})==4)
    for dimension,value in [('batch','OTHER'),('database','OTHER'),('table','OTHER'),('purpose','PK'),('purpose','SCHEMA')]:
        check('domain '+dimension+' '+value,lambda dimension=dimension,value=value:dg()!=dg(**{dimension:value}))
    check('unknown type',lambda:denied(lambda:core.typed(object())))
    check('duplicate columns',lambda:denied(lambda:core.serialize([['v','INT'],['v','INT']],[1,2])))
    check('row structure',lambda:denied(lambda:core.serialize(cols,{})))
    check('type mismatch',lambda:denied(lambda:core.serialize(cols,[1])))
    check('nonfinite decimal',lambda:denied(lambda:core.typed(Decimal('NaN'))))
    check('timezone ambiguity',lambda:denied(lambda:core.typed(dt.datetime.now(dt.timezone.utc))))
    check('missing baseline arm',lambda:denied(lambda:core.Batch('SYNTHETIC').move('ARMED',True)))
    check('invalid state jump',lambda:denied(lambda:core.Batch('SYNTHETIC').move('RUNNING',True)))
    with tempfile.TemporaryDirectory(prefix='SYNTHETIC-',dir=out) as tmp:
        path=Path(tmp)/'state.json'
        def build():
            b=core.Batch('SYNTHETIC');b.move('BASELINE_BUILDING');b.commit_baseline('a'*64);return b
        b=build();check('missing proof',lambda:denied(lambda:b.move('ARMED')))
        b.move('ARMED',True);b.move('RUNNING',True);b.save(path,key)
        original=path.read_bytes()
        check('explicit persistent blocked',lambda:core.Batch.recover_and_seal_blocked(path,key,'SYNTHETIC').state=='SEALED_BLOCKED' and json.loads(path.read_bytes())['data']['history'][-1]=='SEALED_BLOCKED')
        path.write_bytes(original)
        unrelated=Path(tmp)/'unrelated.txt';unrelated.write_text('SYNTHETIC unrelated')
        check('unrelated overwrite refused',lambda:denied(lambda:b.save(unrelated,key)) and unrelated.read_text()=='SYNTHETIC unrelated')
        check('wrong key update refused',lambda:denied(lambda:b.save(path,b'q'*32)))
        other=core.Batch('OTHER')
        check('wrong batch update refused',lambda:denied(lambda:other.save(path,key)))
        old=json.loads(original);old.pop('format');path.write_text(json.dumps(old))
        check('old format refused',lambda:denied(lambda:core.Batch.recover(path,key,'SYNTHETIC')))
        path.write_bytes(original)
        check('unfinished RUNNING recovery blocked',lambda:core.Batch.recover(path,key,'SYNTHETIC').state=='SEALED_BLOCKED')
        check('wrong batch',lambda:denied(lambda:core.Batch.recover(path,key,'OTHER')))
        check('wrong key',lambda:denied(lambda:core.Batch.recover(path,b'z'*32,'SYNTHETIC')))
        for name,data in [('truncated',original[:12]),('damaged',b'not-json'),('duplicate keys',b'{"data":1,"data":2}')]:
            path.write_bytes(data);check(name,lambda:denied(lambda:core.Batch.recover(path,key,'SYNTHETIC')))
        obj=json.loads(original);obj['hmac']='0'*64;path.write_text(json.dumps(obj));check('HMAC tamper',lambda:denied(lambda:core.Batch.recover(path,key,'SYNTHETIC')))
        obj=json.loads(original);obj['unknown']=True;path.write_text(json.dumps(obj));check('unknown envelope field',lambda:denied(lambda:core.Batch.recover(path,key,'SYNTHETIC')))
        obj=json.loads(original);obj['committed']=False;path.write_text(json.dumps(obj));check('missing commit marker',lambda:denied(lambda:core.Batch.recover(path,key,'SYNTHETIC')))
        path.write_bytes(original)
        import hmac,hashlib
        auth=json.loads(original);auth['committed']=False
        auth['hmac']=hmac.new(key,b'MI2-STATE-V2\x00'+core.packed({k:v for k,v in auth.items() if k!='hmac'}),hashlib.sha256).hexdigest()
        auth['committed']=True;path.write_text(json.dumps(auth))
        check('committed false-to-true authenticated flip',lambda:denied(lambda:core.Batch.recover(path,key,'SYNTHETIC')))
        path.write_bytes(original)
        original_replace=core.os.replace
        def fail_replace(*args):raise OSError('SYNTHETIC replacement failure')
        core.os.replace=fail_replace
        try:
            try:core.Batch.recover_and_seal_blocked(path,key,'SYNTHETIC');failed=False
            except OSError:failed=True
            check('persistent block write failure rejects success',lambda:failed and path.read_bytes()==original)
        finally:core.os.replace=original_replace
        b.move('QUIESCING');b.move('POSTCHECK');b.move('SEALED_PASS',True);b.save(path,key)
        check('sealed valid recovery',lambda:core.Batch.recover(path,key,'SYNTHETIC').state=='SEALED_PASS')
        check('duplicate sealing',lambda:denied(lambda:b.move('SEALED_PASS',True)))
        def unchanged_reject(obj):
            before=path.read_bytes()
            return denied(lambda:obj.save(path,key)) and path.read_bytes()==before
        running=build();running.move('ARMED',True);running.move('RUNNING',True)
        path.unlink();running.save(path,key)
        check('F5 RUNNING to CREATED unchanged',lambda:unchanged_reject(core.Batch('SYNTHETIC')))
        running.move('QUIESCING');running.save(path,key)
        check('F5 legal prefix extension',lambda:json.loads(path.read_bytes())['data']['history'][-1]=='QUIESCING')
        core.Batch.recover_and_seal_blocked(path,key,'SYNTHETIC')
        check('F5 BLOCKED to CREATED unchanged',lambda:unchanged_reject(core.Batch('SYNTHETIC')))
        check('F5 stale state after persistent block unchanged',lambda:unchanged_reject(running))
        path.unlink();b.save(path,key)
        oldrun=build();oldrun.move('ARMED',True);oldrun.move('RUNNING',True)
        check('F5 PASS to RUNNING unchanged',lambda:unchanged_reject(oldrun))
        changed=core.Batch.recover(path,key,'SYNTHETIC',_raw=True);changed.baseline='b'*64
        check('F5 same history baseline changed',lambda:unchanged_reject(changed))
        changed=core.Batch.recover(path,key,'SYNTHETIC',_raw=True);changed.proof=False
        check('F5 same history proof changed',lambda:unchanged_reject(changed))
        before=path.read_bytes();b.save(path,key)
        check('F5 sealed idempotent bytes',lambda:path.read_bytes()==before)
        fail=core.Batch('SYNTHETIC')
        try:raise ValueError('SYNTHETIC fault')
        except ValueError:fail.block()
        check('exception forced blocked',lambda:fail.state=='SEALED_BLOCKED')
        path.unlink()
        fail.save(path,key);check('blocked recovery',lambda:core.Batch.recover(path,key,'SYNTHETIC').state=='SEALED_BLOCKED')
    if args.negative_control:check('EXPECTED FAILURE negative control',lambda:False)
    report={'label':'SYNTHETIC ONLY','cases':results,'passed':sum(x['status']=='PASS' for x in results),'failed':sum(x['status']=='FAIL' for x in results),'scope':'No DB, config, app, pytest, IPC, ACL, hardkill or real keys. Synthetic random key existed only in process memory; files use approved output dir.'}
    (out/'合成验证结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SYNTHETIC cases:',len(results),'failed:',report['failed'])
    return 1 if report['failed'] else 0
if __name__=='__main__':raise SystemExit(main())
