-- Loop D auxiliary tables. Activity schema only; catalog schema is frozen.
CREATE TABLE IF NOT EXISTS recommendation_impressions (
  impression_id BIGINT NOT NULL AUTO_INCREMENT,
  request_id CHAR(36) NOT NULL,
  user_id BIGINT NOT NULL,
  channel VARCHAR(32) NOT NULL,
  period VARCHAR(16) NULL,
  board_version VARCHAR(32) NOT NULL,
  context VARCHAR(32) NOT NULL,
  position INT NOT NULL,
  movie_id BIGINT NULL,
  tmdb_id BIGINT NULL,
  shown_at DATETIME(6) NOT NULL,
  PRIMARY KEY (impression_id),
  UNIQUE KEY uq_recommendation_impressions_0 (request_id, position),
  KEY ix_recommendation_impressions_0 (user_id, shown_at),
  KEY ix_recommendation_impressions_1 (channel, shown_at),
  KEY ix_recommendation_impressions_2 (movie_id, shown_at),
  CONSTRAINT ck_recommendation_impressions_0 CHECK (movie_id IS NOT NULL OR tmdb_id IS NOT NULL),
  CONSTRAINT ck_recommendation_impressions_1 CHECK (position >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS recommendation_clicks (
  click_id BIGINT NOT NULL AUTO_INCREMENT,
  request_id CHAR(36) NOT NULL,
  position INT NOT NULL,
  user_id BIGINT NOT NULL,
  channel VARCHAR(32) NOT NULL,
  movie_id BIGINT NULL,
  tmdb_id BIGINT NULL,
  clicked_at DATETIME(6) NOT NULL,
  PRIMARY KEY (click_id),
  UNIQUE KEY uq_recommendation_clicks_0 (request_id, position),
  KEY ix_recommendation_clicks_0 (user_id, clicked_at),
  KEY ix_recommendation_clicks_1 (channel, clicked_at),
  KEY ix_recommendation_clicks_2 (movie_id, clicked_at),
  CONSTRAINT ck_recommendation_clicks_0 CHECK (movie_id IS NOT NULL OR tmdb_id IS NOT NULL),
  CONSTRAINT ck_recommendation_clicks_1 CHECK (position >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
