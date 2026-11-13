"""Optional TMDB title verification; no catalog writes, no model-selected URLs."""
import os,json,urllib.request,urllib.parse
from .agent_provider import NoRedirect
class TMDBVerifier:
    def __init__(self,token=None):self.token=token if token is not None else os.environ.get('MI2_TMDB_READ_TOKEN','')
    def verify(self,title,year=None):
        from catalog.tmdb_connection import current,TMDBError
        context=current()
        if context and context[0].status(context[1],context[2])['enabled']:
            service,user,binding=context
            try:
                tid=service.search(user,binding,title,year)
                if tid:return {'verification':'TMDB_TITLE_YEAR_MATCH','tmdb_id':tid,'in_catalog':False,'movie_id':None,'tmdb_url':'https://www.themoviedb.org/movie/'+str(tid)}
            except TMDBError:pass
        from catalog.recommendation_boards import PUBLIC,public_snapshot
        for channel in sorted(PUBLIC):
            snapshot=public_snapshot(channel,online=False)
            for movie in (snapshot or {}).get('items',[]):
                if movie.get('adult') is True or type(movie.get('id')) is not int or movie['id']<1:continue
                if title.casefold().strip() not in [str(movie.get(k) or '').casefold().strip() for k in ('title','original_title')]:continue
                if year and not str(movie.get('release_date') or '').startswith(str(year)):continue
                return {'verification':'TMDB_OFFICIAL_LIST_SNAPSHOT','tmdb_id':movie['id'],'in_catalog':False,'movie_id':None,'tmdb_url':'https://www.themoviedb.org/movie/'+str(movie['id']),'source_as_of':snapshot['captured_at']}
        if not self.token:return {'verification':'UNVERIFIED_NO_TMDB_CREDENTIAL','in_catalog':False,'movie_id':None}
        params={'query':title,'language':'zh-CN','include_adult':'false'}
        if year:params['year']=str(year)
        request=urllib.request.Request('https://api.themoviedb.org/3/search/movie?'+urllib.parse.urlencode(params),headers={'Authorization':'Bearer '+self.token})
        try:
            with urllib.request.build_opener(NoRedirect,urllib.request.ProxyHandler({})).open(request,timeout=5) as r:raw=r.read(262145)
            if len(raw)>262144:raise ValueError()
            for movie in json.loads(raw).get('results',[])[:10]:
                names=[movie.get('title',''),movie.get('original_title','')]
                if type(movie.get('id')) is not int or movie.get('adult') is True:continue
                if not any(n.casefold()==title.casefold() for n in names):continue
                if year and not str(movie.get('release_date','')).startswith(str(year)):continue
                return {'verification':'TMDB_TITLE_YEAR_MATCH','tmdb_id':movie['id'],'in_catalog':False,'movie_id':None,'tmdb_url':'https://www.themoviedb.org/movie/'+str(movie['id'])}
        except Exception:return {'verification':'UNVERIFIED_TMDB_UNAVAILABLE','in_catalog':False,'movie_id':None}
        return {'verification':'UNVERIFIED_TMDB_NO_EXACT_MATCH','in_catalog':False,'movie_id':None}
