"""Bounded administrator quality sweep, with sealed scope and existing import receipts.

Only already-bound movies may receive metadata. Search and board candidates are
read-only evidence: no title/year auto-binding and no automatic new movies.
"""
import json, os, threading, time, uuid
from collections import Counter
from datetime import datetime, timezone
from .admin_importer import capture, ImportRejected, digest
from .import_jobs import ImportJobs, TERMINAL
from .tmdb_connection import TMDBError


def stamp():return datetime.now(timezone.utc).isoformat()


class QualityRuns:
    def __init__(self, folder, jobs, service):
        self.jobs, self.service = jobs, service
        self.store = ImportJobs(folder, jobs.importer, jobs.authorized)

    def latest(self, owner):
        self.recover_interrupted()
        docs = []
        for path in self.store.folder.glob('*.json'):
            doc = json.loads(path.read_text(encoding='utf-8'))
            if doc.get('owner') == owner:docs.append(doc)
        doc = max(docs, key=lambda d:d['created_at']) if docs else None
        if doc is None:return None
        from .data_quality import classify_search
        for item in doc['items']:
            if item.get('kind')=='gaps' and 'candidates' in item:item['review']=classify_search(item,item['candidates'])
        return dict({k:v for k,v in doc.items() if k not in ('owner', 'scope')},summary=dict(Counter(item['status'] for item in doc['items'])),
                    identity_review_summary=dict(Counter(item['review']['code'] for item in doc['items'] if 'review' in item)))

    def start(self, owner, binding, scope, seal):
        if digest(scope) != seal:raise ImportRejected('QUALITY_SCOPE_CHANGED')
        self.recover_interrupted()
        lease=self._lease()
        if lease is None:raise ImportRejected('QUALITY_RUN_ACTIVE')
        try:
            with self.store.locked():
                for path in self.store.folder.glob('*.json'):
                    old=json.loads(path.read_text(encoding='utf-8'))
                    if old.get('status') == 'RUNNING':raise ImportRejected('QUALITY_RUN_ACTIVE')
                    if any(item.get('status') in ('IMPORT_PREPARED','NEEDS_REVIEW') for item in old.get('items',[])):raise ImportRejected('QUALITY_PREVIOUS_COMMIT_REQUIRES_REVIEW')
                doc=dict(job_id=str(uuid.uuid4()), owner=owner, scope=scope, scope_digest=seal, status='RUNNING', created_at=stamp(), updated_at=stamp(), items=[],
                         total=sum(len(scope[k]) for k in ('refresh','gaps','pending')), completed=0,
                         boundaries='Only existing confirmed TMDB bindings may receive metadata; candidate review never writes identities or movies.')
                self.store.write(self.store.path(doc['job_id']),doc)
            threading.Thread(target=self._run,args=(doc,binding,lease),daemon=True).start()
        except Exception:
            lease.close();raise
        return self.latest(owner)

    def _lease(self):
        path=self.store.folder/'active.lock'
        if path.is_symlink():raise RuntimeError('Unsafe quality lock')
        handle=path.open('a+b');os.chmod(path,0o600)
        if os.name!='nt':
            import fcntl
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:handle.close();return None
        return handle

    def recover_interrupted(self):
        # Linux releases the lease on process death. Never replay network calls
        # or a potentially committed SQL transaction during recovery.
        if os.name=='nt':return
        lease=self._lease()
        if lease is None:return
        try:
            with self.store.locked():
                for path in self.store.folder.glob('*.json'):
                    old=json.loads(path.read_text(encoding='utf-8'))
                    if old.get('status')!='RUNNING':continue
                    for item in old.get('items',[]):
                        if item.get('status')=='IMPORT_PREPARED':
                            job=self.jobs.get(item['import_job_id'],old['owner'])
                            item.update(status=job['status'] if job['status'] in TERMINAL else 'NEEDS_REVIEW',result=job.get('result'))
                    old.update(status='STOPPED',error_code='PROCESS_INTERRUPTED_NO_AUTOMATIC_REPLAY',updated_at=stamp())
                    self.store.write(path,old)
        finally:lease.close()

    def cancel(self, owner):
        doc=self.latest(owner)
        if not doc or doc['status']!='RUNNING':raise ImportRejected('QUALITY_NOT_RUNNING')
        self.store.write(self.store.path(doc['job_id']).with_suffix('.cancel'),dict(requested_at=stamp()))
        return dict(cancel_requested=True)

    def resume(self, key, owner, binding):
        lease=self._lease()
        if lease is None:raise ImportRejected('QUALITY_RUN_ACTIVE')
        try:
            with self.store.locked():
                doc=self.store.read(key)
                if doc['owner']!=owner:raise PermissionError()
                resumable={'NETWORK_UNAVAILABLE','UPSTREAM_UNAVAILABLE','RATE_LIMITED','COOLDOWN'}
                if doc['status']!='STOPPED' or doc.get('error_code') not in resumable:raise ImportRejected('QUALITY_RESUME_REQUIRES_REVIEW')
                if digest(doc['scope'])!=doc['scope_digest'] or doc['completed']!=len(doc['items']) or any(i['status'] in ('IMPORT_PREPARED','NEEDS_REVIEW') for i in doc['items']):raise ImportRejected('QUALITY_RESUME_REQUIRES_REVIEW')
                if any(json.loads(p.read_text(encoding='utf-8')).get('status')=='RUNNING' for p in self.store.folder.glob('*.json')):raise ImportRejected('QUALITY_RUN_ACTIVE')
                doc.setdefault('resume_history',[]).append(dict(at=stamp(),previous_error=doc['error_code'],completed=doc['completed']))
                doc.update(status='RUNNING',error_code=None,updated_at=stamp())
                self.store.write(self.store.path(key),doc)
            threading.Thread(target=self._run,args=(doc,binding,lease),daemon=True).start()
        except Exception:
            lease.close();raise
        return self.latest(owner)

    def _save(self, doc, item):
        doc['items'].append(item);doc['completed']+=1;doc['updated_at']=stamp()
        self.store.write(self.store.path(doc['job_id']),doc)

    def _wait_for_network(self,doc):
        # TMDBConnection applies a 30-second cooldown. This is only a public
        # idempotent GET retry; never a provider generation or SQL replay.
        for _ in range(31):
            if self.store.path(doc['job_id']).with_suffix('.cancel').exists():raise TMDBError('CANCELLED')
            time.sleep(1)

    def _get_read(self,doc,binding,path,params):
        for attempt in range(3):
            try:
                result=self.service._get(doc['owner'],binding,path,params)
                doc.pop('network_wait',None)
                return result
            except TMDBError as error:
                if error.code not in ('NETWORK_UNAVAILABLE','UPSTREAM_UNAVAILABLE') or attempt==2:raise
                doc['network_wait']=dict(at=stamp(),attempt=attempt+1,code=error.code,wait_seconds=31)
                doc.setdefault('network_events',[]).append(dict(doc['network_wait'],completed=doc['completed']))
                self.store.write(self.store.path(doc['job_id']),doc)
                self._wait_for_network(doc)

    def _run(self, doc, binding, lease=None):
        owner=doc['owner']
        skip=doc['completed']
        try:
            for mode, targets in ((k,doc['scope'][k]) for k in ('gaps','pending','refresh')):
                for target in targets:
                    if skip:
                        skip-=1;continue
                    if self.store.path(doc['job_id']).with_suffix('.cancel').exists():raise ImportRejected('CANCELLED_AFTER_CURRENT_ITEM')
                    if not self.jobs.authorized(owner):raise ImportRejected('ADMIN_ACCESS_REVOKED')
                    try:
                        if mode=='gaps':
                            raw=self._get_read(doc,binding,'search/movie',dict(query=target['title'],language='zh-CN',include_adult='false',page=1))
                            candidates=[{k:p.get(k) for k in ('id','title','original_title','release_date','vote_average','vote_count')} for p in raw.get('results',[]) if type(p) is dict and type(p.get('id')) is int and p.get('adult') is not True]
                            item=dict(kind=mode,**target,status='CANDIDATE_REQUIRES_REVIEW' if candidates else 'SEARCH_NO_MOVIE_CANDIDATE', candidates=candidates,
                                      total_results=raw.get('total_results'), pages_checked=1, reason='仅核验首个官方电影搜索结果页；同名同年不足以自动绑定，不搜索或绑定 TV ID。')
                        else:
                            tid=int(target['tmdb_id']) if mode=='refresh' else target
                            raw=self._get_read(doc,binding,'movie/'+str(tid),dict(language='zh-CN'))
                            if raw.get('id')!=tid:raise ImportRejected('UPSTREAM_IDENTITY_MISMATCH')
                            payload=capture(raw)
                            item=dict(kind=mode,tmdb_id=tid,title=raw.get('title'),year=str(raw.get('release_date') or '')[:4],imdb_id=raw.get('imdb_id'),
                                      vote_average=raw.get('vote_average'),vote_count=raw.get('vote_count'),revenue=raw.get('revenue'),captured_at=stamp(),payload_hash=digest(payload))
                            if mode=='pending':
                                from .data_quality import pending_identity
                                item.update(pending_identity(self.jobs.importer.engine,raw),reason='官方 movie 详情与本地外部身份、片名交叉核验；未新增电影或身份。')
                            else:
                                payload['metadata_only']=True
                                plans=self.jobs.importer.preview([payload])
                                if len(plans)!=1 or plans[0]['movie_id']!=target['movie_id']:raise ImportRejected('QUALITY_TARGET_CHANGED')
                                preview=self.jobs.preview(owner,[payload],plans)
                                # Persist the exact receipt identity before approving any write.
                                item.update(import_job_id=preview['job_id'],movie_id=target['movie_id'],status='IMPORT_PREPARED')
                                self._save(doc,item)
                                self.jobs.approve(preview['job_id'],owner,preview['digest']);self.jobs.start()
                                deadline=time.monotonic()+120
                                while time.monotonic()<deadline:
                                    job=self.jobs.get(preview['job_id'],owner)
                                    if job['status'] in TERMINAL:break
                                    time.sleep(.2)
                                else:raise ImportRejected('QUALITY_IMPORT_WAIT_EXPIRED')
                                item.update(status=job['status'],error_code=job.get('error_code'),result=job.get('result'))
                                doc['updated_at']=stamp();self.store.write(self.store.path(doc['job_id']),doc)
                                if job['status']=='NEEDS_REVIEW':raise ImportRejected('COMMIT_REQUIRES_RECONCILIATION')
                                continue
                        self._save(doc,item)
                    except TMDBError as error:
                        if error.code!='NOT_FOUND':raise
                        self._save(doc,dict(kind=mode,target=target,status='REQUIRES_REVIEW',error_code='UPSTREAM_MOVIE_NOT_FOUND'))
                    except (ImportRejected,ValueError) as error:
                        if str(error) in ('COMMIT_REQUIRES_RECONCILIATION','QUALITY_IMPORT_WAIT_EXPIRED'):raise
                        self._save(doc,dict(kind=mode,target=target,status='REQUIRES_REVIEW',error_code=str(error) if isinstance(error,ImportRejected) else 'INVALID_UPSTREAM'))
            doc['status']='COMPLETED'
        except Exception as error:
            doc['status']='STOPPED';doc['error_code']=error.code if isinstance(error,TMDBError) else str(error) if isinstance(error,ImportRejected) else type(error).__name__
        finally:
            try:
                doc['updated_at']=stamp();self.store.write(self.store.path(doc['job_id']),doc)
            finally:
                if lease is not None:lease.close()
