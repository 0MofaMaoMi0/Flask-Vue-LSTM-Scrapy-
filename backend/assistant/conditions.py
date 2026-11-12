"""Canonical, typed movie conditions shared by SQL, tools and final verification."""
FILTER_KEYS={'genre','genres_any','genres_all','excluded_genres','year_from','year_to','years_any','excluded_year_ranges','year_ranges_any','runtime_max','language','unwatched'}
def validate_filters(value):
    if type(value) is not dict or set(value)-FILTER_KEYS:raise ValueError('Filters')
    f=dict(value)
    for key,v in f.items():
        if key in ('year_from','year_to') and (type(v) is not int or not 1880<=v<=2100):raise ValueError('Year')
        if key=='years_any' and (type(v) is not list or not 1<=len(v)<=15 or any(type(x) is not int or not 1880<=x<=2100 for x in v)):raise ValueError('Years')
        if key in ('excluded_year_ranges','year_ranges_any') and (type(v) is not list or len(v)>15 or any(type(pair) is not list or len(pair)!=2 or any(type(y) is not int or not 1880<=y<=2100 for y in pair) or pair[0]>pair[1] for pair in v)):raise ValueError('Excluded year ranges')
        if key=='runtime_max' and (type(v) is not int or not 1<=v<=600):raise ValueError('Duration')
        if key in ('genre','language') and (type(v) is not str or not 1<=len(v)<=20):raise ValueError('Text')
        if key=='unwatched' and type(v) is not bool:raise ValueError('Boolean')
        if key in ('genres_any','genres_all','excluded_genres') and (type(v) is not list or len(v)>32 or any(type(x) is not str or not 1<=len(x)<=20 for x in v)):raise ValueError('Genres')
    if sum(bool(f.get(k)) for k in ('genre','genres_any','genres_all'))>1:raise ValueError('Use one positive genre mode')
    if f.get('year_from',1880)>f.get('year_to',2100):raise ValueError('Contradictory years')
    return f

def genre_groups(f):
    if '_genre_groups' in f:return f['_genre_groups']
    if f.get('genre'):return [[f['genre']]]
    if f.get('genres_all'):return [[g] for g in f['genres_all']]
    return [f['genres_any']] if f.get('genres_any') else []

def hard_conditions(f):return {k:v for k,v in f.items() if k in FILTER_KEYS}

def merge_for_tool(hard,proposed):
    """Model may narrow a query, never replace or weaken explicit user constraints."""
    proposed=validate_filters(proposed);base=hard_conditions(hard);merged=dict(base)
    for key,v in proposed.items():
        if key in ('genre','genres_any','genres_all','excluded_genres','excluded_year_ranges','year_ranges_any'):continue
        if key=='year_from':merged[key]=max(base.get(key,1880),v)
        elif key=='year_to':merged[key]=min(base.get(key,2100),v)
        elif key=='runtime_max':merged[key]=min(base.get(key,600),v)
        elif key=='unwatched':merged[key]=bool(base.get(key) or v)
        elif key=='language' and base.get(key) and base[key]!=v:raise ValueError('Model filters conflict with user conditions')
        elif key=='years_any':merged[key]=[y for y in v if not base.get(key) or y in base[key]]
        else:merged[key]=v
    if merged.get('year_from',1880)>merged.get('year_to',2100):raise ValueError('Model filters conflict with user conditions')
    merged['_year_groups']=([base['year_ranges_any']] if base.get('year_ranges_any') else [])+([proposed['year_ranges_any']] if proposed.get('year_ranges_any') else [])
    merged['_genre_groups']=genre_groups(base)+genre_groups(proposed)
    merged['excluded_genres']=sorted(set(base.get('excluded_genres',[]))|set(proposed.get('excluded_genres',[])))
    if any(all(g in merged['excluded_genres'] for g in group) for group in merged['_genre_groups']):raise ValueError('Model filters conflict with user conditions')
    merged['excluded_year_ranges']=[list(pair) for pair in sorted(set(tuple(pair) for pair in base.get('excluded_year_ranges',[])+proposed.get('excluded_year_ranges',[])))]
    if 'years_any' in merged:
        merged['years_any']=[y for y in merged['years_any'] if merged.get('year_from',1880)<=y<=merged.get('year_to',2100)]
        if not merged['years_any']:raise ValueError('Model filters conflict with user conditions')
    return merged

def matches(movie,filters,state):
    year=movie.get('release_year');runtime=movie.get('runtime_minutes');genres=movie.get('genres',[])
    if filters.get('year_from') is not None and (year is None or year<filters['year_from']):return False
    if filters.get('year_to') is not None and (year is None or year>filters['year_to']):return False
    if filters.get('years_any') is not None and year not in filters['years_any']:return False
    groups=filters.get('_year_groups',([filters['year_ranges_any']] if filters.get('year_ranges_any') else []))
    if groups and (year is None or any(not any(lo<=year<=hi for lo,hi in group) for group in groups)):return False
    if filters.get('excluded_year_ranges') and (year is None or any(lo<=year<=hi for lo,hi in filters['excluded_year_ranges'])):return False
    if filters.get('runtime_max') is not None and (runtime is None or runtime>filters['runtime_max']):return False
    if filters.get('language') and movie.get('original_language')!=filters['language']:return False
    if any(not any(any(g in label for label in genres) for g in group) for group in genre_groups(filters)):return False
    if any(ex in g for ex in filters.get('excluded_genres',[]) for g in genres):return False
    if state.get('not_interested') or (filters.get('unwatched') and state.get('watched')):return False
    return True
