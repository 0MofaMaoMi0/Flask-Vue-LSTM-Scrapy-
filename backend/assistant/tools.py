"""Only bounded read operations. Identity is constructor-bound, never an argument."""
class ToolRegistry:
    def __init__(self,reader,user):self.reader=reader;self.user=user;self.calls=0
    def execute(self,name,arguments):
        if self.calls>=3:raise ValueError('Tool call limit')
        self.calls+=1
        if type(arguments) is not dict:raise ValueError('Tool arguments')
        if name not in {'search_movies','get_movie_detail','get_my_behavior_summary','get_local_recommendations','get_my_watchlist'}:raise ValueError('Tool not permitted')
        if name in {'search_movies','get_local_recommendations'}:
            if set(arguments)-{'filters','limit'}:raise ValueError('Unknown arguments')
            limit=arguments.get('limit',10)
            if type(limit) is not int or not 1<=limit<=10:raise ValueError('Limit')
            filters=validate_filters(arguments.get('filters',{}))
            rows,_,_,_=self.reader.resolve(self.user,'recommend' if name=='get_local_recommendations' else 'search',filters)
            return [facts(r) for r in rows[:limit]]
        if name=='get_movie_detail':
            if set(arguments)!={'movie_id'} or type(arguments['movie_id']) is not int or arguments['movie_id']<=0:raise ValueError('Movie ID')
            movies,_,_=self.reader.snapshot(self.user)
            return next((facts(m) for m in movies if m['movie_id']==arguments['movie_id']),None)
        if arguments:raise ValueError('No identity arguments permitted')
        movies,state,_=self.reader.snapshot(self.user);visible={str(m['movie_id']) for m in movies}
        if name=='get_my_behavior_summary':return {k:sum(bool(s.get(k)) for mid,s in state.items() if mid in visible) for k in ('favorite','want','watched','rating','not_interested')}
        return [facts(m) for m in movies if state.get(str(m['movie_id']),{}).get('want')][:10]
def facts(m):
    # Never send overview, credentials, raw behavior, SQL or source payload to model.
    return {k:m[k] for k in ('movie_id','title','genres','release_year','runtime_minutes','original_language') if k in m}
def validate_filters(f):
    from .conditions import validate_filters as validate
    return validate(f)
