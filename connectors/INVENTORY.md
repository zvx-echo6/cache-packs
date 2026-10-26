# Connector inventory

Built 2026-10-26 from the sources of the raw-API broker (Conduit, 23 sources) and the other upstreams that the mesh assistant's adapters call (meshai). Each source was checked against the policy in README.md (official, documented API only; no scraping; no key in a definition; no personal name or e-mail anywhere) and, where it passed, written as a connector and tested.

Counts: 15 connectors ported (12 keyless, 3 need a key), 10 sources not ported (policy or not live data), 6 sources blocked by gaps in the connector format. Section 3 lists the gaps with proposals.

"Validated" means the definition was run through the `cs live-fetch` command of a release build of the Cache core against the real API for Boise and Stanley (and Alamosa, CO for alerts, because nothing was active in Idaho). "Validated: needs key" means the provider needs a key, so the definition was run against a local copy of the documented response shape (synthetic sample records made from the provider's field list, served from the test machine itself); the real API was not called.

## 1. Ported connectors

| Connector | Source (Conduit / meshai) | Provider and official docs | Auth | Data | Format | Coverage | Result |
|---|---|---|---|---|---|---|---|
| `openmeteo-forecast` | meshai (weather, ducting host); not in Conduit | Open-Meteo, https://open-meteo.com/en/docs | none | weather | JSON | worldwide | validated (Boise, Stanley). Same mapping as the weather definition that ships in the app, new id. Free API is non-commercial, CC BY 4.0; limits 600/min, 10,000/day, 300,000/month are the provider's published ones. |
| `nws-alerts-point` | `nws_alerts` | NWS, https://www.weather.gov/documentation/services-web-api | none (contact User-Agent required; the project one is used) | alerts | GeoJSON | US | validated (Alamosa: 2 alerts with distance; Boise: none active). Same mapping as the shipped alerts definition plus the project User-Agent. NWS publishes no numeric limit; the quota is a polite cap. |
| `swpc-scales` | `swpc_scales` | NOAA SWPC, https://www.swpc.noaa.gov/content/data-access | none | space weather | JSON | worldwide | validated. R/S/G scales now and the 3-day outlook. |
| `swpc-kp` | `swpc_kp` | NOAA SWPC | none | space weather | JSON | worldwide | validated. Newest first. Times are UTC text (see gap 2). |
| `swpc-alerts` | `swpc_alerts` | NOAA SWPC | none | space weather | JSON | worldwide | validated. 90 messages; the message text is one line each. |
| `swpc-f107` | `swpc_f107` | NOAA SWPC | none | space weather | JSON | worldwide | validated. |
| `usgs-quakes-near` | new, same provider as `usgs_quake` | USGS, https://earthquake.usgs.gov/fdsnws/event/1/ | none | quake | GeoJSON | worldwide | validated (Boise 3, Stanley 5 events within 300 km). Uses the documented radius query instead of the worldwide feed. No event time (gap 1). |
| `usgs-quakes-day-m25` | `usgs_quake` | USGS, https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php | none | quake | GeoJSON | worldwide | validated (32 events). Not tied to a place; kept because it is the feed the broker uses. No event time (gap 1). |
| `wfigs-incidents-near` | `wfigs_incidents` | NIFC WFIGS (ArcGIS feature service), https://data-nifc.opendata.arcgis.com/ | none | wildfire | GeoJSON (ArcGIS query) | US | validated (Boise 5, Stanley 6 fires within 150 mi). The broker used a fixed Idaho box; this uses a radius around the place. No discovery time (gap 1), no distance (gap 3). Not sorted by size (gap 9). |
| `wfigs-perimeters-near` | `wfigs_perimeters` | NIFC WFIGS | none | wildfire | GeoJSON (ArcGIS query) | US | validated (Stanley: East Fork fire edge 38 mi S). Polygons are simplified in the query (`maxAllowableOffset`) to keep the response small. |
| `usgs-water-idaho` | `usgs_iv` | USGS Water Services, https://waterservices.usgs.gov/docs/instantaneous-values/ | none | hydrology | JSON | Idaho | validated (443 gage readings). The same list for every place, in the service's order (gaps 3, 4). Uses `stateCd=ID` because the bounding-box query takes about 45 s, longer than the core's 30 s timeout. |
| `wzdx-idaho` | `wzdx_idaho` | Idaho Transportation Department, WZDx feed, https://511.idaho.gov/developers/doc | none | roads | GeoJSON (WZDx 4.1) | Idaho | validated (638 work zones, closures first). The same list for every place (gap 3). |
| `idaho511-events` | `roads511` | Idaho 511, https://511.idaho.gov/help/endpoint/event | key (query) | roads | JSON | Idaho | validated: needs key. Provider limit: 10 calls per 60 s per key; quota set to 4/min. |
| `idaho511-winter-roads` | `roads511` (same key) | Idaho 511, https://511.idaho.gov/help/endpoint/winterroads | key (query) | roads | JSON | Idaho | validated: needs key. The provider's page lists `Overall Status` with a space in one place; the definition accepts both spellings. |
| `tomtom-flow-point` | `tomtom_traffic` | TomTom, https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/traffic-flow/flow-segment-data | key (query) | traffic | JSON | worldwide | validated: needs key. Free tier: 2,500 non-tile requests a day (TomTom pricing page), quota set to that. |

## 2. Not ported, and the other sources that were looked at

| Source | What it is | Reason |
|---|---|---|
| `avalanche` (Conduit; meshai avalanche adapter) | avalanche.org zone danger ratings, GeoJSON | not ported: no official documentation found. The `api.avalanche.org/v2/public/products/map-layer` endpoint is what the avalanche.org map itself uses; only third-party wrappers describe it. The mapping is known (zone name, centre, danger level, travel advice, start and end date, polygons), so it can be added at once if the project accepts it as a public API. |
| `idaho_power` | Idaho Power outage JSON | not ported: undocumented endpoint that is blocked unless a browser-shaped User-Agent is sent (policy). |
| `meshview` | a community MeshView web instance | not ported: a third-party instance with no published API or terms, and mesh node data rather than preparedness data. |
| `tomtom_flow_tiles` | TomTom vector traffic tiles (binary PBF) | not ported: a map layer (binary tiles), not live items; belongs to the map-layer format. The point lookup is ported as `tomtom-flow-point`. |
| `wzdx_registry` | USDOT registry of state WZDx feeds (Socrata) | not ported: a directory of feeds, not end-user data. Each state feed is its own connector (Idaho done). |
| `usgs_site_info` | USGS site metadata, tab-separated text | not ported: metadata used to pick gages, not live data; text format (gap 6). |
| Watch Duty API (meshai) | fire events and evacuation reports | not ported: undocumented API of a commercial app (policy). |
| NWS AHPS gauge pages (meshai gauge import) | HTML pages read to find river gages | not ported: scraping HTML (policy). |
| wttr.in (meshai weather command) | community weather service | not ported: duplicates `openmeteo-forecast`; a volunteer service with no published limits. |
| Open-Meteo geocoding, Photon geocoder, Census gazetteer (meshai) | place lookup and a static county file | not ported: not live data; Cache has its own place search. |

Blocked by a gap in the connector format (nothing was bent to fit):

| Source | What it is | Blocked by |
|---|---|---|
| `firms` (Conduit; meshai FIRMS adapter) | NASA FIRMS satellite fire hotspots, key in the URL path, https://firms.modaps.eosdis.nasa.gov/api/ | CSV response (gap 6) and a bounding box built from the place (gap 4) |
| `ipaws` (Conduit; meshai IPAWS adapter) | FEMA IPAWS-OPEN public alerts, https://www.fema.gov/emergency-managers/practitioners/integrated-public-alert-warning-system/technology-developers | XML (Atom index plus one CAP document per alert), a two-step fetch (gaps 5, 6) |
| `celestrak_tle` (Conduit; meshai satellite passes) | CelesTrak orbital elements, https://celestrak.org/NORAD/documentation/gp-data-formats.php | text TLE format (gap 6); a pass time needs orbit calculation, which is not a mapping (gap 8) |
| `ducting` (Conduit; meshai ducting adapter) | Open-Meteo GFS pressure-level temperature and humidity, used to compute a radio ducting index | the index is arithmetic over several fields (gap 8) |
| `usgs_nwps` (Conduit) | NOAA National Water Prediction Service gauge, one request per gage id, https://api.water.noaa.gov/nwps/v1/docs/ | per-site request with a user-chosen id (gap 12) |
| HamQSL solar data (meshai config default) | solar/HF propagation summary, https://www.hamqsl.com/solar.html | XML (gap 6) |

## 3. Format gaps and proposals

Each proposal is a general feature of the format, not a per-source fix. Nothing was added to the Rust code.

1. **Epoch milliseconds.** `time.in` knows `unix` (seconds) only. USGS earthquake times, and ArcGIS dates (WFIGS discovery and containment) are milliseconds. Blocks: item times of `usgs-quakes-near`, `usgs-quakes-day-m25`, `wfigs-*`. Proposal: `time.in: "unix_ms"`.
2. **UTC times without an offset, and other text patterns.** `local` reads a time as the place's local time, but SWPC writes UTC without an offset (`2026-10-26T12:00:00`, and `2026-10-26 05:17:56.783` with a space). Blocks: proper times for `swpc-kp`, `swpc-alerts`, `swpc-f107` (shown as text now). Proposal: `time.in: "utc"` and an optional `time.pattern` (a strptime pattern) for the input.
3. **Distance and direction from points, lines and coordinate pairs.** `distance` accepts only polygon geometry. Point and line sources (earthquakes, incidents, 511 events, work zones, gage sites) cannot say "6.2 mi S", and long statewide lists cannot be filtered to what is near. Proposal: let `distance` take any GeoJSON geometry or a `{lat, lon}` pair of values, and add to an item group `near: {max: 50, unit: "mi"}` (drop items farther away) and a sort key `distance: true`. This is the biggest gap for `wzdx-idaho`, `idaho511-events`, `usgs-water-idaho`, `usgs-quakes-day-m25`.
4. **Area queries built from the place.** Some APIs only take a box (USGS bounding box, FIRMS) or a state or county code (NWS by area, 511 per state). Proposal: placeholders `{bbox:50km}` (west,south,east,north around the place), `{state}`, `{county_fips}` filled from the places geocoder. Blocks: `firms`, a place-relative USGS gage list, state-wide NWS alerts.
5. **Multi-step lookups.** NWS forecasts need `points/{lat},{lon}` first and then the forecast URL it returns; IPAWS needs an index and then each alert; NWS observations need the nearest station. Proposal: an optional `steps` list in `request`, each step an https request whose URL may use a value read from the previous response (`{prev:properties.forecast}`), limited to two or three steps and to the same host. Blocks: the NWS forecast, hourly forecast and observations (not written), `ipaws`.
6. **Non-JSON responses.** XML (IPAWS, HamQSL), CSV (FIRMS), tab-separated text (USGS site service), TLE text (CelesTrak). Proposal: `request.response: "xml" | "csv"` converted to a JSON tree before mapping (XML: attributes as `@name`, repeated elements as arrays; CSV: an array of objects keyed by the header row). TLE and RDB are specialist text and are better as their own small readers if wanted.
7. **Iterating an object.** `each` needs an array. SWPC's scales file is an object keyed by day (`"0"`, `"1"`, ...); `swpc-scales` uses four fixed item groups as a workaround. Proposal: `each` over an object gives one item per member with `@key` for the member name.
8. **Derived values.** Ducting (a refractivity gradient from temperature, humidity and pressure), speed ratios, and satellite passes need arithmetic or orbit calculation. Proposal: for arithmetic, a `calc` step with `+ - * /` over named values of the item (still pure data); satellite passes are better as a built-in helper than as a format feature.
9. **Numeric sort.** `sort` compares text, ranks or times, so "largest fire first" and "highest flow first" are not possible when the service cannot sort (the WFIGS feature service ignored `orderByFields`). Proposal: `"number": true` on a sort key.
10. **Paging.** ArcGIS queries return at most one page (`exceededTransferLimit`). A 150-mile radius rarely hits it, but a big fire season could. Proposal: `paging: {type: "arcgis", max_pages: 3}` and a similar offset/limit style for SODA and others; until then results beyond a page are silently missing.
11. **Filtering items.** No way to keep only some elements (for example work zones of one type, or non-prescribed burns where the service cannot filter). Proposal: `where: [{path, equals|not_equals|in}]` on an item group.
12. **Values the user supplies.** A gage id (NWPS, single USGS site), a NORAD id, a state code. Proposal: a `params` section in the definition (name, label, default) shown to the user when the connector is added and usable as `{param:name}` in the URL; this also lets one definition serve many gages.

## 4. Publishing (how `connectors/` reaches the tile host)

Layers already publish like this: the build workflows upload the files to the R2 bucket behind https://cache.echo6.co (`scripts/publish_layer.py` uploads the PMTiles and a definition, then merges the layer into `catalogue.json` at the bucket root; `regions/publish_regions.sh` uploads the regions index files last). The R2 secrets live in the repository's secrets and are passed to the job as environment variables.

Connectors would publish the same way, as plain static files, with no build step:

- a workflow `connectors.yml` (not added here, per instructions) triggered on changes under `connectors/` and by hand;
- it validates every definition (the core's parser, and the checks in README.md: https only, no key, no e-mail) and `catalogue.json` (every `path` exists, ids match);
- it then runs `aws s3 sync connectors/ s3://$R2_BUCKET/connectors/ --exclude "*.md" --delete --endpoint-url "$R2_ENDPOINT"` with `--content-type application/json --cache-control "public, max-age=300"`, uploading `catalogue.json` last (as `publish_regions.sh` does for its manifest), so a reader never sees a catalogue that points at a file that is not there yet;
- `base_url` in `catalogue.json` is then `https://cache.echo6.co/connectors/`, and the catalogue link a user adds in the app is `https://cache.echo6.co/connectors/catalogue.json`.

The layer `catalogue.json` at the bucket root stays separate; it lists map layers and is unchanged.

## 5. Proposed, not approved

These are the assistant's suggestions, not requests: more connectors from providers already used here (Open-Meteo air quality, flood and marine endpoints; Idaho 511 mountain passes, cameras and weather stations; work zone feeds of other states from the WZDx registry; the NWS forecast and observations once multi-step requests exist); an app screen to add a catalogue by link.
