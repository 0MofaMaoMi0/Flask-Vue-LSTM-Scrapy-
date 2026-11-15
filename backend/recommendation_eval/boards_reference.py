"""Frozen notes on catalog.recommendation_boards behaviour (2026-11-15).

This is a reference record, not a reimplementation and not a claim that
Baseline V1 covers these channels. The numbers below were read from the
current boards source before any M10 integration change.
"""

BOARDS_VERSION = "boards-v1"
SOURCE_PATH = "backend/catalog/recommendation_boards.py"
SOURCE_SHA256 = "cef317723d14eb30d7782e49f7197ee04626c2ff9736815aded422ff34701c34"
AUDITED_ON = "2026-11-15"
SCOPE_NOTE = (
    "记录 board() 的候选、参数和排序口径；不含 HTTP、TMDB 在线刷新、缓存文件格式、"
    "前端展示或生产数据。Baseline V1 只对应其中四个个人频道调用的 recommend()。"
)

SHARED = {
    "candidate_scope": "active 且 is_adult is not True；scope=local 仅保留库内影片",
    "hard_filters": "assistant.conditions.matches（年份/年代区间/时长/语言/类型/排除类型/unwatched）",
    "personal_exclusion": "daily/weekly/because/personal 额外排除 watched；not_interested 由 matches 排除",
    "keyword": "title + original_title 的大小写无关子串，长度<=100",
    "min_votes": "非负整数；top_rated 默认阈值 100，显式传入时取 max(1,min_votes)",
    "limit": "1..50，输出按 rank 截断",
    "weights": "默认 favorite=3, want=2, rating=1；各值 0..10，键集合固定",
    "catalog_total": "active 且非成人影片总数（不是过滤后的候选数）",
    "candidate_count": "对 personal/daily/weekly/watchlist 为过滤后 eligible 数量；其他频道为 ranked 数量",
    "paid_model_tokens": "固定 0",
}

CHANNELS = {
    "popular": {
        "title": "热门推荐",
        "source": "TMDB 官方 Popular 榜单快照，GET 不触发外网刷新",
        "ranking": "保留快照 items 顺序；source_rank 原样记录",
        "freshness": "captured_at 超过 21600 秒标记 STALE，不替换数据",
        "scope": "默认 TMDB_BOARD_LOCAL_AND_EXTERNAL；scope=local 仅保留库内",
        "notes": "无快照返回 UNAVAILABLE，不以其他频道代替",
    },
    "trending_day": {
        "title": "今日热榜",
        "source": "TMDB 今日趋势快照",
        "ranking": "保留官方来源名次",
        "freshness": "与 popular 相同的 6 小时阈值",
        "scope": "同 popular",
    },
    "trending_week": {
        "title": "本周热榜",
        "source": "TMDB 本周趋势快照",
        "ranking": "保留官方来源名次",
        "freshness": "与 popular 相同的 6 小时阈值",
        "scope": "同 popular",
    },
    "personal": {
        "title": "为你推荐",
        "source": "本地数据库 + 已验证 TMDB 特征",
        "ranking": "推荐候选先经 filters/watched 过滤，再调用 recommend(limit=50, baseline_field='catalog_popularity')",
        "cold_start": "无 favorite/want/rating>=4 时追加偏好不足提示，仍使用热度+多样性",
        "notes": "本频道直接使用 Baseline V1 的纯 recommend()，随后按 limit 截断",
    },
    "daily": {
        "title": "每日为你推荐",
        "source": "本地数据库 + 已验证 TMDB 特征",
        "ranking": "recommend 后叠加 0.2*(n*rating+100*6.5)/(n+100)/10，再按日期种子加 0.06*variety 并做类型惩罚",
        "period": "Asia/Shanghai 当日日期",
        "cache": "persisted_order 仅缓存 ID，按 version/user/channel/period/filters 哈希；返回时重新校验可见性和负反馈",
        "notes": "不是单纯 recommend()；日期扰动、贝叶斯项和缓存不包含在 Baseline V1",
    },
    "weekly": {
        "title": "每周精选",
        "source": "本地数据库 + 已验证 TMDB 特征",
        "ranking": "recommend 后叠加贝叶斯项、周种子 0.22 扰动、类型 0.08 与年代 0.15 惩罚",
        "period": "Asia/Shanghai 所在周的周一日期",
        "cache": "同 daily，按周缓存 ID",
        "notes": "不是单纯 recommend()；年代探索不包含在 Baseline V1",
    },
    "top_rated": {
        "title": "高分口碑",
        "source": "本地库内 TMDB 评分与票数",
        "ranking": "仅保留 tmdb_rating 非空且 vote_count>=threshold；分数=(n*rating+100*mean)/(n+100)，按分数、票数、movie_id 排序",
        "threshold": "默认 100；显式 min_votes 时 max(1,min_votes)",
        "notes": "Baseline V1 不含此贝叶斯频道排序",
    },
    "revenue": {
        "title": "票房精选",
        "source": "本地库内有效累计收入",
        "ranking": "revenue 非空且 >0，按 revenue 降序、movie_id 升序",
        "notes": "是累计收入，不是日/周票房；Baseline V1 不含收入排序",
    },
    "because": {
        "title": "因为你喜欢……",
        "source": "本地库内收藏或评分>=4 的种子",
        "ranking": "对每个候选取最佳种子：同导演 3 分、同演员 2 分、类型 Jaccard；再按分值、catalog_popularity、movie_id 排序",
        "notes": "种子自身排除；Baseline V1 不含导演/演员逻辑",
    },
    "watchlist": {
        "title": "想看清单精选",
        "source": "本地库内用户 want 且未 watched 的影片",
        "ranking": "先限定想看集合，再调用 recommend(limit=50)，最后按 limit 截断",
        "notes": "使用 Baseline V1 的纯函数，但候选集合由频道额外限定",
    },
}

NOT_COVERED_BY_BASELINE = (
    "public / trending_day / trending_week 的官方快照读取与新鲜度判断",
    "daily / weekly 的 TMDB 贝叶斯融合、日期/周种子扰动和 ID 缓存",
    "top_rated 的贝叶斯平滑公式",
    "revenue 的收入排序",
    "because 的导演/演员/类型种子逻辑",
    "board() 的 HTTP 上下文、TMDB 连接状态与错误降级",
)

CHANNEL_NAMES = tuple(CHANNELS)


def describe(channel):
    """Return a copy of the recorded channel dictionary."""
    try:
        return dict(CHANNELS[channel])
    except KeyError:
        raise KeyError("unknown channel: %s" % channel) from None


__all__ = [
    "BOARDS_VERSION",
    "SOURCE_PATH",
    "SOURCE_SHA256",
    "AUDITED_ON",
    "SCOPE_NOTE",
    "SHARED",
    "CHANNELS",
    "CHANNEL_NAMES",
    "NOT_COVERED_BY_BASELINE",
    "describe",
]