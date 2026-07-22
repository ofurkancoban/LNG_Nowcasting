---
title: Live Vessel Tracker
---

# Live Vessel Tracker

Shows the most recently observed AIS position for every LNG carrier in
`data/reference/lng_carriers.csv` seen in the live AISStream feed
(`src/lng/ingest/aisstream.py`, running continuously on the ingestion VPS).
Positions refresh each time this dashboard is rebuilt
(`.github/workflows/nowcast-build.yml`), not on every page view — see the
note at the bottom for what "live" means here.

```sql vessel_positions
select
    mmsi,
    imo,
    vessel_name,
    latitude,
    longitude,
    received_at,
    updated_at
from marts_backtest.vessel_positions
order by vessel_name
```

<PointMap
    data={vessel_positions}
    lat="latitude"
    long="longitude"
    value="vessel_name"
    legendType="categorical"
    tooltipType="hover"
    tooltip={[
        { id: 'vessel_name', showColumnName: false, valueClass: 'text-lg font-semibold' },
        { id: 'mmsi', title: 'MMSI' },
        { id: 'imo', title: 'IMO' },
        { id: 'received_at', title: 'Last position (UTC)' }
    ]}
/>

## Currently tracked vessels

<DataTable data={vessel_positions} rowShading=true>
    <Column id=vessel_name title="Vessel"/>
    <Column id=mmsi title="MMSI"/>
    <Column id=imo title="IMO"/>
    <Column id=latitude fmt="num4"/>
    <Column id=longitude fmt="num4"/>
    <Column id=received_at title="Last position (UTC)"/>
</DataTable>

<Alert status="info">

This table only ever shows vessels from the curated 6-vessel starter
registry (`data/reference/lng_carriers.csv`, see docs/decisions/0001-architecture.md's
open question on expanding it), not all AIS traffic in range. A vessel
disappears from this list if it hasn't been matched via a `ShipStaticData`
message in the current MotherDuck `raw_aisstream` table — this is a
snapshot as of the last dashboard rebuild, not a continuously streaming
live map. AISStream itself has experienced extended service instability
this week (see docs/data_sources.md); gaps here may reflect that rather
than a vessel actually going dark.

</Alert>
