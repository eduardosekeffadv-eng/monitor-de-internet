-- Monitor de Internet — esquema do banco (SQLite, modo WAL)
-- Horários em milissegundos desde 1970-01-01 UTC (exibidos no horário local).

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Cada execução do programa é uma sessão de monitoramento.
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   INTEGER NOT NULL,
    ended_at     INTEGER,
    end_reason   TEXT,
    interval_ms  INTEGER NOT NULL,
    version      TEXT,
    simulated    INTEGER NOT NULL DEFAULT 0,
    provider     TEXT                  -- tipo de conexão no início da sessão
);

-- Períodos de modo de conexão (cabo / Wi-Fi direto / Wi-Fi via roteador).
CREATE TABLE IF NOT EXISTS mode_periods (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mode        TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    confirmed   INTEGER NOT NULL DEFAULT 0,
    session_id  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_mode_periods_start ON mode_periods(started_at);

-- Uma linha por ciclo de medição.
-- Métodos (*_m): NULL = não testado, 0 = sem resposta, 1 = ICMP, 2 = TCP.
-- status: 0 normal, 1 latência elevada, 2 crítica, 3 perda parcial, 4 sem resposta externa.
CREATE TABLE IF NOT EXISTS samples (
    ts          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL,
    mode_id     INTEGER,
    status      INTEGER NOT NULL,
    conn        INTEGER NOT NULL,      -- 0 online, 1 instável, 2 offline
    lat         REAL,                  -- latência principal (menor entre os externos que responderam)
    gw          REAL, gw_m   INTEGER,
    cf          REAL, cf_m   INTEGER,
    gg          REAL, gg_m   INTEGER,
    dish        REAL, dish_m INTEGER,
    ext_lost    INTEGER NOT NULL,
    ext_total   INTEGER NOT NULL,
    wifi        INTEGER                -- sinal Wi-Fi (%) quando aplicável
);

-- Ocorrências: interrupções, microquedas, perda, latência, lacunas do monitor etc.
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL,
    start_ts       INTEGER NOT NULL,
    end_ts         INTEGER,
    precision_ms   INTEGER NOT NULL DEFAULT 0,
    session_id     INTEGER,
    mode           TEXT,
    mode_id        INTEGER,
    wifi_signal    INTEGER,
    router_state   TEXT,
    targets        TEXT,              -- JSON
    lat_before     REAL,
    lat_after      REAL,
    diagnosis      TEXT,
    diagnosis_text TEXT,
    details        TEXT,              -- JSON
    truncated      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_start ON events(start_ts);
CREATE INDEX IF NOT EXISTS ix_events_type_start ON events(type, start_ts);

-- Agregados por minuto (mantidos por mais tempo que os dados brutos).
CREATE TABLE IF NOT EXISTS agg_minute (
    minute_ts   INTEGER PRIMARY KEY,
    mode_id     INTEGER,
    n           INTEGER NOT NULL,
    n_lat       INTEGER NOT NULL,
    lat_avg     REAL,
    lat_min     REAL,
    lat_max     REAL,
    ext_lost    INTEGER NOT NULL,
    ext_total   INTEGER NOT NULL,
    n_fail      INTEGER NOT NULL,      -- ciclos sem nenhuma resposta externa
    n_offline   INTEGER NOT NULL,      -- ciclos dentro de interrupção confirmada
    gw_avg      REAL,
    wifi_avg    REAL,
    covered_ms  INTEGER,               -- tempo monitorado no minuto (ciclos × intervalo)
    lost_on     INTEGER,               -- perdas fora de interrupção confirmada
    total_on    INTEGER                -- verificações fora de interrupção confirmada
);

-- Quedas informadas pela própria antena Starlink (API local), com o motivo.
CREATE TABLE IF NOT EXISTS dish_outages (
    start_ts    INTEGER PRIMARY KEY,   -- horário da antena (GPS convertido para UTC)
    duration_ms INTEGER NOT NULL,
    cause       TEXT NOT NULL,         -- nome informado pela antena (ex.: NO_PINGS, OBSTRUCTED)
    did_switch  INTEGER NOT NULL DEFAULT 0,  -- houve troca de satélite
    seen_at     INTEGER NOT NULL       -- quando o monitor recebeu a informação
);

-- Estatísticas medidas pela antena, por janela de consulta (~1 min).
CREATE TABLE IF NOT EXISTS dish_stats (
    ts                 INTEGER PRIMARY KEY,   -- fim da janela
    samples            INTEGER NOT NULL,      -- segundos cobertos
    drop_s             REAL,                  -- perda total (equivalente em segundos)
    full_drop_s        INTEGER,               -- segundos com 100% de perda
    obstructed_s       REAL,
    full_obstructed_s  INTEGER,
    unscheduled_s      REAL,
    full_unscheduled_s INTEGER,
    lat_mean_ms        REAL,
    power_w            REAL,
    down_bytes         INTEGER,
    up_bytes           INTEGER
);
