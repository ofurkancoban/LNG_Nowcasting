-- The union's placeholder row guarantees at least one row is always
-- written to the cached parquet file even when backtest_metrics is
-- genuinely empty (no fold produced yet). Without it, Evidence's source
-- build writes no parquet at all for a zero-row query, which then makes
-- the site build crash trying to read a nonexistent file. Pages must
-- filter it out with `where run_id != '__no_folds_yet__'`.
select *
from backtest_metrics
union all
select
    '__no_folds_yet__' as run_id,
    'PLACEHOLDER' as terminal,
    '1970-01-01' as gas_day,
    0.0 as predicted_gwh,
    0.0 as actual_gwh,
    0.0 as abs_error_gwh,
    0.0 as mae,
    0.0 as mape,
    current_timestamp as written_at
where not exists (select 1 from backtest_metrics)
