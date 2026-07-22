-- Path is resolved relative to dashboard/ (the Evidence project root) at
-- query-execution time, not relative to this file's own directory.
select *
from read_parquet('../marts/backtest/metrics_*.parquet')
