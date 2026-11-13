"""Refresh public official boards; operator key is separate from user BYOK.
Never log upstream URLs, credentials or exception messages. Only the existing official-host fallback for public GETs.
"""
import json, os, re, sys, time, secrets, stat
import urllib.error
STAGE="START"
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from catalog.board_snapshots import directory
from catalog.tmdb_connection import TMDBConnection
ENDPOINTS={'popular':'movie/popular','trending_day':'trending/movie/day','trending_week':'trending/movie/week'}

def main():
    global STAGE
    STAGE='KEY_FILE'
    keyfile=Path(os.environ.get('MI2_TMDB_KEY_FILE','/etc/movieinsight-m6/tmdb_key'))
    st=keyfile.lstat()
    if stat.S_ISREG(st.st_mode):print(json.dumps(dict(key_file_present=True,key_file_mode=oct(stat.S_IMODE(st.st_mode)),key_file_owner=st.st_uid,secrets_printed=False)))
    if keyfile.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:raise ValueError('unsafe key file')
    key=next((l.strip() for l in keyfile.read_text().splitlines() if re.fullmatch(r'[A-Za-z0-9]{32}',l.strip())),None)
    if not key:raise ValueError('invalid key file')
    def get(path,params):
        time.sleep(0.3)
        # Reuse fixed official hosts and their existing read-only fallback.
        return TMDBConnection._http(path,dict(params,api_key=key,language='zh-CN'),None)
    STAGE='UPSTREAM_GENRES'
    genres=get('genre/movie/list',{})['genres'];folder=directory();folder.mkdir(parents=True,exist_ok=True)
    if folder.is_symlink():raise ValueError('unsafe cache')
    counts={}
    for channel,endpoint in ENDPOINTS.items():
        STAGE='UPSTREAM_'+channel.upper()
        items=[];seen=set()
        for page in range(1,4):
            rows=get(endpoint,{'page':page})['results']
            if not isinstance(rows,list):raise ValueError('invalid upstream')
            for offset,row in enumerate(rows):
                if type(row.get('id')) is not int or row['id']<=0:raise ValueError('invalid upstream')
                if row['id'] in seen:continue
                seen.add(row['id']);item={k:row.get(k) for k in ('id','title','original_title','release_date','original_language','poster_path','genre_ids','vote_average','vote_count','popularity','adult','overview')}
                item['source_rank']=(page-1)*20+offset+1;items.append(item)
        doc=dict(schema=1,channel=channel,endpoint=endpoint,captured_at=datetime.now(timezone.utc).isoformat(),items=items,genres=genres,pages=3)
        temp=folder/(channel+'.'+secrets.token_hex(8)+'.tmp')
        try:
            with temp.open('x',encoding='utf-8') as f:json.dump(doc,f,ensure_ascii=False)
            temp.chmod(0o644);os.replace(temp,folder/(channel+'.json'))
        finally:
            temp.unlink(missing_ok=True)
        counts[channel]=len(items)
    print(json.dumps(dict(updated=counts,secrets_printed=False)))
if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps(dict(updated=False,code='REFRESH_FAILED_RETAIN_CACHE',stage=STAGE,error_type=type(e).__name__,http_status=e.code if isinstance(e,urllib.error.HTTPError) else None,secrets_printed=False)));sys.exit(1)
