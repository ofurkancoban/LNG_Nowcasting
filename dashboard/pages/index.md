---
title: LNG Nowcast Overview
---

<Grid cols=2>

<div>

# European LNG Import Nowcast

Tracks estimated LNG deliveries per terminal, detected from AIS carrier
arrivals, and validates them against GIE ALSI ground truth. See
[Backtest Accuracy](/backtest-accuracy) for fold-by-fold validation detail.

</div>

<div class="text-right text-sm text-gray-500 dark:text-gray-400 pt-2">

Data source: `marts/backtest/metrics_*.parquet`
<br/>
Pipeline: `src/lng/pipeline/orchestrate.py`

</div>

</Grid>

```sql overall_stats
select
    count(distinct terminal) as terminal_count,
    count(*) as terminal_days,
    avg(mae) as mae_gwh,
    avg(mape) as mape_pct
from marts_backtest.backtest_metrics
```

<Grid cols=4>
    <BigValue data={overall_stats} value=terminal_count title="Terminals tracked"/>
    <BigValue data={overall_stats} value=terminal_days title="Terminal-days backtested"/>
    <BigValue data={overall_stats} value=mae_gwh title="Avg. MAE" fmt="num2" description="GWh/d"/>
    <BigValue data={overall_stats} value=mape_pct title="Avg. MAPE" fmt="num1" description="%"/>
</Grid>

## Accuracy by terminal

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

<DataTable data={terminal_summary} rowShading=true>
    <Column id=terminal title="Terminal"/>
    <Column id=terminal_days title="Terminal-days"/>
    <Column id=mae_gwh title="MAE (GWh/d)" fmt="num2" contentType=colorscale colorScale=blues/>
    <Column id=mape_pct title="MAPE (%)" fmt="num1" contentType=colorscale colorScale=blues/>
</DataTable>

<Alert status="info">

This dashboard's current numbers come from a demonstration backtest run, not
a live production nowcast. See the caveat on the
[Backtest Accuracy](/backtest-accuracy) page before drawing conclusions from
these figures.

</Alert>
