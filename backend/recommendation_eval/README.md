# recommendation_eval — Baseline V1 与离线评估工具

本目录是 M10 员工 1 的独立离线评估包，**不会被 Flask 应用导入**。它只读取外部 JSON，调用调用方提供的排序器 adapter，不连接 HTTP、数据库、模型或用户会话。

## 1. 交付边界

- `baseline_v1.py`：把 `catalog/recommendation.py` 的纯 `recommend()` 按原字节冻结为可执行函数；不改算法、不做重写。
- `boards_reference.py` / `BOARDS_REFERENCE.md`：记录当前 `recommendation_boards.board()` 的频道、参数和排序口径。
- `schema.py`：电影快照、用户状态、场景/标签的严格校验。
- `metrics.py` / `evaluate.py`：Recall@10、NDCG@10、全局覆盖、类型多样性/覆盖、硬条件违规率、已看/不感兴趣误推荐率，以及逐场景 ID、分数/分项、耗时/内存。
- `adapters.py`：Baseline adapter、演示 adapter、任意可调用 adapter。
- `fixtures/`：合成快照、状态、十类场景模板和 JSON Schema。
- `tests/`：43 项纯标准库单测。

**Baseline V1 只对应纯 `recommend()`。** `boards` 的公开榜单、口碑、收入、because、日/周扰动与缓存不在基线内；不能把本包结果说成“所有旧频道已复现”。

## 2. 冻结来源与许可

| 项 | 值 |
|---|---|
| baseline_id | `baseline-v1-catalog-recommend-20261113` |
| 原文件 | `backend/catalog/recommendation.py` |
| 原字节 SHA-256 | `d75483891c0d892e12ed1719c583107230dd784b8ff495767afa6a7d420225f2` |
| 函数 | `recommend`，原文件第 53–89 行 |
| 函数原文 SHA-256 | `ad255c8f379a53a9028d2174f7641e238da2caf6bf104447fb65e392db867a61` |
| 冻结日 | 2026-11-15 |
| 许可 | `NOT_DECLARED_IN_REPO`：仓库根目录与 backend 未发现 LICENSE/COPYING/NOTICE，不推断许可 |

后续 Codex 修改原文件时，Baseline V1 不追随更新。可用 `tools/freeze_baseline.py` 核对生成条件；该工具拒绝在原字节变化后重写基线。

默认参数保持原样：`limit=12`、`weights=None`（favorite=3/want=2/rating=1）、`baseline=False`、`baseline_field='demo_popularity'`、`profile_movies=None`。算法行为见 `baseline_v1.py` 顶部说明。

## 3. 输入契约与边界语义

### 电影快照

```json
{"schema_version":"m10-eval-v1","dataset_id":"...","source":"...","frozen_at":"...+08:00","movies":[...]}
```

- `movie_id` 必须是正整数；`title` 非空；`genres` 为字符串数组；`catalog_status` 为 `active/hidden/inactive`，缺省 `active`；`is_adult=True` 不进入候选。
- 重复 `movie_id`：严格模式直接报错；宽松模式保留第一条并记录 `duplicate_ids`。
- 非法行（ID 类型错误、负 ID、标题为空等）：严格模式报错；宽松模式跳过并记录 `invalid_entries`。
- 空 `movies` 允许；此时覆盖类指标分母为 0，返回 `null` 及原因，不伪造 0。
- 本包不抓取真实数据库；合成 ID 必须明确标为 `SYNTHETIC`，不得伪装真实 TMDB/内部 ID。

### 用户状态

- 键为快照中存在的电影 ID；未知 ID 严格模式报错、宽松模式丢弃。
- 允许 `rating`（0.5–5，0.5 步长）与 `favorite/want/watched/not_interested` 布尔值。
- `state_as_of` 必须带时区。**曝光、点击、已看不自动等于喜欢**；它们只在负反馈/过滤指标中使用，不会变成相关性标签。

### 场景与标签

- 类别固定十种：`cold_start`、`genre`、`multi_genre`、`watched`、`not_interested`、`multi_hard_conditions`、`soft_preference`、`niche`、`no_results`、`out_of_catalog`。
- `split` 显式写 `train/dev/test`，不得由运行时推断。
- `label_source` 只能是 `HUMAN`、`HISTORICAL_HELDOUT`、`SYNTHETIC`、`NONE`。
- `SYNTHETIC` 不能被标为 `reliable`；`NONE` 必须使用空标签列表。
- `HISTORICAL_HELDOUT` 必须提供 `state_as_of` 与 `label_window`，且 `label_window.from` 严格晚于 `state_as_of`，否则拒绝；避免当前状态冒充未来真值。
- 重复标签 ID 去重并记录；不存在的标签 ID 严格模式拒绝，宽松模式记入 `invalid_movie_ids` 并使效力指标为 `null`，不会静默当成有效标签。
- 空标签集合：Recall/NDCG 返回 `null`（原因 `EMPTY_LABEL_SET`），不是 0。
- 列表截断：adapter 返回超过 10 条时，指标只取前 10；`dropped_after_k` 记录被截掉的合法 ID，`truncated=true`。
- 输出重复 ID：第一次出现参与指标；后续标记 `duplicate`，不占额外名额。
- 非法/未知/非 active 输出 ID：记入 `invalid_items`，不进入命中、覆盖或多样性分母；非法 ID 率单独统计。
- 无结果：若存在标签，Recall/NDCG 为 0（候选确实没被返回）；若没有标签，则为 `null`。
- 库外结果：必须 `movie_id=null` 且有 `external_id`；只统计为 external，绝不生成伪造内部 ID。`allow_external=false` 时标记 `EXTERNAL_NOT_ALLOWED`。

完整结构见 `fixtures/scenarios.schema.json`，可复制模板见 `fixtures/scenarios.template.json`。

## 4. 指标与分母

| 指标 | 公式/口径 | 分母 |
|---|---|---|
| Recall@10 | 每个合格测试场景 `|top10 ∩ relevant| / |relevant|`，再对场景取宏平均 | 全部标签相关集；无标签为 null |
| NDCG@10 | 二元相关，`DCG=Σ rel/log2(rank+1)`，`IDCG` 用 `min(10,|relevant|)` 个理想位置，再宏平均 | 同场景理想排序；无标签为 null |
| 全局电影覆盖 | 所有 OK 场景 top10 中不重复的合法 active ID / active 非成人影片数 | 快照 active 集合；空快照为 null |
| 类型覆盖 | 推荐出现的类型标签并集 / 快照 active 类型并集 | 快照类型并集；空为 null |
| 类型多样性 | 每个场景 top10 的两两 `1 - Jaccard` 距离均值；少于两个有类型项的场景排除 | 有效场景数；无有效场景为 null |
| 硬条件违规率 | 硬条件场景 top10 中违规位置数 / 这些场景 top10 位置数 | 仅声明硬条件的场景；零位置为 null |
| 已看/不感兴趣误推荐率 | top10 中状态为 watched/not_interested 的位置数 / 全部 OK 场景 top10 位置数 | 全部 top10 位置；零位置为 null |
| 非法 ID 率 | 未知/非 active/缺 ID 的输出条目 / 全部输出条目（截断前） | 原始输出条目；external 单列 |
| 重复输出率 | 第一次出现之后的重复合法 ID / 全部输出条目 | 原始输出条目 |

`metric_definitions` 会随结果 JSON 一起输出。所有指标都是离线描述，不是线上因果效果。

## 5. 标签效力规则

- 默认 `metric_mode="effectiveness"`：只有 `split=test`、`label_source∈{HUMAN,HISTORICAL_HELDOUT}`、`reliable=true` 且标签非空/无非法 ID 的场景才计算 Recall/NDCG。
- `metric_mode="synthetic_engine_check"`：允许 `SYNTHETIC` 标签参与计算，但结果标记 `claim_scope=SYNTHETIC_ENGINE_CHECK_ONLY`、`effectiveness_available=false`、`online_effect=NOT_PROVEN`。**SYNTHETIC 只能校验计算器，不能称真实用户效果。**
- `train/dev` 场景仍执行安全/覆盖/资源统计，但不进入效力指标；无可靠标签时返回 `null` 和原因，不返回 0。

## 6. Adapter 协议

```python
class Adapter:
    def rank(self, movies, state, scenario) -> list | dict:
        # list: [movie_id, ...] 或 [{"movie_id":..., "score":..., "components":..., "reasons":[...]}]
        # dict: {"items": [...], "candidate_count": int, "paid_model_tokens": 0, "metadata": {...}}
```

`BaselineV1Adapter(mode="prefiltered")` 会先应用场景硬条件与 watched/not_interested 过滤，再调用冻结函数；`mode="raw"` 不做频道过滤，便于测出纯函数的硬条件违规。两种模式都不改 `recommend()`。

## 7. 复现命令

在 `项目源码/backend` 下执行（TEMP 指到 F 盘，禁用字节码/插件）：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='F:/.../M10-20261115/员工1-评估/.tmp'
$env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP

& 'C:/Program Files/Python311/python.exe' -B -m recommendation_eval.run_all `
  --output-dir 'F:/.../M10-20261115/员工1-评估'
```

单测/演示也可分开：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m recommendation_eval.run_checks
& 'C:/Program Files/Python311/python.exe' -B -m recommendation_eval.demo --output <demo.json>
```

## 8. 输出

- `unit-results.json`：43 项检查名、通过/失败、原始 detail、退出码。
- `demo-result.json`：十场景端到端原始结果（逐场景 ID、分数/分项、资源、指标）。
- `result.json`：简明机器结论、Baseline/boards 来源、代码 SHA、副作用、未覆盖项。

## 9. 未覆盖

未使用真实冻结电影快照、人工/历史留出标签、统计显著性、线上因果归因、Flask/MySQL/HTTP/前端、真实模型/BYOK、浏览器布局。`boards` 只记录口径，不复现全部频道；真实快照由主脑后续提供。