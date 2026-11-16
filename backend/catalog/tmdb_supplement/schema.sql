-- =============================================================================
-- TMDB 补采：现有库 mi2_m6_realcopy_20261107 的增量结构（仅新增表）
--
-- 状态：提案 / 未执行（NOT EXECUTED）。本文件仅供审查与后续受控执行，
--       不含、也不得包含 CREATE DATABASE、ALTER、DROP、TRUNCATE、GRANT、
--       RENAME、SET FOREIGN_KEY_CHECKS=0 等语句。
--
-- 依据：
--   * backend/catalog/models.py（16 表冻结草案，本文件不改其定义与索引）
--   * 项目工作日志/02-架构与设计/TMDB补采-原库增量结构草案-20261116.md
--   * 项目工作日志/06-开发与验证/TMDB补采-20261116/协作契约.md
--
-- 兼容性声明（未在真实实例核验）：
--   * movie_id 一律 BIGINT（有符号），按冻结草案书写；真实列类型、排序规则、
--     字符集与 MySQL 版本仍需 SHOW CREATE TABLE 核验，见 schema_manifest.json
--     .preconditions 与 .unverified。
--   * 全部新表 ENGINE=InnoDB、DEFAULT CHARSET=utf8mb4、COLLATE=utf8mb4_bin；
--     标识类列显式使用 ascii/ascii_bin（大小写敏感、大小写规则见各列注释）。
--   * 全部外键 ON DELETE RESTRICT ON UPDATE RESTRICT；不建 CASCADE 回退路径。
--   * 不使用 IF NOT EXISTS：执行前必须先跑 manifest.preflight，同名表存在即停止，
--     逐表比对结构指纹后才可续跑，绝不静默跳过。
--   * 本文件按依赖顺序排列；父表先于子表。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1/12 补采任务：预算、断点、状态。只存任务自身信息，不存 Token/连接串/密钥。
-- 关联既有 ingest_batches 做批次审计（dry-run 可不建批次，故可空）。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_supplement_jobs (
  job_id BIGINT NOT NULL AUTO_INCREMENT,
  -- 任务可读标识，与 normalize.py 的 job_id 一致（JOB_RE 上限 128）；唯一，支持同任务续跑。
  job_ref VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- dry-run | write；真实网络/写入必须显式开启，默认 dry-run。
  mode VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- pending | running | paused | succeeded | partial | failed | interrupted
  status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- 请求预算（TMDB 调用上限）与已用计数；预算必须为正，已用不得超过预算。
  request_budget INT NOT NULL,
  request_count INT NOT NULL DEFAULT 0,
  success_count INT NOT NULL DEFAULT 0,
  failure_count INT NOT NULL DEFAULT 0,
  skipped_count INT NOT NULL DEFAULT 0,
  -- 范围摘要（筛选条件、目标数、批次摘要等）；不得写入凭据/Token。
  scope_json JSON NOT NULL,
  -- 断点（游标、页码、已处理标识计数等）；不得写入凭据/Token。
  checkpoint_json JSON NULL,
  ingest_batch_id BIGINT NULL,
  started_at DATETIME(6) NOT NULL,
  heartbeat_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  error_summary VARCHAR(512) NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (job_id),
  UNIQUE KEY uq_tmdb_supplement_jobs_ref (job_ref),
  KEY ix_tmdb_supplement_jobs_status (status, started_at, job_id),
  KEY ix_tmdb_supplement_jobs_batch (ingest_batch_id),
  CONSTRAINT fk_tmdb_supplement_jobs_batch FOREIGN KEY (ingest_batch_id) REFERENCES ingest_batches (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_supplement_jobs_mode CHECK (mode IN ('dry-run','write')),
  CONSTRAINT ck_tmdb_supplement_jobs_status CHECK (status IN ('pending','running','paused','succeeded','partial','failed','interrupted')),
  CONSTRAINT ck_tmdb_supplement_jobs_budget CHECK (request_budget > 0 AND request_count >= 0 AND request_count <= request_budget),
  CONSTRAINT ck_tmdb_supplement_jobs_counts CHECK (success_count >= 0 AND failure_count >= 0 AND skipped_count >= 0),
  CONSTRAINT ck_tmdb_supplement_jobs_finished CHECK (status NOT IN ('succeeded','partial','failed') OR finished_at IS NOT NULL),
  CONSTRAINT ck_tmdb_supplement_jobs_times CHECK (finished_at IS NULL OR finished_at >= started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 补采任务：预算、断点、状态；不含秘密';

-- -----------------------------------------------------------------------------
-- 2/12 制作公司字典。身份是 TMDB company id；名称/来源国家/logo 是可变更属性。
-- 公司来源国家不等于制作国家，不能互相推断。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_companies (
  company_id BIGINT NOT NULL AUTO_INCREMENT,
  tmdb_company_id BIGINT NOT NULL,
  name VARCHAR(255) NOT NULL,
  -- ISO 3166-1 alpha-2 大写；未知为 NULL，不反推为 US。
  origin_country CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NULL,
  logo_path VARCHAR(500) NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (company_id),
  UNIQUE KEY uq_tmdb_companies_tmdb (tmdb_company_id),
  KEY ix_tmdb_companies_country (origin_country, company_id),
  CONSTRAINT ck_tmdb_companies_tmdb CHECK (tmdb_company_id > 0),
  CONSTRAINT ck_tmdb_companies_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
  CONSTRAINT ck_tmdb_companies_origin CHECK (origin_country IS NULL OR (CHAR_LENGTH(origin_country)=2 AND origin_country=UPPER(origin_country)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 制作公司字典';

-- -----------------------------------------------------------------------------
-- 3/12 关键词字典。身份是 TMDB keyword id；名称是属性，不同 id 允许同名，
-- 因此 name 不设唯一约束，也不以译名合并不同 id。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_keywords (
  keyword_id BIGINT NOT NULL AUTO_INCREMENT,
  tmdb_keyword_id BIGINT NOT NULL,
  -- 名称是属性不是身份；宽度对齐 normalize 的 500，超长由 normalizer 报 issue，不截断。
  name VARCHAR(500) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (keyword_id),
  UNIQUE KEY uq_tmdb_keywords_tmdb (tmdb_keyword_id),
  CONSTRAINT ck_tmdb_keywords_tmdb CHECK (tmdb_keyword_id > 0),
  CONSTRAINT ck_tmdb_keywords_name CHECK (CHAR_LENGTH(TRIM(name)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 关键词字典';

-- -----------------------------------------------------------------------------
-- 4/12 当前补充态：每部电影一行（movie_id 唯一）。
-- 状态词表固定五态，不设第六态：NOT_FETCHED / MISSING / EMPTY / PRESENT / FAILED。
-- 三层状态严格分离：
--   * field_status_json：8 个主题最近一次尝试的状态，取值只能是上述五态，
--     只描述“这次取到了什么”，不描述“已保留事实是什么”。
--   * last_attempt_json：该尝试的来源、时间与错误
--     （topic -> {status, attempt_at, source_record_pk, payload_sha256, error_code,
--                conflict_details}），按 attempt_at 单调更新：
--     旧尝试不得覆盖较新尝试的状态或来源。
--   * provenance_json：当前已保留事实的来源与成功版本
--     （topic -> {source_record_pk, payload_sha256, fetched_at, state}），
--     state 只能是 PRESENT 或 EMPTY；失败不得改写此列。
-- 替换规则逐主题独立：只有该主题本次成功、且 fetched_at 严格大于
-- provenance_json 中该主题的 fetched_at 时，才替换该主题关系行并推进对应 *_at。
-- 不允许用全局 fetched_at 短路整部电影：公司已到 t20、国家仅 t10 时，
-- t15 的国家成功仍必须写入。
-- 同主题、同一 fetched_at 但 payload_sha256 不同：不引入新状态，也不把冲突
-- 挂到已保留事实；记为一次失败尝试——
--   field_status_json[topic] = 'FAILED'，
--   last_attempt_json[topic] = {status:'FAILED', error_code:'TOPIC_VERSION_CONFLICT',
--                              conflict_details:{retained_payload_sha256,
--                              incoming_payload_sha256, retained_source_record_pk,
--                              incoming_source_record_pk, attempt_at}, ...}；
-- 关系行、provenance_json、*_at 全部保持不变。
-- 全局 fetched_at 只是最近尝试/响应的高水位（永不倒退），
-- 不代表所有主题都已更新到该时间。
-- 失败（FAILED/MISSING/NOT_FETCHED）只更新 field_status_json 与 last_attempt_json：
-- 不得清空关系行、不得改写 provenance_json、不得推进 *_at。FAILED 即使携带
-- 部分可用 facts 也只保留既有原件与该次 issues，不更新关系与成功时间，
-- 不设“可选部分接受”路径（fail-closed）。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_movie_metadata (
  movie_id BIGINT NOT NULL,
  tmdb_id BIGINT NOT NULL,
  -- base 主题当前事实来源（movie_source_records.id），是 provenance_json.base 的投影列；
  -- 不能用于给其它主题归因，其它主题来源见 provenance_json。
  source_record_pk BIGINT NULL,
  -- 该 base 响应的规范化 JSON SHA256（小写十六进制），与 provenance_json.base 一致；未知 NULL。
  payload_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  -- 最近一次尝试的状态映射（8 主题）；严格五态，冲突不是状态，见文件头。
  field_status_json JSON NOT NULL,
  -- 最近一次尝试的状态/时间/来源/错误映射；按 attempt_at 单调更新，失败只更新这里。
  last_attempt_json JSON NOT NULL,
  -- 已保留事实的来源/成功版本映射（8 主题）；state 仅 PRESENT/EMPTY，失败不得改写。
  provenance_json JSON NOT NULL,
  -- 各主题“已保留事实”的成功时间（UTC），不是最近尝试时间；逐主题独立比较。
  base_at DATETIME(6) NULL,
  countries_at DATETIME(6) NULL,
  companies_at DATETIME(6) NULL,
  spoken_languages_at DATETIME(6) NULL,
  keywords_at DATETIME(6) NULL,
  releases_at DATETIME(6) NULL,
  credits_at DATETIME(6) NULL,
  metrics_at DATETIME(6) NULL,
  -- 全局高水位：最近一次尝试/响应的 fetched_at（UTC），永不倒退；
  -- 不表示所有主题都已更新到该时间。逐主题成功时间见各 *_at 与 provenance_json。
  fetched_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (movie_id),
  UNIQUE KEY uq_tmdb_movie_metadata_tmdb (tmdb_id),
  KEY ix_tmdb_movie_metadata_fetched (fetched_at, movie_id),
  KEY ix_tmdb_movie_metadata_source (source_record_pk, movie_id),
  CONSTRAINT fk_tmdb_movie_metadata_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_tmdb_movie_metadata_source FOREIGN KEY (source_record_pk, movie_id) REFERENCES movie_source_records (id, movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_movie_metadata_tmdb CHECK (tmdb_id > 0),
  CONSTRAINT ck_tmdb_movie_metadata_sha CHECK (payload_sha256 IS NULL OR CHAR_LENGTH(payload_sha256)=64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 当前补充态与各主题采集状态（每电影一行）';

-- -----------------------------------------------------------------------------
-- 5/12 制作国家（多对多）。身份是 (movie_id, country_iso2)；
-- 合拍片多国各计一次，全库电影数仍按 movie_id 去重。
-- 缺失标为未知（metadata.field_status_json），不反推国家。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_movie_countries (
  movie_id BIGINT NOT NULL,
  country_iso2 CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- 展示名缓存；身份仍是 ISO 代码，名称不参与去重；宽度对齐 normalize 的 255，不截断。
  country_name VARCHAR(255) NULL,
  PRIMARY KEY (movie_id, country_iso2),
  KEY ix_tmdb_movie_countries_country (country_iso2, movie_id),
  CONSTRAINT fk_tmdb_movie_countries_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_movie_countries_iso CHECK (CHAR_LENGTH(country_iso2)=2 AND country_iso2=UPPER(country_iso2))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 制作国家关联';

-- -----------------------------------------------------------------------------
-- 6/12 电影-公司关联（多对多）。主键即去重键；同批重跑为幂等替换。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_movie_companies (
  movie_id BIGINT NOT NULL,
  company_id BIGINT NOT NULL,
  PRIMARY KEY (movie_id, company_id),
  KEY ix_tmdb_movie_companies_company (company_id, movie_id),
  CONSTRAINT fk_tmdb_movie_companies_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_tmdb_movie_companies_company FOREIGN KEY (company_id) REFERENCES tmdb_companies (company_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 电影-制作公司关联';

-- -----------------------------------------------------------------------------
-- 7/12 对白语言（多对多，已合并语言字典职责）。
-- 身份是 ISO 639-1 小写代码；english_name/name 是该次成功响应的展示属性。
-- 对白语言与 movies.original_language、发行地区三者不同，不得互相推导。
-- 合并说明：639-1 是封闭标准，字典只有“代码+两个展示名”，拆独立字典表会增加
-- 第二条同步路径而无查询收益；按语言汇总只需代码，展示名可取自任一行。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_movie_languages (
  movie_id BIGINT NOT NULL,
  language_code VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- 展示名宽度对齐 normalize 的 500；缺代码的语言不伪造行。
  english_name VARCHAR(500) NULL,
  name VARCHAR(500) NULL,
  PRIMARY KEY (movie_id, language_code),
  KEY ix_tmdb_movie_languages_code (language_code, movie_id),
  CONSTRAINT fk_tmdb_movie_languages_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_movie_languages_code CHECK (CHAR_LENGTH(language_code) BETWEEN 2 AND 16 AND language_code=LOWER(language_code))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 对白语言关联（含语言展示名缓存）';

-- -----------------------------------------------------------------------------
-- 8/12 电影-关键词关联（多对多）。主键即去重键。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_movie_keywords (
  movie_id BIGINT NOT NULL,
  keyword_id BIGINT NOT NULL,
  PRIMARY KEY (movie_id, keyword_id),
  KEY ix_tmdb_movie_keywords_keyword (keyword_id, movie_id),
  CONSTRAINT fk_tmdb_movie_keywords_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_tmdb_movie_keywords_keyword FOREIGN KEY (keyword_id) REFERENCES tmdb_keywords (keyword_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 电影-关键词关联';

-- -----------------------------------------------------------------------------
-- 9/12 地区发行明细。一条 = 某电影在某地区的一次发行记录。
-- 去重不依赖可空复合唯一，也不用 normalize 的 dedupe_key 直接做全局唯一：
--   normalize._releases 的 item.dedupe_key =
--     SHA256(canonical_json({iso_3166_1, type, release_date_raw,
--                           certification, note, iso_639_1}))，
--   canonical_json = json.dumps(ensure_ascii=False, sort_keys=True,
--                   separators=(',',':'), allow_nan=False)。
--   该 key 不含 movie_id，两片若有完全相同的发行元组会得到同一 key。
-- 因此 record_fingerprint 必须由写入方按下列公式重算（manifest.release_dedupe 为准）：
--   record_fingerprint = SHA256_hex( utf8( str(movie_id) + U+001F + dedupe_key ) )
--   —— 十进制 movie_id、单个 0x1F 分隔符、无空格/无补零、小写十六进制。
-- 该列 NOT NULL + 全局 UNIQUE；跨片相同发行因 movie_id 前缀不同而不会合并。
-- release_date 可为 NULL（未知/部分日期），原始串保留在 release_date_raw。
-- 来源：source_record_pk 必须存在且属于同一 movie_id（复合外键保证）。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_release_records (
  release_id BIGINT NOT NULL AUTO_INCREMENT,
  movie_id BIGINT NOT NULL,
  country_iso2 CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  -- TMDB release type：1 首映/2 有限影院/3 影院/4 数字/5 实体/6 电视；未知 NULL。
  release_type TINYINT UNSIGNED NULL,
  release_date DATE NULL,
  release_date_raw VARCHAR(64) NULL,
  certification VARCHAR(64) NULL,
  note VARCHAR(512) NULL,
  -- 认证/备注语言（TMDB release_dates[].iso_639_1），小写；未知 NULL。
  certification_language VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
  source_record_pk BIGINT NOT NULL,
  -- SHA256(str(movie_id) + U+001F + normalize.dedupe_key)，小写十六进制；不得只写 dedupe_key。
  record_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at DATETIME(6) NOT NULL,
  PRIMARY KEY (release_id),
  UNIQUE KEY uq_tmdb_release_records_fingerprint (record_fingerprint),
  KEY ix_tmdb_release_records_movie (movie_id, country_iso2, release_date),
  KEY ix_tmdb_release_records_country (country_iso2, movie_id, release_date),
  KEY ix_tmdb_release_records_source (source_record_pk, movie_id),
  CONSTRAINT fk_tmdb_release_records_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_tmdb_release_records_source FOREIGN KEY (source_record_pk, movie_id) REFERENCES movie_source_records (id, movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_release_records_iso CHECK (CHAR_LENGTH(country_iso2)=2 AND country_iso2=UPPER(country_iso2)),
  CONSTRAINT ck_tmdb_release_records_type CHECK (release_type IS NULL OR release_type BETWEEN 1 AND 6),
  CONSTRAINT ck_tmdb_release_records_raw CHECK (release_date_raw IS NULL OR CHAR_LENGTH(release_date_raw) > 0),
  CONSTRAINT ck_tmdb_release_records_lang CHECK (certification_language IS NULL OR (CHAR_LENGTH(certification_language) BETWEEN 2 AND 16 AND certification_language=LOWER(certification_language)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 各地区发行记录（指纹去重）';

-- -----------------------------------------------------------------------------
-- 10/12 真实指标观测（历史点）。一次成功采集一条；observed_at 是实际采集时刻(UTC)。
-- 幂等：UNIQUE(movie_id, observed_at)——同批重跑复用同一 observed_at 即为重复，不新增。
-- 历史保留：不用 payload_hash 跨日期去重，不同日期即使数值相同也保留新观测。
-- 有效 0 保留（vote_count=0、popularity=0 都是真实观测），未知/缺字段写 NULL 而不是 0。
-- TMDB 评分与站内评分独立：站内当前值仍在 movie_ratings_summary(source 区分)。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_metric_observations (
  observation_id BIGINT NOT NULL AUTO_INCREMENT,
  movie_id BIGINT NOT NULL,
  observed_at DATETIME(6) NOT NULL,
  vote_average DECIMAL(4,2) NULL,
  vote_count BIGINT NULL,
  popularity DECIMAL(20,4) NULL,
  source_record_pk BIGINT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  PRIMARY KEY (observation_id),
  UNIQUE KEY uq_tmdb_metric_observations_movie_time (movie_id, observed_at),
  KEY ix_tmdb_metric_observations_time (observed_at, movie_id),
  KEY ix_tmdb_metric_observations_source (source_record_pk, movie_id),
  CONSTRAINT fk_tmdb_metric_observations_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_tmdb_metric_observations_source FOREIGN KEY (source_record_pk, movie_id) REFERENCES movie_source_records (id, movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_metric_observations_avg CHECK (vote_average IS NULL OR (vote_average >= 0 AND vote_average <= 10)),
  CONSTRAINT ck_tmdb_metric_observations_votes CHECK (vote_count IS NULL OR vote_count >= 0),
  CONSTRAINT ck_tmdb_metric_observations_pop CHECK (popularity IS NULL OR popularity >= 0),
  CONSTRAINT ck_tmdb_metric_observations_empty CHECK (vote_count IS NULL OR vote_count <> 0 OR vote_average IS NULL OR vote_average = 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 指标历史观测（真实采集时点）';

-- -----------------------------------------------------------------------------
-- 11/12 榜单快照。一次频道采集一行；captured_at 是实际采集时刻，不伪造过去榜单。
-- 频道沿用既有词表：popular / trending_day / trending_week。
-- 幂等：UNIQUE(board_channel, captured_at)；同日重复抓取是不同快照，需显式区分时刻。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_board_snapshots (
  snapshot_id BIGINT NOT NULL AUTO_INCREMENT,
  board_channel VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  captured_at DATETIME(6) NOT NULL,
  -- succeeded | partial | failed
  status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  page_count INT NOT NULL DEFAULT 1,
  entry_count INT NOT NULL DEFAULT 0,
  -- 本次榜单原始响应摘要；失败时可为 NULL。
  payload_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ingest_batch_id BIGINT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  PRIMARY KEY (snapshot_id),
  UNIQUE KEY uq_tmdb_board_snapshots_channel_time (board_channel, captured_at),
  KEY ix_tmdb_board_snapshots_batch (ingest_batch_id),
  CONSTRAINT fk_tmdb_board_snapshots_batch FOREIGN KEY (ingest_batch_id) REFERENCES ingest_batches (batch_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_board_snapshots_channel CHECK (board_channel IN ('popular','trending_day','trending_week')),
  CONSTRAINT ck_tmdb_board_snapshots_status CHECK (status IN ('succeeded','partial','failed')),
  CONSTRAINT ck_tmdb_board_snapshots_counts CHECK (page_count > 0 AND entry_count >= 0),
  CONSTRAINT ck_tmdb_board_snapshots_sha CHECK (payload_sha256 IS NULL OR CHAR_LENGTH(payload_sha256)=64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 榜单快照（频道+采集时刻）';

-- -----------------------------------------------------------------------------
-- 12/12 榜单条目。库外电影允许 movie_id 为 NULL，保留 tmdb_id 与原始排名；
-- 不自动创建本地电影、不按标题猜测身份。库内映射由 movie_external_ids(
-- source='tmdb') 解析后写入 movie_id。
-- -----------------------------------------------------------------------------
CREATE TABLE tmdb_board_entries (
  snapshot_id BIGINT NOT NULL,
  tmdb_id BIGINT NOT NULL,
  rank_position INT NOT NULL,
  -- 库内电影；库外为 NULL（外键不约束 NULL），不自动建片。
  movie_id BIGINT NULL,
  -- 榜单当时的标题快照，仅用于库外条目展示，不作身份依据。
  title VARCHAR(500) NULL,
  PRIMARY KEY (snapshot_id, tmdb_id),
  UNIQUE KEY uq_tmdb_board_entries_rank (snapshot_id, rank_position),
  KEY ix_tmdb_board_entries_tmdb (tmdb_id, snapshot_id),
  KEY ix_tmdb_board_entries_movie (movie_id),
  CONSTRAINT fk_tmdb_board_entries_snapshot FOREIGN KEY (snapshot_id) REFERENCES tmdb_board_snapshots (snapshot_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_tmdb_board_entries_movie FOREIGN KEY (movie_id) REFERENCES movies (movie_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_tmdb_board_entries_rank CHECK (rank_position > 0),
  CONSTRAINT ck_tmdb_board_entries_tmdb CHECK (tmdb_id > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='TMDB 榜单条目（允许库外 movie_id 为 NULL）';