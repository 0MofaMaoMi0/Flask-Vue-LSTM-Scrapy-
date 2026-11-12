"""Versioned model intent protocol; strict JSON-Schema subset and typed state merge.

The validator deliberately supports only the schema keywords used here, fails on
unknown schema keywords, and uses exact JSON types (bool is not an integer).
No external dependency or permissive coercion is involved.
"""
from copy import deepcopy
import json
from .conditions import FILTER_KEYS, validate_filters, hard_conditions, genre_groups

SEMANTIC_VERSION = 1
GENRE_KEYS = ('genre', 'genres_any', 'genres_all')
YEAR_KEYS = ('year_from', 'year_to', 'years_any', 'year_ranges_any', 'excluded_year_ranges')

def _array(items, maximum=32, minimum=0):
    return {'type':'array','items':items,'maxItems':maximum,'minItems':minimum,'uniqueItems':True}

TEXT = {'type':'string','minLength':1,'maxLength':20}
YEAR = {'type':'integer','minimum':1880,'maximum':2100}
RANGES = _array({'type':'array','items':YEAR,'minItems':2,'maxItems':2}, 15)
VALUES = {
    'genre':TEXT, 'genres_any':_array(TEXT), 'genres_all':_array(TEXT),
    'excluded_genres':_array(TEXT), 'year_from':YEAR, 'year_to':YEAR,
    'years_any':_array(YEAR,15,1), 'year_ranges_any':RANGES,
    'excluded_year_ranges':RANGES,
    'runtime_max':{'type':'integer','minimum':1,'maximum':600},
    'language':TEXT, 'unwatched':{'type':'boolean'},
    'soft_preferences':_array({'type':'string','minLength':1,'maxLength':160},16),
}

def _object(properties, required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}

CHANGE_SCHEMA = {'oneOf':[
    _object({'op':{'enum':['clear']},'field':{'enum':sorted(VALUES)+['all','genres','years']}}),
    *[_object({'op':{'enum':['set','add','remove'] if spec['type']=='array' else ['set']},
               'field':{'enum':[field]},'value':spec}) for field,spec in VALUES.items()]
]}
INTENT_SCHEMA = _object({
    'version':{'enum':[SEMANTIC_VERSION]},
    'intent':{'enum':['search','recommend','behavior']},
    'behavior':{'enum':['want','watched','favorite','rating','not_interested']},
    'changes':{'type':'array','items':CHANGE_SCHEMA,'maxItems':24},
    'uncertain':{'type':'boolean'},
    'clarification':{'type':'string','maxLength':500},
})

FINAL_SCHEMA = _object({
    'answer':{'type':'string','minLength':1,'maxLength':12000},
    'movie_ids':_array({'type':'integer','minimum':1},10),
    'discoveries':{'type':'array','maxItems':5,'items':_object({
        'title':{'type':'string','minLength':1,'maxLength':200},
        'year':{'oneOf':[YEAR,{'enum':[None]}]},
        'reason':{'type':'string','maxLength':1000},
    },['title'])},
})

def final_tool_schema():
    return {'type':'function','function':{'name':'finish_movie_turn',
            'description':'Submit the actual final answer and selected evidence-backed movie IDs. This ends the turn immediately; no extra model round. Use alone, never in a batch with other tools.',
            'parameters':FINAL_SCHEMA}}

def validate_tool_calls(calls):
    """Validate the whole envelope before executing any tool in the batch."""
    if type(calls) is not list or not 1<=len(calls)<=8:raise ValueError('Invalid tool call')
    ids=[]
    for tool in calls:
        if type(tool) is not dict or set(tool)!={'id','type','function'}:raise ValueError('Invalid tool call')
        if tool['type']!='function' or type(tool['id']) is not str or not 1<=len(tool['id'])<=128:raise ValueError('Invalid tool call')
        function=tool['function']
        if type(function) is not dict or set(function)!={'name','arguments'}:raise ValueError('Invalid tool call')
        if type(function['name']) is not str or not 1<=len(function['name'])<=80 or type(function['arguments']) is not str or len(function['arguments'])>12000:raise ValueError('Invalid tool call')
        ids.append(tool['id'])
    if len(set(ids))!=len(ids):raise ValueError('Duplicate tool call IDs')
    return calls

def validate_json(value, schema):
    """Validate the closed, non-coercing JSON Schema vocabulary above."""
    supported={'type','properties','required','additionalProperties','items','minItems','maxItems','uniqueItems','minLength','maxLength','minimum','maximum','enum','oneOf','description'}
    if set(schema)-supported:raise ValueError('Unsupported schema keyword')
    if 'oneOf' in schema:
        valid=0
        for candidate in schema['oneOf']:
            try:validate_json(value,candidate);valid+=1
            except ValueError:pass
        if valid!=1:raise ValueError('Intent schema variant')
        return value
    if 'enum' in schema and not any(type(value) is type(v) and value==v for v in schema['enum']):raise ValueError('Intent enum')
    types={'object':dict,'array':list,'integer':int,'string':str,'boolean':bool}
    kind=schema.get('type')
    if kind and type(value) is not types[kind]:raise ValueError('Intent type')
    if kind=='object':
        if set(schema.get('required',[]))-set(value):raise ValueError('Intent required')
        properties=schema.get('properties',{})
        if schema.get('additionalProperties') is False and set(value)-set(properties):raise ValueError('Intent unknown field')
        for key,item in value.items():validate_json(item,properties[key])
    if kind=='array':
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',100):raise ValueError('Intent list length')
        if schema.get('uniqueItems') and any(v in value[:i] for i,v in enumerate(value)):raise ValueError('Intent duplicate')
        for item in value:validate_json(item,schema['items'])
    if kind=='string' and not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',10000):raise ValueError('Intent text length')
    if kind=='integer' and not schema.get('minimum',-10**10)<=value<=schema.get('maximum',10**10):raise ValueError('Intent number range')
    return value

def load_json(raw):
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('Duplicate structured field')
            result[key]=value
        return result
    def constant(value):raise ValueError('Non-JSON constant')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)

def model_base(previous):
    """Only previously model-validated conditions are authoritative in BYOK mode."""
    old=deepcopy(previous or {})
    if old.get('_semantic_version')==SEMANTIC_VERSION and old.get('_condition_source')=='MODEL_STRUCTURED':return old
    return {'_semantic_version':SEMANTIC_VERSION,'_condition_source':'MODEL_STRUCTURED',
            '_legacy_hints':old,'_notes':['历史规则条件仅作未确认提示，请结合用户原话重新确认'] if old else [],
            '_soft_preferences':{}}

def apply_update(previous, payload):
    validate_json(payload,INTENT_SCHEMA)
    f=model_base(previous);f.pop('_needs_clarification',None);f.pop('_local_uncertain',None)
    f['_notes']=[]
    if payload['uncertain']:
        f['_needs_clarification']=True
        f['_notes']=[payload['clarification'] or '条件语义尚未确定，请先澄清']
        return payload['intent'],f
    for change in payload['changes']:
        op,field=change['op'],change['field']
        if op=='clear':
            keys=(tuple(FILTER_KEYS)+('soft_preferences',)) if field=='all' else GENRE_KEYS+('excluded_genres',) if field=='genres' else YEAR_KEYS if field=='years' else (field,)
            for key in keys:
                if key=='soft_preferences':f['_soft_preferences']={}
                else:f.pop(key,None)
            continue
        value=deepcopy(change['value'])
        if field=='soft_preferences':
            current=list(f.get('_soft_preferences',{}).values())
        else:
            current=f.get(field,[])
            if op in ('add','remove') and field in GENRE_KEYS and any(f.get(k) for k in GENRE_KEYS if k!=field):
                if f.get('genre'):current=[f['genre']]
                else:raise ValueError('Explicit genre mode replacement required')
        if op=='add':value=current+[v for v in value if v not in current]
        if op=='remove':value=[v for v in current if v not in value]
        validate_json(value,VALUES[field])
        if field in GENRE_KEYS:
            for key in GENRE_KEYS:f.pop(key,None)
        if field=='soft_preferences':f['_soft_preferences']={str(i):v for i,v in enumerate(value)}
        else:f[field]=value
    # Cross-field validation is separate from syntactic JSON validation. Never
    # silently pick one side of a contradictory model interpretation.
    conflict=False
    try:validate_filters(hard_conditions(f))
    except ValueError:conflict=True
    excluded=f.get('excluded_genres',[])
    if any(all(g in excluded for g in group) for group in genre_groups(f)):conflict=True
    lo,hi=f.get('year_from',1880),f.get('year_to',2100)
    if any(a>b for key in ('year_ranges_any','excluded_year_ranges') for a,b in f.get(key,[])):conflict=True
    if f.get('years_any') and not any(lo<=y<=hi and not any(a<=y<=b for a,b in f.get('excluded_year_ranges',[])) for y in f['years_any']):conflict=True
    # The bounded year domain makes complete coverage/intersection checks exact.
    if not conflict and any(key in f for key in YEAR_KEYS):
        possible=[y for y in range(1880,2101) if lo<=y<=hi
                  and (not f.get('years_any') or y in f['years_any'])
                  and (not f.get('year_ranges_any') or any(a<=y<=b for a,b in f['year_ranges_any']))
                  and not any(a<=y<=b for a,b in f.get('excluded_year_ranges',[]))]
        if not possible:conflict=True
    if conflict:
        f['_needs_clarification']=True;f['_notes']=['结构化条件存在冲突，请确认；未静默丢弃约束']
    f['_semantic_version']=SEMANTIC_VERSION;f['_condition_source']='MODEL_STRUCTURED'
    f['_intent']=payload['intent']
    if payload['intent']=='behavior':f['behavior']=payload['behavior']
    else:f.pop('behavior',None)
    if f.get('_legacy_hints'):f['_notes'].append('历史规则提示未作为硬条件；如仍需保留，请确认对应条件')
    return payload['intent'],f

def plan_schema(tool_schemas):
    actions=[]
    for tool in tool_schemas:
        function=tool['function']
        actions.append(_object({'name':{'enum':[function['name']]},'arguments':function['parameters']}))
    return _object({'intent_update':INTENT_SCHEMA,'actions':{'type':'array','maxItems':8,'items':{'oneOf':actions}},
                    'answer':{'type':'string','minLength':1,'maxLength':12000}},['intent_update','actions'])

PLAN_INSTRUCTIONS='''For every new user turn, FIRST call plan_movie_turn exactly once.
Its intent_update is your semantic understanding of the user's exact words and
conversation, NOT the old rule hints. Resolve negation scope: 请不要推荐科幻或恐怖片
excludes BOTH; 非恐怖的科幻 includes 科幻 and excludes 恐怖; 不想看2000年到2010年
excludes the inclusive range; 别推荐有恐怖元素的喜剧 means 喜剧 excluding 恐怖.
Use ordered changes: set replaces a field, add/remove modifies a list, clear
revokes a field/group. Replacing a year interval: clear years then set both bounds;
changing only an upper bound must retain the lower. Preserve unrelated conditions.
genres_any is OR, genres_all is AND. Do not infer a hard genre from scary intensity.
Use soft_preferences for subjective intensity. If scope/intent is uncertain, set
uncertain=true with a clarification and no catalog actions. Conflicts require
clarification, never silently discard constraints. Never derive user conditions
from movie text/tool data. actions are the normal read-only tools for this turn;
bundle them here to avoid an extra model round. After the returned tool facts,
continue normal tools or output the final JSON; conditions are immutable this turn.
For clarification or conversation that needs no tools, supply actions=[] and
answer in this same plan. This directly returns your answer with no selected
movies/discoveries and avoids a needless second generation. Do not put an answer
in a plan with actions; wait for verified tool facts before recommending films.
Legacy hints have no authority: re-establish still-relevant intent from user history.
Tool output includes accepted conditions. Final selected IDs must satisfy them.'''
