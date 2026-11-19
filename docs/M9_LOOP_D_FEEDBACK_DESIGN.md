# 循环 D：推荐曝光/点击/转化存储与评估（设计草案）

状态：**未执行**。本节涉及新建辅助表，按执行令和 AGENTS.md 需要单独授权；本文只做准备，不产生 DDL/DML。

## 1. 目标

- 区分“展示分母”和“点击分母”，计算真实 CTR，而不是用点击/行为冒充曝光。
- 用展示位置、频道、榜期、算法版本作为切片，评估榜单质量。
- 以用户后续正向行为（已看/收藏/评分≥4）作为相关性真值，计算 Precision@K / Recall@K / NDCG@K；样本不足显示 N/A。
- 不记录查询关键词、提示词或任何私密文本；只记录必要的展示与点击事实。

## 2. 存储位置

放在独立行为 schema `mi2_m6_realactivity_20261107`，不进入冻结的 16 表目录。

### 2.1 `recommendation_impressions`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `impression_id` | BIGINT PK AUTO | 主键 |
| `request_id` | CHAR(36) | 一次榜单渲染的批次 ID |
| `user_id` | BIGINT | 会话用户 |
| `channel` | VARCHAR(32) | daily/weekly/popular/... |
| `period` | VARCHAR(16) NULL | 榜期，如 `2026-11-13` |
| `board_version` | VARCHAR(32) | `boards-v1` |
| `context` | VARCHAR(32) | home/recommendations/detail/ai |
| `position` | INT | 名次，从 1 开始 |
| `movie_id` | BIGINT NULL | 库内影片 |
| `tmdb_id` | BIGINT NULL | 库外条目 |
| `shown_at` | DATETIME(6) | UTC |

约束/索引：

- UNIQUE `(request_id, position)`
- INDEX `(user_id, shown_at)`、`(channel, shown_at)`、`(movie_id, shown_at)`
- CHECK：`movie_id` 与 `tmdb_id` 至少一个非空；`position >= 1`

### 2.2 `recommendation_clicks`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `click_id` | BIGINT PK AUTO | 主键 |
| `request_id` | CHAR(36) | 对应曝光批次 |
| `position` | INT | 对应曝光名次 |
| `user_id` | BIGINT | 会话用户 |
| `channel` | VARCHAR(32) | 冗余便于查询 |
| `movie_id` / `tmdb_id` | BIGINT NULL | 点击对象 |
| `clicked_at` | DATETIME(6) | UTC |

约束：

- UNIQUE `(request_id, position)`（同一曝光最多一次点击）
- 外键 `request_id` 不强制，避免删除曝光时级联风险；由应用校验存在性。

## 3. 写入接口

- `POST /api/catalog/recommendation-impressions`
  - 登录 + CSRF
  - 请求体：`{request_id, channel, period, board_version, context, items:[{position, movie_id?, tmdb_id?}]}`
  - 限制：单批 ≤ 50；`request_id` 幂等；重复批次直接返回已存在
- `POST /api/catalog/recommendation-clicks`
  - 请求体：`{request_id, position, channel, movie_id?, tmdb_id?}`
  - 校验对应曝光存在且属于当前用户；重复点击幂等

## 4. 评估

- 指标接口：`GET /api/v1/insights/recommendation-feedback?from&to`
  - `impressions`、`clicks`、`ctr = clicks / impressions`
  - 按 channel / period / position 桶切分
  - 展示分母与点击分母分别标注
- 排序指标：
  - 候选集 = 某用户某频道的曝光集合
  - 相关性真值 = 点击后 7 天内出现 `watched` / `favorite` / `rating>=4`
  - 只有同时具备曝光与真值的用户参与计算；用户数、样本量不足则 `INSUFFICIENT_DATA`
  - 输出 Precision@K、Recall@K、NDCG@K（K=5/10），附 `as_of` 与 `definition_version`
- 不把 CTR 当“推荐准确率”；不以点击替代观看/收藏。

## 5. 前端埋点

- `RecommendationView` 成功渲染后发送一次曝光批次（含频道、榜期、名次、库内/库外标识）
- 卡片点击发送点击事件，再跳详情或 TMDB
- 首页两条频道同样接入（context=home）
- 失败静默重试一次；不阻塞渲染、不记录筛选关键词

## 6. 隐私与保留

- 不存关键词、提示词、模型输出、IP、UA
- 仅存 `user_id` + 对象 + 位置 + 时间
- 保留 180 天，之后按天清理
- 评估结果只对管理员开放（沿用 `/api/admin` 或收紧后的 insights）

## 7. 回退

- 表为新增辅助表，删除两张表即可回退；不影响冻结目录与行为事件。
- 前端埋点失败不影响推荐展示。

## 8. 需要授权的内容

1. 在 `mi2_m6_realactivity_20261107` 创建上述两张表及索引
2. 部署曝光/点击写入接口
3. 前端接入埋点并重新构建

在获得明确授权前不执行以上任何一步。
