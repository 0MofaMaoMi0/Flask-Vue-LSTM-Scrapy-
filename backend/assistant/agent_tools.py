"""V2 read-only Agent tools, grounded library IDs and labelled discovery."""
import json,os,urllib.request,urllib.parse
from .tools import validate_filters,facts
from .conditions import merge_for_tool,hard_conditions
class AgentTools:
    def __init__(self,reader,user,initial=(),conditions=None):self.reader=reader;self.user=user;self.calls=0;self.conditions=conditions or {};self.model_hints=[];self.observed={m['movie_id']:self.dto(m) for m in initial}
    def dto(self,m):return dict(facts(m),overview=str(m.get('overview') or '')[:1200],local_reasons=m.get('local_reasons',[]),tmdb_popularity=m.get('tmdb_popularity'),popularity_observed_at=m.get('popularity_observed_at'))
    def execute(self,name,args):
        if type(args) is not dict:raise ValueError('Tool arguments')
        self.calls+=1
        if name=='get_recommendation_board':
            if set(args)-{'channel','filters','keyword','limit','scope'} or 'channel' not in args:raise ValueError('Channel arguments')
            from catalog.recommendation_boards import CHANNELS
            channel=args['channel'];limit=args.get('limit',10);keyword=args.get('keyword','')
            if type(channel) is not str or channel not in CHANNELS or type(limit) is not int or not 1<=limit<=10:raise ValueError('Channel arguments')
            filters=merge_for_tool(self.conditions,validate_filters(args.get('filters',{})))
            result=self.reader.recommendation_board(self.user,channel,filters,keyword,limit,scope=args.get('scope','all'))
            for movie in result['items']:
                if movie['in_catalog']:self.observed[movie['movie_id']]=self.dto(movie)
            return result
        if name in ('search_movies','get_local_recommendations'):
            if set(args)-{'filters','keyword','limit'}:raise ValueError('Unknown arguments')
            proposed=validate_filters(args.get('filters',{}));filters=merge_for_tool(self.conditions,proposed);self.model_hints.append(proposed);self.model_hints=self.model_hints[-3:];keyword=args.get('keyword','');limit=args.get('limit',10)
            if type(keyword) is not str or len(keyword)>100 or type(limit) is not int or not 1<=limit<=10:raise ValueError('Arguments')
            if name=='search_movies':rows,truncated=self.reader.search(self.user,filters,keyword,limit)
            else:
                rows,_,truncated,_=self.reader.resolve(self.user,'recommend',filters,**({'keyword':keyword} if keyword else {}))
            result=[self.dto(m) for m in rows[:limit]]
            for m in result:self.observed[m['movie_id']]=m
            return {'items':result,'candidate_window_truncated':truncated,'scope':'LOCAL_CATALOG'}
        if name in ('get_movie_detail','compare_movies'):
            if name=='get_movie_detail':
                if set(args)!={'movie_id'}:raise ValueError('Arguments')
                ids=[args['movie_id']]
            else:
                if set(args)!={'movie_ids'} or type(args['movie_ids']) is not list or not 2<=len(args['movie_ids'])<=4:raise ValueError('Arguments')
                ids=args['movie_ids']
            if any(type(mid) is not int or mid<1 for mid in ids):raise ValueError('IDs')
            movies=self.reader.get_movies(ids);result=[self.dto(m) for m in movies]
            for m in result:self.observed[m['movie_id']]=m
            return {'items':result,'missing_ids':[mid for mid in ids if mid not in self.observed]}
        if name not in ('get_my_behavior_summary','get_my_watchlist') or args:raise ValueError('Tool denied')
        movies,state,truncated=self.reader.snapshot(self.user)
        if name=='get_my_behavior_summary':
            visible={str(m['movie_id']):m for m in movies};counts={k:0 for k in ('favorite','want','rating','watched','not_interested')};preferences=[]
            for mid,s in state.items():
                if mid not in visible:continue
                for k in counts:counts[k]+=bool(s.get(k))
                if s.get('favorite') or s.get('want') or s.get('rating',0)>=4:preferences.extend(visible[mid].get('genres',[]))
            return {'counts':counts,'preferred_genres':sorted(set(preferences))[:20],'scope':'CURRENT_AUTHENTICATED_USER_ONLY'}
        result=[self.dto(m) for m in movies if state.get(str(m['movie_id']),{}).get('want')][:10]
        for m in result:self.observed[m['movie_id']]=m
        return {'items':result,'scope':'CURRENT_AUTHENTICATED_USER_ONLY'}

def schemas():
    def tool(name,description,properties,required=()):return {'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}}
    filters={'type':'object','properties':{'genre':{'type':'string','description':'One genre; use genres_any for alternatives, genres_all for conjunction.'},'genres_any':{'type':'array','items':{'type':'string'},'description':'OR: at least one of these genres.'},'genres_all':{'type':'array','items':{'type':'string'},'description':'AND: every listed genre must be present.'},'excluded_genres':{'type':'array','items':{'type':'string'}},'year_from':{'type':'integer'},'year_to':{'type':'integer'},'years_any':{'type':'array','items':{'type':'integer'}},'year_ranges_any':{'type':'array','items':{'type':'array','items':{'type':'integer'}}},'excluded_year_ranges':{'type':'array','items':{'type':'array','items':{'type':'integer'}}},'runtime_max':{'type':'integer'},'language':{'type':'string'},'unwatched':{'type':'boolean'}},'additionalProperties':False}
    search={'filters':filters,'keyword':{'type':'string'},'limit':{'type':'integer'}}
    from catalog.recommendation_boards import CHANNELS
    return [tool('get_recommendation_board','Read a recommendation channel with no extra model calls. Public charts may contain clearly labelled external entries; never treat external tmdb_id as movie_id. Popular/trending preserve TMDB source rank. Revenue is cumulative USD, not daily box office. Conditions and user behavior always enforced.',dict(search,channel={'type':'string','enum':list(CHANNELS)},scope={'type':'string','enum':['all','local']}),['channel']),tool('search_movies','Search the active local catalog by current conditions; limit <=10.',search),tool('get_local_recommendations','Use local personalized recommendation ranking, respecting filters.',search),tool('get_movie_detail','Read a visible local movie.',{'movie_id':{'type':'integer'}},['movie_id']),tool('compare_movies','Compare two to four visible library movies.',{'movie_ids':{'type':'array','items':{'type':'integer'}}},['movie_ids']),tool('get_my_behavior_summary','Read current user preference summary, without identity or private authentication.',{}),tool('get_my_watchlist','Read only the current user watchlist.',{})]

def verify_final(content,observed,reader,user,filters=None,behavior_query=False):
    from .structured_intent import load_json,validate_json,FINAL_SCHEMA
    try:body=load_json(content)
    except Exception:raise ValueError('Invalid final JSON') from None
    if type(body) is not dict or set(body)-{'answer','movie_ids','discoveries'}:raise ValueError('Final fields')
    # Preserve optional legacy fields, but use the same strict types as the
    # terminal tool. Never coerce string/dict IDs or silently discard fields.
    body=dict(body)
    body.setdefault('movie_ids',[]);body.setdefault('discoveries',[])
    try:validate_json(body,FINAL_SCHEMA)
    except ValueError:raise ValueError('Invalid final output') from None
    answer=body.get('answer');ids=body.get('movie_ids',[]);external=body.get('discoveries',[])
    if type(answer) is not str or not 1<=len(answer)<=12000 or type(ids) is not list or len(ids)>10 or len(set(ids))!=len(ids) or any(type(i) is not int or i not in observed for i in ids):raise ValueError('Invalid final output')
    current=reader.get_movies(ids) if hasattr(reader,'get_movies') else reader.snapshot(user)[0];visible={m['movie_id']:m for m in current}
    if any(mid not in visible for mid in ids):raise ValueError('Visibility changed')
    if filters is not None:
        from .intent import matches
        behavior=reader.behavior.snapshot(user)
        if (ids or external) and filters.get('_needs_clarification'):raise ValueError('Unresolved explicit user conditions')
        for mid in ids:
            state={} if behavior_query else behavior.get(str(mid),{})
            if not matches(visible[mid],filters,state):raise ValueError('Final movie violates server-enforced conditions')
    items=[dict(facts(visible[mid]),in_catalog=True,verification='LOCAL_DATABASE',reasons=['详见AI生成解释']) for mid in ids]
    if type(external) is not list or len(external)>5:raise ValueError('Discovery limit')
    discoveries=[]
    for item in external:
        if type(item) is not dict or set(item)-{'title','year','reason'} or type(item.get('title')) is not str or not 1<=len(item['title'])<=200 or type(item.get('reason','')) is not str or len(item.get('reason',''))>1000:raise ValueError('Discovery fields')
        year=item.get('year')
        if year is not None and (type(year) is not int or not 1880<=year<=2100):raise ValueError('Discovery year')
        discoveries.append(dict(item,in_catalog=False,verification='UNVERIFIED_MODEL_SUGGESTION',movie_id=None))
    return {'answer':answer,'items':items,'discoveries':discoveries}
