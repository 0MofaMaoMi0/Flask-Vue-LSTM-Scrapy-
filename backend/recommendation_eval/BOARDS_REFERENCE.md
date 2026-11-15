# 现有推荐频道行为与参数口径（2026-11-15）

本文件记录 `backend/catalog/recommendation_boards.py` 的 `board()` 口径。它是**参考记录**，不是实现替代品。Baseline V1 只冻结了 `recommend()`；下列频道中的大部分还叠加了别的候选过滤、融合、扰动或排序，不能声称已由 Baseline V1 覆盖。

- boards 版本：`boards-v1`
- 源码 SHA-256：`cef317723d14eb30d7782e49f7197ee04626c2ff9736815aded422ff34701c34`
- 机器可读副本：`boards_reference.py`

## 共同参数

| 项 | 口径 |
|---|---|
| 候选 | `catalog_status=active` 且 `is_adult is not True`；`scope=local` 仅保留库内 |
| 硬过滤 | `assistant.conditions.matches`：年份、年代区间、时长、语言、类型、排除类型、unwatched |
| 个人频道 | `daily/weekly/because/personal` 额外排除 `watched`；`not_interested` 由 matches 排除 |
| 关键词 | `title + original_title` 大小写无关子串，长度≤100 |
| `min_votes` | 非负整数；`top_rated` 默认 100，显式传入取 `max(1,min_votes)` |
| `limit` | 1–50，按 rank 截断 |
| 权重 | 默认 favorite=3、want=2、rating=1；每项 0–10，键集合固定 |
| `catalog_total` | active 非成人总数，不是过滤后候选数 |
| `candidate_count` | personal/daily/weekly/watchlist 为过滤后 eligible 数；其他为 ranked 数 |
| 模型 token | `paid_model_tokens=0` |

## 频道

| 频道 | 候选/来源 | 排序口径 | Baseline V1 是否覆盖 |
|---|---|---|---|
| `popular` | TMDB Popular 官方快照 | 保留快照顺序与 `source_rank`；6 小时新鲜度只标记 STALE | 否 |
| `trending_day` | TMDB 今日趋势快照 | 保留官方名次 | 否 |
| `trending_week` | TMDB 本周趋势快照 | 保留官方名次 | 否 |
| `personal` | 本地库 + 已验证 TMDB 特征 | 过滤后调用 `recommend(limit=50, baseline_field='catalog_popularity')` | 部分是：纯函数，仍缺频道过滤/回包 |
| `daily` | 本地库 + TMDB 特征 | `recommend` 后加 `0.2*(n*rating+100*6.5)/(n+100)/10`，加 0.06 日期种子扰动与类型惩罚 | 否：额外融合/扰动/缓存 |
| `weekly` | 本地库 + TMDB 特征 | `recommend` 后加贝叶斯项、0.22 周种子扰动、类型 0.08 与年代 0.15 惩罚 | 否：额外融合/扰动/缓存 |
| `top_rated` | 本地库评分/票数 | `vote_count≥threshold`；`(n*rating+100*mean)/(n+100)` 排序 | 否 |
| `revenue` | 本地库累计收入 | `revenue>0`，按 revenue 降序、movie_id 升序 | 否 |
| `because` | 收藏或评分≥4 的种子 | 同导演 3、同演员 2、类型 Jaccard；按分值/热度/ID 排序 | 否：导演/演员逻辑不在纯函数 |
| `watchlist` | 用户 want 且未 watched | 先限定想看集合，再调用 `recommend(limit=50)` | 部分是：候选集合另限定 |

## 明确不在 Baseline V1 内

- 公开榜单快照读取、6 小时新鲜度与库内外合并。
- `daily/weekly` 的贝叶斯项、日期/周种子、类型/年代惩罚、ID 缓存与回填校验。
- `top_rated` 贝叶斯平滑、`revenue` 收入排序、`because` 导演/演员种子逻辑。
- `board()` 的 HTTP 上下文、TMDB 连接状态、错误降级与前端展示。
- 真实数据库读取、真实用户状态、真实模型调用。

因此，本目录的 Baseline 指标只能描述“纯 recommend() 在给定候选/状态下的表现”，不能代表各频道生产效果，也不能代表所有旧频道已完成重排复现。