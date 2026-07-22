---
title: Live Vessel Tracker
---

Most recently observed position for each tracked LNG carrier.

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
    <Column id=latitude title="Latitude" fmt="num4"/>
    <Column id=longitude title="Longitude" fmt="num4"/>
    <Column id=received_at title="Last position (UTC)"/>
</DataTable>
