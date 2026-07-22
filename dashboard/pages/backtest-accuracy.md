---
title: Backtest Accuracy
---

# Backtest Accuracy

Walk-forward backtest results comparing the nowcast model
(`src/lng/nowcast/model.py`) against vintaged GIE ALSI ground truth
(`src/lng/nowcast/backtest.py`), per docs/milestones/M5.md. Every fold here
was scored only against the ALSI vintage that existed as of its prediction
date; no fold uses a later, hindsight-corrected ALSI revision (ADR 0001,
Decision 4).

```sql fold_detail
select
    run_id,
    terminal,
    gas_day,
    predicted_gwh,
    actual_gwh,
    abs_error_gwh,
    mae,
    mape
from marts_backtest.backtest_metrics
order by terminal, gas_day
```

<DataTable data={fold_detail} rows=20>
    <Column id=run_id/>
    <Column id=terminal/>
    <Column id=gas_day/>
    <Column id=predicted_gwh title="Predicted (GWh/d)" fmt="num2"/>
    <Column id=actual_gwh title="Actual (GWh/d)" fmt="num2"/>
    <Column id=abs_error_gwh title="Abs. error (GWh/d)" fmt="num2"/>
    <Column id=mae title="MAE (aggregate)" fmt="num2"/>
    <Column id=mape title="MAPE % (aggregate)" fmt="num1"/>
</DataTable>

```sql daily_error
select
    gas_day,
    terminal,
    abs_error_gwh
from marts_backtest.backtest_metrics
order by gas_day
```

<LineChart
    data={daily_error}
    x=gas_day
    y=abs_error_gwh
    series=terminal
    title="Absolute backtest error by gas day"
/>

<Alert status="info">
The nowcast model's cargo-to-energy conversion factor
(`APPROXIMATE_GWH_PER_CBM` in `src/lng/nowcast/model.py`) is an
uncalibrated approximation. Treat MAE/MAPE here as evidence the backtest
harness works end to end, not as validated production accuracy.
</Alert>
