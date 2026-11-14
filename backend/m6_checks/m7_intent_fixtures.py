"""Explicit synthetic model intent fixtures, never used by production parsing."""
from copy import deepcopy

def update(changes=(),intent='search',uncertain=False,clarification=''):
 return {'version':1,'intent':intent,'behavior':'want','changes':list(changes),'uncertain':uncertain,'clarification':clarification}
def set_(field,value):return {'op':'set','field':field,'value':value}
def clear(field):return {'op':'clear','field':field}
def years(lo,hi):return [clear('years'),set_('year_from',lo),set_('year_to',hi)]
ACTION_FIXTURES={}
FIXTURES={
 '请不要推荐科幻或恐怖片':update([set_('excluded_genres',['科幻','恐怖'])]),
 '我想看非恐怖的科幻电影':update([set_('genre','科幻'),set_('excluded_genres',['恐怖'])]),
 '不想看2000年到2010年的电影':update([set_('excluded_year_ranges',[[2000,2010]])]),
 '别推荐有恐怖元素的喜剧电影':update([set_('genre','喜剧'),set_('excluded_genres',['恐怖'])]),
 '不想看恐怖片':update([set_('excluded_genres',['恐怖'])]),
 '不喜欢恐怖电影':update([set_('excluded_genres',['恐怖'])]),
 '不要太恐怖':update([set_('soft_preferences',['不要太恐怖'])]),
 '1990到2000年或2005到2020年的电影':update([clear('years'),set_('year_ranges_any',[[1990,2000],[2005,2020]])]),
 '1994年或2010年的电影':update([clear('years'),set_('years_any',[1994,2010])]),
 '1990到2000年的电影':update(years(1990,2000)),
 '1990到2020年，不要2000到2010年':update(years(1990,2020)+[set_('excluded_year_ranges',[[2000,2010]])]),
 '改成2005到2020年':update(years(2005,2020)),
 '年份不限':update([clear('years')]),
 '改成2010年之后的科幻片':update([clear('years'),set_('year_from',2011),set_('genre','科幻')]),
 '再改成2000年以前的喜剧':update([clear('years'),set_('year_to',1999),set_('genre','喜剧')]),
}
def fixture(message):
 if message.endswith('现在只看2000年以前的喜剧，不要已看过的'):
  return update([clear('years'),set_('year_to',1999),set_('genre','喜剧'),set_('unwatched',True)])
 return deepcopy(FIXTURES.get(message,update(intent='recommend')))
