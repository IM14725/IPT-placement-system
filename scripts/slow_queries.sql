-- Slow-query report. Needs pg_stat_statements (see scripts\postgres_tune.ps1).
-- Run:  psql -U ipt -d ipt_marketplace -f scripts\slow_queries.sql

\echo '== Top 15 by total time (where the database spends its life) =='
SELECT round(total_exec_time::numeric, 0) AS total_ms,
       calls,
       round(mean_exec_time::numeric, 2) AS mean_ms,
       round(max_exec_time::numeric, 0) AS max_ms,
       rows,
       left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 15;

\echo '== Top 10 slowest on average (>= 5 calls) =='
SELECT round(mean_exec_time::numeric, 2) AS mean_ms, calls,
       left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS query
FROM pg_stat_statements
WHERE calls >= 5 AND dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY mean_exec_time DESC
LIMIT 10;

\echo '== Tables read by sequential scan (candidates for an index) =='
SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup
FROM pg_stat_user_tables
WHERE seq_scan > 100 AND n_live_tup > 1000
ORDER BY seq_tup_read DESC
LIMIT 15;

\echo '== Indexes never used since stats reset (candidates to drop; ignore unique/PK) =='
SELECT s.relname AS table_name, s.indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS size, s.idx_scan
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary
ORDER BY pg_relation_size(s.indexrelid) DESC
LIMIT 20;

\echo '== Dead tuples / autovacuum health =='
SELECT relname, n_live_tup, n_dead_tup,
       round(100.0 * n_dead_tup / nullif(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
       last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 15;

\echo '== Connections by application/state (vs max_connections) =='
SELECT coalesce(nullif(application_name, ''), '(unnamed)') AS app, state, count(*)
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY 1, 2
ORDER BY 3 DESC;
SHOW max_connections;
