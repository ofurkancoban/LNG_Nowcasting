---
title: LNG Nowcast Overview
---

Estimated LNG deliveries per terminal, detected from AIS carrier movements
and validated against official GIE ALSI data. See
[Backtest Accuracy](/backtest-accuracy) for details, or the
[Live Vessel Tracker](/tracker) for current vessel positions.

```sql overall_stats
select
    count(distinct terminal) as terminal_count,
    count(*) as terminal_days,
    avg(mae) as mae_gwh,
    avg(mape) as mape_pct
from marts_backtest.backtest_metrics
where run_id != '__no_folds_yet__'
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
where run_id != '__no_folds_yet__'
group by terminal
order by terminal
```

<DataTable data={terminal_summary} rowShading=true>
    <Column id=terminal title="Terminal"/>
    <Column id=terminal_days title="Terminal-days"/>
    <Column id=mae_gwh title="MAE (GWh/d)" fmt="num2" contentType=colorscale colorScale=blues/>
    <Column id=mape_pct title="MAPE (%)" fmt="num1" contentType=colorscale colorScale=blues/>
</DataTable>
