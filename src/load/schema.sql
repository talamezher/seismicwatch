-- SeismicWatch warehouse (SQLite). Star schema:
--   fact_earthquake      grain: one earthquake (one USGS event id, its current version)
--   fact_place_exposure  grain: one earthquake x one populated place within 50 km
--   dimensions: dim_date, dim_country, dim_place, dim_magnitude_band,
--               dim_event_version (type 2: every version of an event USGS published)
-- Operational tables: run_log, data_test_results, quarantine, join_log.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dim_date (
    date_key        INTEGER PRIMARY KEY,          -- yyyymmdd
    full_date       TEXT    NOT NULL UNIQUE,      -- ISO date, UTC
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    month_name      TEXT    NOT NULL,
    day             INTEGER NOT NULL,
    day_of_week     TEXT    NOT NULL,
    is_weekend      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_country (
    country_key       INTEGER PRIMARY KEY,
    country_code      TEXT NOT NULL UNIQUE,       -- ISO 3166 alpha-2, 'XX' = at sea or remote (BR-1)
    country_name      TEXT NOT NULL,
    is_study_country  INTEGER NOT NULL            -- 1 for Lebanon, Turkey, Cyprus
);

CREATE TABLE IF NOT EXISTS dim_place (
    place_key        INTEGER PRIMARY KEY,         -- GeoNames geonameid (a stable natural key)
    place_name       TEXT NOT NULL,
    place_name_ascii TEXT,
    country_key      INTEGER NOT NULL REFERENCES dim_country (country_key),
    admin1_code      TEXT,
    feature_code     TEXT NOT NULL,
    latitude         REAL NOT NULL,
    longitude        REAL NOT NULL,
    population       INTEGER NOT NULL,
    population_class TEXT NOT NULL,
    modified_on      TEXT
);

CREATE TABLE IF NOT EXISTS dim_magnitude_band (
    magnitude_band_key INTEGER PRIMARY KEY,
    band_label         TEXT NOT NULL UNIQUE,
    min_magnitude      REAL NOT NULL,             -- included
    max_magnitude      REAL NOT NULL,             -- excluded
    usgs_class         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_event_version (
    event_version_key  INTEGER PRIMARY KEY,
    event_id           TEXT NOT NULL,
    version_no         INTEGER NOT NULL,
    magnitude          REAL,
    mag_type           TEXT,
    review_status      TEXT,
    latitude           REAL,
    longitude          REAL,
    depth_km           REAL,
    place_text         TEXT,
    source_updated_at  TEXT NOT NULL,             -- USGS 'updated' of this version (UTC)
    valid_from         TEXT NOT NULL,
    valid_to           TEXT,                      -- NULL while current
    is_current         INTEGER NOT NULL,
    UNIQUE (event_id, source_updated_at)
);

CREATE TABLE IF NOT EXISTS fact_earthquake (
    event_id                TEXT PRIMARY KEY,     -- USGS event id (natural key of the grain)
    event_version_key       INTEGER NOT NULL REFERENCES dim_event_version (event_version_key),
    date_key                INTEGER NOT NULL REFERENCES dim_date (date_key),
    place_key               INTEGER NOT NULL REFERENCES dim_place (place_key),     -- nearest place
    country_key             INTEGER NOT NULL REFERENCES dim_country (country_key),
    magnitude_band_key      INTEGER NOT NULL REFERENCES dim_magnitude_band (magnitude_band_key),
    event_time_utc          TEXT    NOT NULL,
    magnitude               REAL    NOT NULL,
    mag_type                TEXT,
    depth_km                REAL,
    depth_is_default        INTEGER NOT NULL,
    depth_class             TEXT,
    latitude                REAL    NOT NULL,
    longitude               REAL    NOT NULL,
    usgs_place_text         TEXT,
    review_status           TEXT,
    distance_to_place_km    REAL    NOT NULL,
    places_within_25km      INTEGER NOT NULL,
    places_within_50km      INTEGER NOT NULL,
    population_within_25km  INTEGER NOT NULL,
    population_within_50km  INTEGER NOT NULL,
    in_study_period         INTEGER NOT NULL,
    is_documented           INTEGER NOT NULL,     -- matched to the Wikipedia list (BR-4)
    wiki_place              TEXT,
    wiki_deaths             INTEGER,
    significance            INTEGER,
    felt_reports            INTEGER,
    tsunami_flag            INTEGER
);

CREATE TABLE IF NOT EXISTS fact_place_exposure (
    event_id     TEXT    NOT NULL REFERENCES fact_earthquake (event_id),
    place_key    INTEGER NOT NULL REFERENCES dim_place (place_key),
    date_key     INTEGER NOT NULL REFERENCES dim_date (date_key),
    distance_km  REAL    NOT NULL,
    within_25km  INTEGER NOT NULL,
    PRIMARY KEY (event_id, place_key)
);

CREATE INDEX IF NOT EXISTS ix_fact_date    ON fact_earthquake (date_key);
CREATE INDEX IF NOT EXISTS ix_fact_country ON fact_earthquake (country_key);
CREATE INDEX IF NOT EXISTS ix_exp_place    ON fact_place_exposure (place_key);

-- ---- operational tables (they keep history; they are not part of the star) ----
CREATE TABLE IF NOT EXISTS run_log (
    run_id             TEXT PRIMARY KEY,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    mode               TEXT,
    status             TEXT NOT NULL,             -- running, success, stopped, failed
    records_extracted  INTEGER,
    events_staged      INTEGER,
    events_loaded      INTEGER,
    events_quarantined INTEGER,
    tests_failed       INTEGER,
    warehouse_hash     TEXT,                      -- fingerprint of the star tables
    message            TEXT
);

CREATE TABLE IF NOT EXISTS data_test_results (
    run_id       TEXT NOT NULL,
    test_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    table_name   TEXT,
    failing_rows INTEGER NOT NULL,
    passed       INTEGER NOT NULL,
    details      TEXT,
    PRIMARY KEY (run_id, test_id)
);

CREATE TABLE IF NOT EXISTS quarantine (
    run_id          TEXT NOT NULL,
    source          TEXT NOT NULL,
    record_key      TEXT NOT NULL,
    test_id         TEXT NOT NULL,
    severity        TEXT NOT NULL,
    reason          TEXT NOT NULL,
    quarantined_at  TEXT NOT NULL,
    record_json     TEXT NOT NULL,
    PRIMARY KEY (run_id, source, record_key)
);

CREATE TABLE IF NOT EXISTS join_log (
    run_id       TEXT NOT NULL,
    join_name    TEXT NOT NULL,
    rows_before  INTEGER NOT NULL,
    rows_after   INTEGER NOT NULL,
    unmatched    INTEGER,
    PRIMARY KEY (run_id, join_name)
);

-- ---- views read by the dashboard ----
CREATE VIEW IF NOT EXISTS v_earthquakes AS
SELECT f.event_id, f.event_time_utc, d.full_date, d.year, d.month, f.magnitude, b.band_label,
       f.depth_km, f.depth_class, f.latitude, f.longitude, c.country_code, c.country_name,
       c.is_study_country, p.place_name AS nearest_place, p.population AS nearest_population,
       f.distance_to_place_km, f.places_within_25km, f.places_within_50km,
       f.population_within_25km, f.population_within_50km, f.in_study_period,
       f.is_documented, f.wiki_place, f.wiki_deaths, f.usgs_place_text, f.review_status
FROM fact_earthquake f
JOIN dim_date d USING (date_key)
JOIN dim_country c USING (country_key)
JOIN dim_place p USING (place_key)
JOIN dim_magnitude_band b USING (magnitude_band_key);

CREATE VIEW IF NOT EXISTS v_place_exposure AS
SELECT x.place_key, p.place_name, pc.country_code AS place_country, p.population, p.latitude AS place_lat,
       p.longitude AS place_lon, x.event_id, x.distance_km, x.within_25km, f.magnitude, f.event_time_utc,
       d.year, ec.country_code AS event_country, f.in_study_period
FROM fact_place_exposure x
JOIN dim_place p ON p.place_key = x.place_key
JOIN dim_country pc ON pc.country_key = p.country_key
JOIN fact_earthquake f ON f.event_id = x.event_id
JOIN dim_country ec ON ec.country_key = f.country_key
JOIN dim_date d ON d.date_key = f.date_key;
