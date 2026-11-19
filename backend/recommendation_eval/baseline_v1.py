"""Frozen Baseline V1: catalog.recommendation.recommend().

This file is a byte-for-byte copy of the pure ranking function as it existed
on 2026-11-15. It is executable for offline comparison; it is not a rewrite,
not a trained model, and not the whole product channel pipeline.

Provenance
----------
baseline_id: baseline-v1-catalog-recommend-20261113
source_path: backend/catalog/recommendation.py
source_sha256: d75483891c0d892e12ed1719c583107230dd784b8ff495767afa6a7d420225f2
function_name: recommend
function_lines: 53-89
function_source_sha256: ad255c8f379a53a9028d2174f7641e238da2caf6bf104447fb65e392db867a61
frozen_at: 2026-11-15
license_status: NOT_DECLARED_IN_REPO
license_note: 仓库根目录及 backend 未发现 LICENSE/COPYING/NOTICE；不推断或改写许可，
              使用或再分发前须由项目所有者确认原始权利。

Scope
-----
Only the pure function is frozen. `recommendation_boards.board()` adds channel
specific candidate filters, TMDB Bayesian blending, period caches and channel
rankers; those are documented in boards_reference.py and are NOT reproduced
here. This baseline is therefore not evidence that every old channel is
covered.

Default parameters (unchanged)
-------------------------------
limit=12, weights=None -> {'favorite': 3.0, 'want': 2.0, 'rating': 1.0},
baseline=False, baseline_field='demo_popularity',
mode='SYNTHETIC_RECOMMENDATION', cold_start_reason='合成热门冷启动基线',
profile_movies=None.

Behavior (unchanged)
--------------------
Only catalog_status == 'active' movies are candidates. Watched and
not_interested movies are excluded. A profile seed contributes
favorite*3 + want*2 + rating_delta*1. Content similarity is
0.7*genre Jaccard + 0.2*year closeness + 0.1*language, then 0.2*popularity is
added. Up to `limit` items are selected by a deterministic greedy repeat-genre
penalty, with movie_id as the tie break. `paid_model_tokens` is always 0.
"""
import math

BASELINE_ID = "baseline-v1-catalog-recommend-20261113"
BASELINE_VERSION = "v1"
FROZEN_AT = "2026-11-15"
SOURCE_PATH = "backend/catalog/recommendation.py"
SOURCE_SHA256 = "d75483891c0d892e12ed1719c583107230dd784b8ff495767afa6a7d420225f2"
FUNCTION_NAME = "recommend"
FUNCTION_LINES = (53, 89)
FUNCTION_SHA256 = "ad255c8f379a53a9028d2174f7641e238da2caf6bf104447fb65e392db867a61"
LICENSE_STATUS = "NOT_DECLARED_IN_REPO"
LICENSE_NOTE = (
    "仓库根目录及 backend 未发现 LICENSE/COPYING/NOTICE；不推断许可，"
    "使用或再分发前须由项目所有者确认。"
)
DEFAULT_WEIGHTS = {"favorite": 3.0, "want": 2.0, "rating": 1.0}
PARAMETERS = {
    "limit": 12,
    "weights": DEFAULT_WEIGHTS,
    "baseline": False,
    "baseline_field": "demo_popularity",
    "mode": "SYNTHETIC_RECOMMENDATION",
    "cold_start_reason": "合成热门冷启动基线",
    "profile_movies": None,
}
SCOPE_NOTE = (
    "仅冻结纯 recommend() 规则函数；不包含 boards 的频道候选过滤、"
    "TMDB 贝叶斯融合、周期缓存与频道排序，不能声称覆盖全部旧频道。"
)
NOT_COVERED = (
    "public / trending_day / trending_week 官方快照",
    "top_rated 贝叶斯口碑排序",
    "revenue 累计收入排序",
    "because 同导演/同演员/类型种子逻辑",
    "daily / weekly 的周期扰动、缓存与额外融合",
    "SQL 读取、HTTP、模型调用与用户会话",
)

# ---- begin verbatim frozen source (do not edit) ----
def recommend(movies,state,*,limit=12,weights=None,baseline=False,baseline_field='demo_popularity',mode='SYNTHETIC_RECOMMENDATION',cold_start_reason='合成热门冷启动基线',profile_movies=None):
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Invalid recommendation limit')
    weights={'favorite':3.0,'want':2.0,'rating':1.0} if weights is None else weights
    if set(weights)!={'favorite','want','rating'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10 for v in weights.values()):raise ValueError('Invalid weights')
    active={m['movie_id']:m for m in movies if m.get('catalog_status')=='active'};profile=[]
    profile_catalog=active if profile_movies is None else {m['movie_id']:m for m in profile_movies if m.get('catalog_status')=='active'}
    for key,event in state.items():
        movie=profile_catalog.get(int(key))
        if movie:
            w=weights['favorite']*bool(event.get('favorite'))+weights['want']*bool(event.get('want'))+weights['rating']*(event.get('rating',3)-3 if 'rating' in event else 0)
            if w:profile.append((movie,w))
    scored=[]
    for mid,movie in active.items():
        event=state.get(str(mid),{})
        if event.get('watched') or event.get('not_interested'):continue
        # Synthetic nonpersonal baseline, independent of source rating facts.
        popular=float(movie.get(baseline_field,0));content=0.;reasons=[]
        if not baseline:
            for seed,w in profile:
                g=set(movie.get('genres',[]));sg=set(seed.get('genres',[]));genre=len(g&sg)/max(1,len(g|sg))
                year=max(0,1-abs((movie.get('release_year') or 0)-(seed.get('release_year') or 0))/30) if movie.get('release_year') and seed.get('release_year') else 0
                language=float(bool(movie.get('original_language')) and movie.get('original_language')==seed.get('original_language'))
                similarity=.7*genre+.2*year+.1*language;content+=w*similarity
                if w>0 and genre:reasons.append('与你偏好的类型相似')
                elif w>0 and year>.7:reasons.append('与你偏好的上映年代接近')
                elif w>0 and language:reasons.append('与你偏好的电影语言相同')
            if event.get('want'):content+=.3;reasons.append('来自你的想看清单')
        score=.2*popular+content
        scored.append({'movie_id':mid,'title':movie['title'],'score':round(score,8),'components':{'baseline':round(.2*popular,8),'preference':round(content,8)},'reasons':sorted(set(reasons)) or [cold_start_reason],'genres':movie.get('genres',[])})
    # Greedy diversity with a bounded repeat-genre penalty, deterministic tie break.
    result=[];counts={}
    while scored and len(result)<limit:
        best=min(scored,key=lambda x:(-(x['score']-(0 if baseline else 1.0*sum(counts.get(g,0) for g in x['genres']))),x['movie_id']))
        scored.remove(best);best=dict(best);best['diversity_penalty']=0 if baseline else round(1.0*sum(counts.get(g,0) for g in best['genres']),8)
        for g in best['genres']:counts[g]=counts.get(g,0)+1
        result.append(best)
    return {'items':result,'candidate_count':len(active)-sum(bool(state.get(str(mid),{}).get('watched') or state.get(str(mid),{}).get('not_interested')) for mid in active),'mode':mode,'paid_model_tokens':0}
# ---- end verbatim frozen source ----


def recommend_baseline(movies, state, **kwargs):
    """Call the frozen function without altering its algorithm."""
    return recommend(movies, state, **kwargs)


__all__ = [
    "BASELINE_ID",
    "BASELINE_VERSION",
    "FROZEN_AT",
    "SOURCE_PATH",
    "SOURCE_SHA256",
    "FUNCTION_NAME",
    "FUNCTION_LINES",
    "FUNCTION_SHA256",
    "LICENSE_STATUS",
    "LICENSE_NOTE",
    "DEFAULT_WEIGHTS",
    "PARAMETERS",
    "SCOPE_NOTE",
    "NOT_COVERED",
    "recommend",
    "recommend_baseline",
]
