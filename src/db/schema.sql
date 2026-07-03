-- Nerdpraat Telemetry schema (MySQL / MariaDB)

CREATE TABLE IF NOT EXISTS users (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
  email         VARCHAR(255) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  name          VARCHAR(120) NULL,
  role          ENUM('admin','user') NOT NULL DEFAULT 'user',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sites (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name       VARCHAR(160) NOT NULL,
  slug       VARCHAR(160) NOT NULL,
  owner_id   INT UNSIGNED NULL,
  api_key    VARCHAR(64) NOT NULL,
  is_public  TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sites_slug (slug),
  UNIQUE KEY uq_sites_api_key (api_key),
  KEY idx_sites_owner (owner_id),
  CONSTRAINT fk_sites_owner FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Entity configuration: maps a HA entity to a telemetry kind.
CREATE TABLE IF NOT EXISTS site_entities (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  site_id    INT UNSIGNED NOT NULL,
  kind       ENUM('grid','solar','consumer','battery') NOT NULL,
  entity_id  VARCHAR(255) NOT NULL,
  name       VARCHAR(160) NULL,
  unit       VARCHAR(16) NOT NULL DEFAULT 'W',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_site_entity_kind (site_id, kind),
  KEY idx_site_entities_site (site_id),
  CONSTRAINT fk_entities_site FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Energy/power readings (kept for RETENTION_HOURS).
CREATE TABLE IF NOT EXISTS energy_readings (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  site_id     INT UNSIGNED NOT NULL,
  kind        ENUM('grid','solar','consumer','battery') NOT NULL,
  value       DECIMAL(14,3) NOT NULL,
  unit        VARCHAR(16) NOT NULL DEFAULT 'W',
  recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_readings_site_kind_time (site_id, kind, recorded_at),
  KEY idx_readings_time (recorded_at),
  CONSTRAINT fk_readings_site FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
