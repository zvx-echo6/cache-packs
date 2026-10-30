# Connector inventory

Built 2026-10-26 from the sources of the raw-API broker (Conduit, 23 sources) and the other upstreams that the assistant's adapters call. Each source was checked against the policy in README.md (official, documented API only; no scraping; no key in a definition; no personal name or e-mail anywhere) and, where it passed, written as a connector and tested. Revised the same day for connector format 2 (the live-source format now covers XML, CSV, paging, multi-step requests, POST, Basic auth, distance filtering and the rest of the gaps this file first listed).

Counts: **22 connectors** (21 until 2026-10-30, when `ipaws-alerts-near` was added; the counts after this sentence are of the first 21): 17 keyless and 4 that need a key (`idaho511-events`, `idaho511-winter-roads`, `tomtom-flow-point`, `firms-viirs-near`). Of the 21, 15 were ported in the first pass (12 keyless, 3 keyed; 11 of them updated to format 2 below) and 6 are new in this pass (`nws-forecast-point`, `nws-observations-latest`, `nwps-gauges-near`, `nwps-gauge-forecast`, `avalanche-forecast-zones`, `firms-viirs-near`). Not added: 3 held (2 for ham radio, 1 for provider access), 2 out of scope (mesh), 8 not ported (policy or not live data). Section 3 lists the 12 format gaps and how each was closed.

"Validated" means the definition was run with `cs live-preview` of a release-equivalent build of the Cache core against the real API for Boise or Stanley (and Ketchum for avalanche zones). "Validated: needs key" means the provider needs a key, so the definition was run against a local copy of the documented response shape served from the test machine itself (the request the core made was checked: key, box, path); the real API was not called with a key.

## 1. Connectors

| Connector | Source (Conduit / meshai) | Provider and official docs | Auth | Data | Format | Coverage | Result |
|---|---|---|---|---|---|---|---|
| `openmeteo-forecast` | not in Conduit | Open-Meteo, https://open-meteo.com/en/docs | none | weather | JSON | worldwide | validated (Boise, Stanley). Same mapping as the weather definition that ships in the app, new id. Format 1. Free API is non-commercial, CC BY 4.0; limits 600/min, 10,000/day, 300,000/month are the provider's published ones. |
| `nws-forecast-point` | new | NWS, https://www.weather.gov/documentation/services-web-api | none (project User-Agent) | weather | GeoJSON, 2 steps | US | validated (Boise: 14 periods). `points/{lat},{lon}` then the forecast URL it returns. NWS publishes no numeric limit; the quota is a polite cap. |
| `nws-observations-latest` | new | NWS (same docs) | none | weather | GeoJSON, 3 steps | US | validated (Boise: KBOI and KMAN). Point, station list, then the latest report of the 3 nearest stations (fan-out), reports older than 12 hours left out. |
| `nws-alerts-point` | `nws_alerts` | NWS (same docs) | none | alerts | GeoJSON | US | validated (Alamosa: 2 alerts with distance; Boise: none active). Format 1, unchanged. |
| `ipaws-alerts-near` | `ipaws` (meshai IPAWS adapter; knowledge of the endpoint only, no code) | FEMA IPAWS-OPEN public EAS feed, `https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/feed` then `/rest/eas/<id>` (FEMA's page on the IPAWS feeds: https://www.fema.gov/emergency-managers/practitioners/integrated-public-alert-warning-system/technology-developers/all-hazards-information-feed) | none (project User-Agent) | alerts | Atom index + CAP 1.2 XML, 3 steps, **format 3** | US | **Public, undocumented; accepted by the project owner 2026-10-29** as an exception (FEMA's Developer registration is for government organisations only). Steps: the county the place is in (NWS `zones?type=county&point=`, kept 24 h), the national feed (every 10 minutes), each entry's CAP document (kept 24 h, so a document is downloaded once; an expired entry's 403 is remembered). Items: `Actual`, `Public`, `Alert` or `Update` messages whose polygon or circle contains the place (a region: overlaps its area) or whose SAME county code is the place's county. NWS warnings that IPAWS relays are kept (the app shows one pop-up per alert). Fields: Issued by (CAP `senderName`, as sent), Event, Severity, Area, Expires, Instructions (CAP `instruction`, only when the alert has one), Details (CAP `description`, only when there is no instruction: the model relays "Instructions" as official instructions); Severity and Expires as `nws-alerts-point` writes them. The feed sends no ETag or Last-Modified (checked 2026-10-30), so conditional requests are not possible; polling is every 10 minutes per watched place, only while the source is on. Tested on recorded real messages (core test `tests/ipaws.rs`); live feed read 2026-10-30 (three entries, all expired). |
| `swpc-scales` | `swpc_scales` | NOAA SWPC, https://www.swpc.noaa.gov/content/data-access | none | space weather | JSON | worldwide | validated. One item group over the object (`@key`), with UTC times (was four fixed groups). |
| `swpc-kp` | `swpc_kp` | NOAA SWPC | none | space weather | JSON | worldwide | validated. Times are UTC text read as UTC and shown in the place's offset; newest 24. |
| `swpc-alerts` | `swpc_alerts` | NOAA SWPC | none | space weather | JSON | worldwide | validated. Newest 15, issue time as a real time. |
| `swpc-f107` | `swpc_f107` | NOAA SWPC | none | space weather | JSON | worldwide | validated. Newest 10, measured time as a real time. |
| `usgs-quakes-near` | new, same provider as `usgs_quake` | USGS, https://earthquake.usgs.gov/fdsnws/event/1/ | none | quake | GeoJSON | worldwide | validated (Boise 3, Stanley 5 events). Radius and smallest magnitude are parameters; event time (epoch ms) and distance/direction added. |
| `usgs-quakes-day-m25` | `usgs_quake` | USGS, https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php | none | quake | GeoJSON | worldwide | validated (Boise: none within 500 mi today; a place in California: 1 event 107 mi away). The worldwide feed is now filtered to the place (radius parameter, default 500 mi), nearest first. |
| `wfigs-incidents-near` | `wfigs_incidents` | NIFC WFIGS (ArcGIS feature service), https://data-nifc.opendata.arcgis.com/ | none | wildfire | GeoJSON (ArcGIS query) | US | validated (Stanley 6 fires within 150 mi, 60 within 500 mi; paging checked live with a page size of 25: 3 requests, 60 items, and the truncated flag when capped at 2 pages). Largest first, discovery time (epoch ms), distance. |
| `wfigs-perimeters-near` | `wfigs_perimeters` | NIFC WFIGS | none | wildfire | GeoJSON (ArcGIS query) | US | validated (Stanley: East Fork fire edge 38 mi S). Paged, nearest edge first. |
| `firms-viirs-near` | `firms` | NASA FIRMS, https://firms.modaps.eosdis.nasa.gov/api/area/ | key (in the path) | wildfire | CSV | worldwide | validated: needs key. `area/csv/{key}/{satellite}/{west,south,east,north}/{days}`; the request was checked (key in path, 100 km box around the place, 1 day); records from the provider's documented VIIRS columns. Provider limit: 5,000 transactions per 10 minutes per key; quota set far below that. |
| `avalanche-forecast-zones` | `avalanche` | avalanche.org map layer, https://api.avalanche.org/v2/public/products/map-layer | none | avalanche | GeoJSON | US | validated live for Ketchum and Stanley (Sawtooth, Galena Summit, Soldier and Wood River Valley, Banner Summit zones); all zones are "no rating" today (early season), so the rated mapping was checked against a copy of the real response with one zone set to moderate. **Public, undocumented; accepted by the project owner 2026-10-26** (the only connector that is not an officially documented API). Project User-Agent. |
| `usgs-water-idaho` | `usgs_iv` | USGS Water Services, https://waterservices.usgs.gov/docs/instantaneous-values/ | none | hydrology | JSON | Idaho | validated (Boise: the 30 nearest of 443 readings, gages that stopped reporting more than 48 hours ago left out). Distance filter and sort; 90 s timeout. |
| `nwps-gauges-near` | new (`usgs_nwps` in Conduit is one gage per request) | NOAA NWPS, https://api.water.noaa.gov/nwps/v1/docs/ | none | hydrology | JSON | US | validated (Boise: 15 gauges within 40 mi). Bounding box from the place; latest observed, next forecast and flood category; -999 "no reading" values left out. |
| `nwps-gauge-forecast` | `usgs_nwps` | NOAA NWPS (same docs) | none | hydrology | JSON | US | validated (gauge BIGI1). One gauge by id (parameter): latest valid reading and the next 20 forecast values. |
| `wzdx-idaho` | `wzdx_idaho` | Idaho Transportation Department, WZDx feed, https://511.idaho.gov/developers/doc | none | roads | GeoJSON (WZDx 4.1) | Idaho | validated (Boise: 30 nearest of 638 work zones; closures first then nearest, 2 MB feed). |
| `idaho511-events` | `roads511` | Idaho 511, https://511.idaho.gov/help/endpoint/event | key (query) | roads | JSON | Idaho | validated: needs key (request checked; 4 sample events: full closure first, the far one dropped). Provider limit: 10 calls per 60 s per key; quota set to 4/min. |
| `idaho511-winter-roads` | `roads511` (same key) | Idaho 511, https://511.idaho.gov/help/endpoint/winterroads | key (query) | roads | JSON | Idaho | validated: needs key. Format 1, unchanged (the records carry no coordinates, so there is nothing to filter by distance). |
| `tomtom-flow-point` | `tomtom_traffic` | TomTom, https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/traffic-flow/flow-segment-data | key (query) | traffic | JSON | worldwide | validated: needs key. Format 1, unchanged. Free tier: 20,000 Traffic Flow requests a month, no daily limit (TomTom pricing page, checked 2026-10-29); the quota is 60/min, 650/day (about a month spread evenly) and 20,000 per 30 days. |

## 2. Not added

Held:

| Source | What it is | Why held |
|---|---|---|
| HamQSL solar data (meshai config default) | solar/HF propagation summary, https://www.hamqsl.com/solar.html (XML) | held: ham radio, ask the project owner. The format can read it now (XML). |
| `celestrak_tle` (Conduit; meshai satellite passes) | CelesTrak orbital elements, https://celestrak.org/NORAD/documentation/gp-data-formats.php | held: ham radio, ask the project owner. The TLE text reader exists in the format (`"response": "tle"`); pass prediction is orbit calculation, which the format does not do. |
| `ipaws` (Conduit; meshai IPAWS adapter) | FEMA IPAWS-OPEN public alerts | no longer held: added as `ipaws-alerts-near` (section 1) under the project owner's exception of 2026-10-29. The OpenFEMA dataset `IpawsArchivedAlerts` (24-hour delay) stays unused for live alerts; one of its messages is a test sample. |

Out of scope: Cache is not a mesh tool.

| Source | What it is | Reason |
|---|---|---|
| `meshview` | a community MeshView web instance | out of scope: Cache is not a mesh tool |
| `ducting` | Open-Meteo GFS pressure-level data used to compute a radio ducting index | out of scope: Cache is not a mesh tool |

Not ported (policy, or not live data):

| Source | What it is | Reason |
|---|---|---|
| `idaho_power` | Idaho Power outage JSON | undocumented endpoint that is blocked unless a browser-shaped User-Agent is sent (policy). |
| `tomtom_flow_tiles` | TomTom vector traffic tiles (binary PBF) | a map layer (binary tiles), not live items; belongs to the map-layer format. The point lookup is ported as `tomtom-flow-point`. |
| `wzdx_registry` | USDOT registry of state WZDx feeds (Socrata) | a directory of feeds, not end-user data. Each state feed is its own connector (Idaho done). |
| `usgs_site_info` | USGS site metadata, tab-separated text | metadata used to pick gages, not live data (the format reads RDB/TSV now). |
| Watch Duty API (meshai) | fire events and evacuation reports | undocumented API of a commercial app (policy). |
| NWS AHPS gauge pages (meshai gauge import) | HTML pages read to find river gages | scraping HTML (policy); `nwps-gauges-near` replaces it with the documented API. |
| wttr.in (meshai weather command) | community weather service | duplicates `openmeteo-forecast`; a volunteer service with no published limits. |
| Open-Meteo geocoding, Photon geocoder, Census gazetteer (meshai) | place lookup and a static county file | not live data; Cache has its own place search. |

## 3. The format gaps of the first pass, and how each was closed

Each is a general feature of the format (`cache_live`, format 2), not a per-source fix, with an offline test. Reference with an example of each: DESIGN.md, "Live sources". Tests are in `packages/cache_search/native/cache_live` (`src/*` unit tests and `tests/format2.rs`).

| # | Gap | Now | Test | Used by |
|---|---|---|---|---|
| 1 | Epoch milliseconds | `time.in: "unix_ms"` | `map::tests::time_inputs_ms_utc_pattern`, `format2::near_a_point_with_unix_ms...` | quakes, WFIGS incidents |
| 2 | UTC text without an offset, other patterns | `time.in: "utc"` (T or space, fractions, `Z`), `"rfc2822"`, `"pattern"` + `assume` | `map::tests::time_inputs_ms_utc_pattern` | SWPC x4, FIRMS |
| 3 | Distance from points, lines, pairs; near filter; distance sort | `distance` on any GeoJSON geometry or `{lat,lon}`; `near`; sort `distance: true`; `limit` | `geo::tests`, `map::tests::distance_from_points_lines_and_pairs`, `map::tests::where_near_sort_limit_and_each_object` | quakes, WFIGS, 511, WZDx, gages, FIRMS, avalanche |
| 4 | Area queries from the place | `{bbox:50km}`, `{west:..}`..`{north:..}`, `{state}`, `{county}`, `{county_fips}`, `{country}`, `{latlon}`, `{lat_abs}{lat_hem}`, `{now-24h:unix}` | `request::tests::placeholders`, `format2::csv_with_the_key_in_the_path...`, `format2::parameters_make_separate_rows...` | FIRMS, NWPS |
| 5 | Multi-step lookups | `request.steps`, `{step:path|raw}`, `for_each` with `limit` | `format2::multi_step_forecast_then_a_fan_out_of_stations`, `format2::xml_index_then_one_cap_document...` | NWS forecast and observations |
| 6 | XML, CSV, TSV, TLE | `response: xml|csv|tsv|tle|auto`, `table`, `xml`; gzip undone | `convert::tests`, `format2::gzip_tsv_with_comments_and_tle_responses` | FIRMS |
| 7 | `each` over an object | `@key` | `map::tests::where_near_sort_limit_and_each_object` | SWPC scales |
| 8 | Derived values | `calc` (add, sub, mul, div, min, max) with `round` | `map::tests::calc_and_param` | (none yet; the ducting index that needed it is out of scope) |
| 9 | Numeric sort | sort key `number: true` | `map::tests::where_near_sort_limit_and_each_object`, `format2::near_a_point...` | WFIGS (largest first), quakes |
| 10 | Paging | `paging`: offset, page, cursor, next_link, hard cap, `truncated` flag | `format2::paging_by_offset_page_cursor_and_next_link_with_a_hard_cap` | WFIGS x2 |
| 11 | Item filter | `where`: equals, not_equals, in, not_in, gt, ge, lt, le, contains, starts_with, exists, `or`, on mapped or raw values | `map::tests::where_near_sort_limit_and_each_object` | FIRMS, gages |
| 12 | Values the user supplies | `params` with types, defaults and limits, `{param:name}`, `{"param": name}` | `params::tests`, `format2::parameters_make_separate_rows...` | radius on 10 connectors, NWPS gauge id |

Also added while closing them (all general, all tested): auth (key in the query, a header with any name and prefix, the path, HTTP Basic: `format2::basic_auth_bearer_header_and_a_rejected_login`), POST with a JSON, form or text body (`format2::post_with_a_json_body_and_a_form_body`), static headers, a per-request timeout (default 30 s, up to 120) and a size cap per definition (default 8 MiB, up to 32 MiB) (`format2::size_cap_is_per_definition_and_the_timeout_too`), `Retry-After` and 429 handling inside the quotas (`format2::rate_limits_are_honoured_inside_the_quota`), upstream 4xx/5xx recorded with a clear message while the last good copy stays (`format2::upstream_errors_are_recorded...`), `after_last`, `min_value`/`max_value`, `time.not_before` and `where.max_age_hours` (for providers' "no reading" and "no time" values and for stale stations), and the builder's tools: `cs live-check`, `cs live-preview`, `cs_live_preview`, the JSON Schema (`format2::preview_shows_the_raw_tree...`, `format2::errors_name_the_field...`, `schema::tests::committed_schema_is_current`).

Still not expressible, and why: OAuth or any login flow (not in the format on purpose); satellite pass prediction and anything that is orbit calculation (not a mapping); radio-propagation indices (out of scope here, though `calc` could do the arithmetic); IPAWS live alerts (access, not format); a `where` that compares two response values with each other (comparisons are against a literal, a parameter or the fetch time only).

## 4. Publishing (how `connectors/` reaches the tile host)

Layers already publish like this: the build workflows upload the files to the R2 bucket behind https://cache.echo6.co (`scripts/publish_layer.py` uploads the PMTiles and a definition, then merges the layer into `catalogue.json` at the bucket root; `regions/publish_regions.sh` uploads the regions index files last). The R2 secrets live in the repository's secrets and are passed to the job as environment variables.

Connectors would publish the same way, as plain static files, with no build step:

- a workflow `connectors.yml` (not added here, per instructions) triggered on changes under `connectors/` and by hand;
- it validates every definition (the core's parser, and the checks in README.md: https only, no key, no e-mail) and `catalogue.json` (every `path` exists, ids match);
- it then runs `aws s3 sync connectors/ s3://$R2_BUCKET/connectors/ --exclude "*.md" --delete --endpoint-url "$R2_ENDPOINT"` with `--content-type application/json --cache-control "public, max-age=300"`, uploading `catalogue.json` last (as `publish_regions.sh` does for its manifest), so a reader never sees a catalogue that points at a file that is not there yet;
- `base_url` in `catalogue.json` is then `https://cache.echo6.co/connectors/`, and the catalogue link a user adds in the app is `https://cache.echo6.co/connectors/catalogue.json`.

The layer `catalogue.json` at the bucket root stays separate; it lists map layers and is unchanged.

## 5. Proposed, not approved

These are the assistant's suggestions, not requests: the NWS hourly forecast and NWS gridpoint data (one more step each); the avalanche.org per-zone forecast product (bottom line, avalanche problems; two steps per zone) once the season has started; a USGS bounding-box gage connector for the whole US (the bounding-box query takes about 45 s, which the new 120 s timeout allows); more connectors from providers already used here (Open-Meteo air quality, flood and marine endpoints; Idaho 511 mountain passes, cameras and weather stations; work zone feeds of other states from the WZDx registry); an app screen to add a catalogue by link.
