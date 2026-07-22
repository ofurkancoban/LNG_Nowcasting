# Data sources

Every claim below is tagged VERIFIED (confirmed by reading the source's own
documentation this session) or UNVERIFIED (could not be confirmed from
available documentation and must be checked empirically before relying on it).

## AISStream.io

Real time AIS data delivered over a WebSocket.

- **Auth**: API key generated from the aisstream.io web dashboard. The key is
  sent inside the JSON subscription message, not as an HTTP header. VERIFIED.
- **Connection**: `wss://stream.aisstream.io/v0/stream`. After connecting, a
  subscription message must be sent within 3 seconds:
  ```json
  {
    "APIKey": "<key>",
    "BoundingBoxes": [[[lat, lon], [lat, lon]]],
    "FiltersShipMMSI": ["<mmsi>", "..."],
    "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
  }
  ```
  VERIFIED.
- **Filtering**: multiple bounding boxes are supported (no documented limit
  other than avoiding duplicate/overlapping boxes). `FiltersShipMMSI` accepts
  at most 50 MMSI values. `FilterMessageTypes` accepts any of the ~24+
  supported AIS message type names. VERIFIED.
- **Message envelope**: every message is
  `{"MessageType": "...", "Message": {...}, "Metadata": {...}}`. `Metadata`
  carries latitude, longitude, MMSI, ship name, and a UTC timestamp regardless
  of message type. VERIFIED.
- **Message types relevant to this project**:
  - Type 1/2/3 -> `PositionReport` (position, speed, course, heading).
  - Type 5 -> `ShipStaticData`. Per the documentation summary this includes
    ship name, IMO number, call sign, dimensions, draught, destination, and
    ETA. M1 pinned a concrete field-name contract in
    `src/lng/ingest/aisstream.py::SHIP_STATIC_DATA_REQUIRED_FIELDS`
    (`ImoNumber`, `CallSign`, `ShipType`, `Dimension`,
    `MaximumStaticDraught`, `Destination`, `Eta`), enforced by a schema-drift
    test that fails loudly if any field is absent from a message. **These
    exact names are still UNVERIFIED against live traffic** — M1's fixture
    (`tests/fixtures/aisstream_sample.jsonl`) is a synthetic sample built to
    this assumed schema, not a message set captured from a real
    subscription, because no live network call was made or authorized this
    session. Before trusting these field names in production, connect once
    with the real API key in `.env` and confirm a live `ShipStaticData`
    message matches; update this section to VERIFIED (or correct the field
    names and the `SHIP_STATIC_DATA_REQUIRED_FIELDS` constant) at that point.
  - Type 24 -> `StaticDataReport`, a partial-static-data variant (name, call
    sign, type, dimensions only, no voyage data). VERIFIED to exist, exact
    fields UNVERIFIED for the same reason as above.
- **Rate limits**: documentation states a client should be provisioned for
  roughly 300 messages/second if subscribed to the entire world, and that
  subscription updates are throttled to one per second. **No explicit
  numeric cap on concurrent connections per API key, nor a documented request
  quota, was found.** UNVERIFIED — confirm directly with aisstream.io support
  or by testing before assuming any specific ceiling.
- **Reconnection / resume**: not addressed in the documentation. There is no
  documented mechanism to request missed messages after a disconnect.
  VERIFIED (as an absence): ingestion code must treat every disconnect as a
  potential gap and cannot assume the stream will backfill it.
- **Coverage**: AISStream aggregates data from a network of terrestrial AIS
  receivers (the standard model for free/low-cost AIS aggregators). This
  means coverage is strong near coastlines and busy straits, and has gaps in
  open ocean where no receiver is in range and no satellite feed is used.
  UNVERIFIED as an explicit documentation statement, but consistent with how
  every terrestrial AIS aggregator behaves; treat as a working assumption and
  confirm empirically once ingestion is live (see docs/risks.md).
- **Licence / redistribution**: not stated in the documentation reviewed.
  UNVERIFIED — do not assume redistribution rights beyond internal use until
  confirmed with aisstream.io directly.
- **Credentials**: the project's AISStream API key is stored in `.env`
  (gitignored per M0) and read via `src/lng/config.py`, never hardcoded or
  committed. The ingester process itself runs on a dedicated VPS (see M7),
  reading the key from the VPS's own environment file, not from a value
  checked into the repo.

## GIE ALSI API

The Gas Infrastructure Europe Aggregated LNG Storage Inventory platform.
Source: GIE API user manual v007 (4 October 2022), full text read this
session.

- **Base URLs**: production `https://alsi.gie.eu/api`, test environment
  `https://alsitest.gie.eu/api` (test env returns the same data as
  production). VERIFIED.
- **Auth**: free registration at alsi.gie.eu yields a personal API key,
  passed as the `x-key` HTTP header. Keys currently never expire (GIE notes
  this may change). VERIFIED.
- **Endpoints**:
  - `/api` - facility and aggregate report listing. Query params: `country`
    (2-letter code), `company` (EIC), `facility` (EIC), `type` (`eu` / `ne` /
    `ai` for EU / non-EU / additional-info aggregates), `date` (single gas
    day), `from` / `to` (date range), `page`, `size` (default 30, max 300),
    `reverse`.
  - `/api/about` and `/api/about?show=listing` - company/facility EIC code
    directory.
  - `/api/news` - service announcements, including retroactive data-correction
    notices, filterable by `?url=<id>`.
  VERIFIED.
- **Granularity and fields**: one row per facility (or aggregate) per gas
  day: `gasDayStart`, `sendOut` (GWh/d), `dtrs` (Declared Total Reference
  Send-out = send-out capacity, GWh/d), plus a per-dataset `status` flag
  (E = estimated, C = confirmed, N = no data) and an `info` field linking to
  any relevant Service Announcement. VERIFIED directly against a live API
  response on 2026-07-22 (`GET /api?type=eu`), including the `status` field
  name, which the older v007 manual left ambiguous.
  **Schema correction**: `inventory` and `dtmi` are NOT flat numeric fields
  as the v007 manual (and this project's original M4 implementation)
  assumed. Per the GIE API manual's own changelog (v009, effective January
  2024), both changed to nested objects: `{"lng": "<10^3 m3 LNG>", "gwh":
  "<energy units>"}`. `src/lng/ingest/alsi.py` was corrected to flatten
  these into `inventory_lng`/`inventory_gwh` and `dtmi_lng`/`dtmi_gwh`
  columns after this was discovered running a real ingestion.
- **Real EIC codes** (VERIFIED via `GET /api/about?show=listing` on
  2026-07-22): Rotterdam Gate Terminal is `21W0000000000079`, Zeebrugge LNG
  Terminal is `21W0000000001245`. The placeholder codes originally used in
  `tests/fixtures/alsi_sample_response.json` and
  `src/lng/pipeline/orchestrate.py`'s `FACILITY_TO_TERMINAL` mapping were
  fabricated for testing and have been replaced with these real codes.
- **Historical depth**: since 2012-01-01 or the terminal's commissioning
  date, whichever is later. Not every LSO backfilled its full history
  equally; check `/api/news` and the per-facility start date rather than
  assuming uniform coverage. VERIFIED.
- **Update cadence**: data is published once per day at 19:30 CET, with a
  second pass at 23:00 CET for operators who report late. Polling more
  frequently than daily returns no new information. VERIFIED.
- **Rate limit**: 60 requests/minute per IP. Exceeding it triggers a 60
  second "too many requests" cooldown; repeated abuse can lead to a
  permanent IP ban. VERIFIED.
- **Data revisions**: SSOs/LSOs can retroactively correct historical data at
  any time; material corrections are announced via `/api/news`. This is the
  direct reason the nowcast pipeline needs vintage-preserving storage (see
  docs/decisions/0001-architecture.md) rather than overwriting historical
  ALSI values in place. VERIFIED.
- **Licence**: free to use and repackage, provided GIE/ALSI is credited as
  the data source (minimum: name "GIE" or "GIE ALSI" alongside the data).
  VERIFIED.
- **Known gaps**: unavailability (planned/unplanned outage) reporting exists
  on the AGSI/ALSI website but is explicitly NOT exposed through the API.
  VERIFIED.

## Vessel static data (dimensions, gas capacity)

No single free, bulk-redistributable source with confirmed LNG carrier cargo
capacity (cbm) was found this session. Candidates evaluated:

- **Global Fishing Watch vessel identity dataset**
  - Access: web map, a Vessels API, R/Python packages, and a separate bulk
    Data Download Portal (portal dataset differs from what the API/map
    expose). VERIFIED.
  - Fields confirmed: length, gross tonnage, engine power, vessel type
    (40 categories, most fishing-vessel focused), flag state, gear type.
    Cargo/gas capacity in cbm is **not confirmed present** — UNVERIFIED,
    needs a direct sample pull to check before depending on it.
  - Licence: **CC BY-NC 4.0** for publicly available data products —
    non-commercial use only, attribution required. Source code/tools without
    an explicit licence default to Apache 2.0. VERIFIED. This rules GFW data
    out for any future commercialized use of the dashboard unless a separate
    commercial licence is obtained from GFW.
  - Update frequency: not stated in the page reviewed. UNVERIFIED.

- **IMO GISIS (Global Integrated Shipping Information System)**
  - Ship and company particulars are viewable by IMO number; a large part of
    GISIS is publicly browsable without login. VERIFIED (existence and
    browse access).
  - Bulk export / redistribution rights: UNVERIFIED. GISIS's terms of use
    were not reviewed in full this session; do not assume bulk scraping or
    redistribution is permitted without checking IMO's terms directly.

- **ITU MARS (Maritime mobile Access and Retrieval System)**
  - MMSI / call sign / vessel name lookup, searchable for free. VERIFIED
    (browse access).
  - Bulk download of the underlying ship-station/MMSI database is
    restricted. VERIFIED (stated as restricted), exact restriction wording
    UNVERIFIED.

- **Recommendation (open question, not decided silently)**: there are only
  on the order of ~700 LNG carriers in the global fleet. A hand-curated
  static reference table (IMO number -> name, cargo capacity cbm, build
  year, propulsion type), sourced from public vessel-particulars pages and
  cross-checked manually, may be more tractable than any bulk dataset and
  avoids the GFW non-commercial restriction. This trades ongoing
  maintenance effort for licensing safety. Needs the user's decision — see
  open questions in docs/decisions/0001-architecture.md and the milestone
  M2 file.
