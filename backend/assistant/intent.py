"""Conservative Chinese conditions. Only explicit supported facts become hard filters."""
import re,unicodedata
from copy import deepcopy
from .conditions import matches,genre_groups
CONDITION_VERSION=4
GENRES=('科幻','剧情','犯罪','喜剧','爱情','恐怖','惊悚','动作','动画','悬疑','冒险','纪录','奇幻','战争','家庭','历史','音乐','传记','西部','运动')
Y=r'(?<!\d)((?:18|19|20)\d{2}|2100)(?!\d)'
YEAR=r'((?:18|19|20)\d{2}|2100)'
NEG=re.compile(r'不是|不希望(?:推荐|看|是)?|不想(?:推荐|看)?|不(?:怎么|太)?喜欢|不爱(?:看)?|不要(?:推荐|看)?|不看|排除|拒绝|别(?:推荐|来|看)?|不接受|避免')
POS=re.compile(r'偏好|只看|只要|来点|来一部|找点|找|喜欢|想看|要看|希望看|推荐|改成|改为|换成|换为|可以|接受|保留|还是|加上|再加|增加|也要')

def _clear_positive(f):
    for k in ('genre','genres_any','genres_all'):f.pop(k,None)
def _positive(f,values,mode='any'):
    _clear_positive(f);values=list(dict.fromkeys(values))
    if len(values)==1:f['genre']=values[0]
    elif values:f['genres_all' if mode=='all' else 'genres_any']=values

def _note(f,text):
    notes=f.setdefault('_notes',[])
    if text not in notes:notes.append(text)

def parse(message,previous=None,*,max_length=500):
    if type(message) is not str or not 1<=len(message.strip())<=max_length:raise ValueError('message')
    text=unicodedata.normalize('NFKC',message.strip());f=deepcopy(previous or {})
    # Narrow offline grammar for excluded elements; BYOK uses structured model intent.
    text=re.sub(r'(?:别|不要|不想)(?:推荐|看)?有('+('|'.join(GENRES))+r')元素的([^，,。；;]+)',lambda m:m[2]+'，排除'+m[1],text)
    f['_condition_source']='LOCAL_RULE';f.pop('_semantic_version',None)
    f['_condition_version']=CONDITION_VERSION;f['_notes']=[];f.setdefault('_soft_preferences',{});f.pop('rotate',None)
    if re.search(r'清除(?:所有)?条件|重新开始|不限条件|重置筛选',text):f={'_condition_version':CONDITION_VERSION,'_notes':[],'_soft_preferences':{}}
    if re.search(r'不限年份|年份不限|取消年份(?:条件|限制)?',text):
        for k in ('year_from','year_to','years_any','excluded_year_ranges','year_ranges_any'):f.pop(k,None)
        f.pop('_needs_clarification',None);f.pop('_local_uncertain',None);f['_soft_preferences'].pop('years',None)
    if re.search(r'(?:取消|撤销|去掉)(?:年份)?(?:\d{4}年?)?上限|不设(?:年份)?上限',text):f.pop('year_to',None)
    if re.search(r'(?:取消|撤销|去掉)(?:年份)?(?:\d{4}年?)?下限|不设(?:年份)?下限',text):f.pop('year_from',None)
    if re.search(r'不限时长|时长不限|取消时长(?:限制)?',text):f.pop('runtime_max',None);f['_soft_preferences'].pop('runtime',None)
    if re.search(r'不限语言|语言不限',text):f.pop('language',None)
    if re.search(r'不限类型|类型不限|类型随意|取消类型(?:限制)?',text):
        _clear_positive(f);f['excluded_genres']=[]
        for g in GENRES:f['_soft_preferences'].pop(g,None)
    if re.search(r'看过的也可以|包括已看|取消(?:未看|没看过|已看)限制|不限是否看过',text):f.pop('unwatched',None)
    # Split scope before classifying each genre; a negative cue applies to its list,
    # but cannot turn a following positive clause into a negative or vice versa.
    events={};positive_order=[];additive=bool(re.search(r'加上|再加|增加|也要|不仅',text));mode='all' if re.search(r'同时|兼有|都(?:要|有|包含)|两种.*都有',text) else 'any'
    genre_text=re.sub(r'《[^》]*》|“([^”]*)”',lambda m:(m.group(1) if m.lastindex and m.group(1) in GENRES else ''),text)
    for clause in re.split(r'[，,。；;!?！？]|但是|不过|但|而是',genre_text):
        negative=list(NEG.finditer(clause));positive=[m for m in POS.finditer(clause) if not any(n.start()<=m.start()<n.end() for n in negative)]
        for found in re.finditer('|'.join(GENRES),clause):
            g=found.group();before=clause[:found.start()];after=clause[found.end():]
            if re.search(r'不排斥|不拒绝',before) or re.search(r'(?:取消|撤销|解除|不再).*(?:排除|不看|不喜欢)',clause) or re.match(r'(?:片|电影)?也(?:可以|行|能接受)',after):events[g]='alternative' if re.match(r'(?:片|电影)?也(?:可以|行|能接受)',after) else 'allow';continue
            if re.search(r'不要只(?:限|看)|不只看|不限于|(?:取消|撤销).*',before) and ('限制' in after or '不限于' in before or '只' in before):events[g]='remove';continue
            if re.search(r'(?:不要|别|不想|避免).{0,4}(?:太|过于|特别|那么)$',before) or re.search(r'不(?:太|怎么)(?:喜欢)?$',before):
                events[g]='soft';f['_soft_preferences'][g]=clause.strip()[:80];_note(f,'“'+clause.strip()[:80]+'”是程度/软偏好，目录没有强度字段，不作为正向类型或硬排除');continue
            cues=[(m.start(),'negative') for m in negative if m.end()<=found.start()]+[(m.start(),'positive') for m in positive if m.end()<=found.start()]
            if not cues and re.search(r'比较|区别|是什么意思|介绍|讨论|看过|曾经|听说|提到|片名|名字',clause):
                _note(f,'类型出现在讨论中，未自动作为选片硬条件');continue
            suffix_negative=bool(re.match(r'(?:片|电影)?(?:我)?(?:不想看|不喜欢|不看|不要)',after) or re.search(r'都(?:不要|不想看|不喜欢|不看)',clause))
            if not cues and not suffix_negative:
                bare=re.sub('|'.join(GENRES),'',clause)
                bare=re.sub(r'电影|影片|类型|片|的|或者|以及|同时|包含|或|和|与|、|都要|都不要|都不看|[\s“”]','',bare)
                if bare and not re.search(r'有什么|有哪些|推荐|好看',after) and not re.search(r'\d{4}.*(?:年|到|至|[-~～—])',clause):
                    f['_soft_preferences'][g]=clause.strip()[:80];f['_uncertain_genre']=True;_note(f,'类型提及未形成明确筛选指令，请确认：'+clause.strip()[:80]);continue
            polarity=max(cues)[1] if cues else 'positive'
            if re.search(r'非\s*$',before):polarity='negative'
            if re.match(r'(?:片|电影)?(?:我)?(?:不想看|不喜欢|不看|不要)',after) or re.search(r'都(?:不要|不想看|不喜欢|不看)',clause):polarity='negative'
            events[g]=polarity
            if polarity=='positive':positive_order.append(g)
    if any(e in ('positive','negative','allow','remove','alternative') for e in events.values()) or re.search(r'不限类型|类型不限|取消类型',text):f.pop('_uncertain_genre',None)
    excluded=set(f.get('excluded_genres',[]));old_groups=genre_groups(f);old_positive=list(dict.fromkeys(g for group in old_groups for g in group))
    for g,event in events.items():
        if event=='negative':excluded.add(g);old_positive=[x for x in old_positive if x!=g]
        elif event in ('allow','alternative'):excluded.discard(g)
        elif event=='soft' and re.search(r'改成|其实|而是|就行',text):excluded.discard(g)
        elif event=='remove':old_positive=[x for x in old_positive if x!=g]
    chosen=list(dict.fromkeys(g for g in positive_order if events[g]=='positive'))
    alternatives=[g for g,e in events.items() if e=='alternative']
    if chosen or (alternatives and old_positive):
        if not chosen:chosen=old_positive
        chosen=list(dict.fromkeys(chosen+alternatives))
        for g in chosen:excluded.discard(g)
        _positive(f,(old_positive if additive else [])+chosen,mode)
    elif any(e in ('negative','remove') for e in events.values()):_positive(f,old_positive,'all' if f.get('genres_all') else 'any')
    f['excluded_genres']=sorted(excluded)
    # Explicit ranges are inclusive. Individual before/after bounds are scoped to
    # their own number, never to an unrelated word elsewhere in the utterance.
    year_text=text
    corrections=list(re.finditer(r'改成|改为|换成|换为|而是',text))
    if corrections and re.search(YEAR,text[corrections[-1].end():]):year_text=text[corrections[-1].end():]
    year_text=re.sub(r'《[^》]*》|“[^”]*”|"[^"]*"','',year_text)
    ranges=list(re.finditer(YEAR+r'\s*年?\s*(?:到|至|[-~～—–])\s*'+YEAR+r'\s*年?',year_text))
    bounds={};ambiguous=False
    if ranges:
        positive_ranges=[]
        for match in ranges:
            lo,hi=map(int,match.groups());prefix=re.split(r'[，,。；;]',year_text[:match.start()])[-1]
            if lo>hi:
                ambiguous=True;_note(f,'年份区间起点大于终点，请确认，未擅自交换或沿用旧区间');continue
            if re.search(r'(?:撤销|取消|解除)',prefix) and '排除' in year_text:
                f['excluded_year_ranges']=[pair for pair in f.get('excluded_year_ranges',[]) if pair!=[lo,hi]]
            elif re.search(r'(?:不要|不想看|不喜欢|不看|别看|排除|避开)(?:推荐)?\s*$',prefix):
                pair=[lo,hi]
                if pair not in f.setdefault('excluded_year_ranges',[]):f['excluded_year_ranges'].append(pair)
            else:positive_ranges.append([lo,hi])
        if len(positive_ranges)==1:bounds={'year_from':positive_ranges[0][0],'year_to':positive_ranges[0][1]}
        elif positive_ranges:
            if re.search(r'同时满足|还要满足',year_text):bounds={'year_from':max(pair[0] for pair in positive_ranges),'year_to':min(pair[1] for pair in positive_ranges)}
            else:bounds={'year_ranges_any':positive_ranges}
    else:
        for match in re.finditer(Y,year_text):
            year=int(match.group(1));before=year_text[:match.start()];after=year_text[match.end():]
            if re.match(r'年?\s*(?:前后|左右)',after):
                _note(f,'近似年份“'+match.group(1)+'年前后/左右”交由模型理解，不制造精确年份硬界限');f['_soft_preferences']['years']=match.group(1)+'年前后/左右';ambiguous=True;continue
            if re.search(r'(?:不早于|至少|最早|从|自|>=)\s*$',before[-8:]):bounds['year_from']=year
            elif re.search(r'(?:不晚于|截至|最晚|<=)\s*$',before[-8:]):bounds['year_to']=year
            elif re.search(r'早于$',before):bounds['year_to']=year-1
            elif re.search(r'晚于$',before):bounds['year_from']=year+1
            elif re.match(r'年?\s*(?:及以前|及之前|以下|以前及)',after):bounds['year_to']=year
            elif re.match(r'年?\s*(?:及以后|及之后|以上|起)',after):bounds['year_from']=year
            elif re.match(r'年?\s*(?:之前|以前|前)',after):bounds['year_from' if re.search(r'(?:不要|排除|不看)\s*$',before) else 'year_to']=year if re.search(r'(?:不要|排除|不看)\s*$',before) else year-1
            elif re.match(r'年?\s*(?:之后|以后|后)',after):bounds['year_to' if re.search(r'(?:不要|排除|不看)\s*$',before) else 'year_from']=year if re.search(r'(?:不要|排除|不看)\s*$',before) else year+1
            elif re.match(r'年(?:的)?(?:(?:'+('|'.join(GENRES))+r')\s*)?(?:电影|影片|作品|片)',after) or re.search(r'年份(?:是|为)?$',before):
                if re.search(r'(?:不要|排除|不看|不喜欢|不想看)\s*$',before):f.setdefault('excluded_year_ranges',[]).append([year,year])
                else:bounds={'year_from':year,'year_to':year}
    upper=re.search(r'上限(?:改成|改为|是|为)?\s*'+YEAR,text);lower=re.search(r'下限(?:改成|改为|是|为)?\s*'+YEAR,text)
    if upper:bounds={'year_to':int(upper[1])}
    if lower:bounds={'year_from':int(lower[1])}
    if not ranges and not ambiguous:
        listed=re.findall(Y,year_text)
        if len(listed)>1 and re.search(r'或|或者|、|和',year_text) and not re.search(r'之前|以前|之后|以后|早于|晚于',year_text):bounds={'years_any':list(dict.fromkeys(map(int,listed)))}
    if bounds or ambiguous:
        if not (upper or lower or re.search(r'再加|同时满足|进一步限制|(?:年份)?上限|(?:年份)?下限',year_text)):
            for key in ('year_from','year_to','years_any','year_ranges_any'):f.pop(key,None)
        f.update(bounds)
        if bounds and not ambiguous:f.pop('_needs_clarification',None);f.pop('_local_uncertain',None)
        if ambiguous and not bounds:
            if ranges:f['_needs_clarification']=True
            else:f.pop('_needs_clarification',None);f['_local_uncertain']=True
    if f.get('year_from',1880)>f.get('year_to',2100):
        f['_needs_clarification']=True;_note(f,'年份条件存在矛盾，请确认；没有静默丢弃其中一个界限')
    duration=re.search(r'(不超过|最多|少于|短于)\s*(\d{1,3})\s*分钟',text)
    if duration:f['runtime_max']=int(duration[2])-(duration[1] in ('少于','短于'))
    elif '别太长' in text or '短一点' in text:f['_soft_preferences']['runtime']='时长别太长/短一点';_note(f,'“别太长/短一点”没有明确分钟数，作为软偏好交由模型理解')
    for word,language in [('英语','en'),('中文','zh'),('日语','ja'),('韩语','ko'),('法语','fr')]:
        if word in text:f['language']=language
    if '没看过' in text or '未看' in text or re.search(r'(?:不要|排除|不看)\s*(?:我)?(?:已看过|已看|看过)',text):f['unwatched']=True
    intent='search'
    if '推荐' not in text and ('清单' in text or '打过分' in text or ('我的' in text and any(k in text for k in ('评分','收藏','想看','已看','不感兴趣')))):
        intent='behavior';f['behavior']='rating' if '打过分' in text or '评分' in text else 'not_interested' if '不感兴趣' in text else 'favorite' if '收藏' in text else 'watched' if '已看' in text else 'want'
    elif '推荐' in text:
        intent='recommend';f.pop('behavior',None)
        # Explicit offline source scope only; model intent remains authoritative in BYOK.
        if re.search(r'(?:从|在)(?:我的)?想看清单(?:中|里)?',text) and not re.search(r'(?:不要|别|排除|不从).*想看清单',text):f['behavior']='want'
    if '换一批' in text:f['rotate']=True
    last_year=max((m.start() for m in re.finditer(Y,text)),default=-1)
    for pattern,keys in [(r'不限年份|年份不限|取消年份(?:限制|条件)?',('year_from','year_to','years_any','year_ranges_any','excluded_year_ranges')),(r'(?:取消|撤销|去掉)(?:年份)?上限',('year_to',)),(r'(?:取消|撤销|去掉)(?:年份)?下限',('year_from',))]:
        retract=list(re.finditer(pattern,text))
        if retract and retract[-1].start()>last_year:
            for key in keys:f.pop(key,None)
            if len(keys)>1:f.pop('_needs_clarification',None);f.pop('_local_uncertain',None)
    last_genre=max((m.start() for m in re.finditer('|'.join(GENRES),text)),default=-1)
    retract=list(re.finditer(r'不限类型|类型不限|取消类型限制',text))
    if retract and retract[-1].start()>last_genre:_clear_positive(f);f['excluded_genres']=[]
    legacy=f.get('_legacy_hints',{})
    if events or re.search(r'类型不限|不限类型|取消类型',text):
        for key in ('genre','genres_any','genres_all','excluded_genres'):legacy.pop(key,None)
    if bounds or ambiguous or re.search(r'年份不限|不限年份|取消年份',text):
        for key in ('year_from','year_to','years_any','year_ranges_any','excluded_year_ranges'):legacy.pop(key,None)
    if duration or re.search(r'时长|分钟',text):legacy.pop('runtime_max',None)
    if re.search(r'英语|中文|日语|韩语|法语|语言',text):legacy.pop('language',None)
    if events or bounds or duration:f.pop('_model_hints',None)
    return intent,f
