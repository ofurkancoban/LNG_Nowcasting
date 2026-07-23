---
title: Backtest Accuracy
---

How well the nowcast model predicts LNG deliveries, compared day by day
against official GIE ALSI figures.

```sql summary
select
    count(*) as n_folds,
    avg(mae) as mae_gwh,
    avg(mape) as mape_pct,
    max(abs_error_gwh) as worst_error_gwh
from marts_backtest.backtest_metrics
where run_id != '__no_folds_yet__'
```

<Grid cols=4>
    <BigValue data={summary} value=n_folds title="Folds scored"/>
    <BigValue data={summary} value=mae_gwh title="MAE" fmt="num2" description="GWh/d"/>
    <BigValue data={summary} value=mape_pct title="MAPE" fmt="num1" description="%"/>
    <BigValue data={summary} value=worst_error_gwh title="Worst single-day error" fmt="num2" description="GWh/d"/>
</Grid>

## Daily absolute error

```sql daily_error
select
    gas_day,
    terminal,
    abs_error_gwh
from marts_backtest.backtest_metrics
where run_id != '__no_folds_yet__'
order by gas_day
```

<LineChart
    data={daily_error}
    x=gas_day
    y=abs_error_gwh
    series=terminal
    title="Absolute backtest error by gas day"
    yAxisTitle="Abs. error (GWh/d)"
/>

## Fold detail

```sql fold_detail
select
    run_id,
    terminal,
    gas_day,
    predicted_gwh,
    actual_gwh,
    abs_error_gwh
from marts_backtest.backtest_metrics
where run_id != '__no_folds_yet__'
order by terminal, gas_day
```

<DataTable data={fold_detail} rows=20 rowShading=true search=true>
    <Column id=run_id title="Run"/>
    <Column id=terminal title="Terminal"/>
    <Column id=gas_day title="Gas day"/>
    <Column id=predicted_gwh title="Predicted (GWh/d)" fmt="num2"/>
    <Column id=actual_gwh title="Actual (GWh/d)" fmt="num2"/>
    <Column id=abs_error_gwh title="Abs. error (GWh/d)" fmt="num2" contentType=colorscale colorScale=reds/>
</DataTable>
