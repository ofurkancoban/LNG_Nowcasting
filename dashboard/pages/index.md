---
title: LNG Nowcast Overview
---

# European LNG Import Nowcast

This dashboard tracks the pipeline's estimated LNG deliveries per terminal
and how they compare to GIE ALSI ground truth. See the
[Backtest Accuracy](/backtest-accuracy) page for validation results.

```sql terminal_summary
select
    terminal,
    count(*) as terminal_days,
    avg(mae) as mae_gwh,
    avg(mape) as mape_pct
from marts_backtest.backtest_metrics
group by terminal
order by terminal
```

<DataTable data={terminal_summary}>
    <Column id=terminal/>
    <Column id=terminal_days title="Terminal-days backtested"/>
    <Column id=mae_gwh title="MAE (GWh/d)" fmt="num2"/>
    <Column id=mape_pct title="MAPE (%)" fmt="num1"/>
</DataTable>

Backtest accuracy metrics are sourced from `marts/backtest/metrics_*.parquet`,
produced by the M5 walk-forward backtest harness
(`src/lng/nowcast/backtest.py`). This page's data depends entirely on that
artifact existing and passing schema validation; see
`scripts/validate_marts_schema.py`, which runs before every dashboard build.
