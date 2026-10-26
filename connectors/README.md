# Cache connectors

A connector is one JSON file that tells Cache how to download one kind of live data (weather, alerts, fires, river levels, roads, space weather and so on) and how to turn the response into labelled items the model and the Home card can read. A connector is pure data: there is no code in it, so the same file works on every platform, including iOS.

This folder is the catalogue Cache publishes. Anyone can publish their own catalogue the same way (see "Publishing your own catalogue").

## Layout

```
connectors/
  catalogue.json          index of every connector in this catalogue
  <category>/<id>.json    one definition per connector
  README.md               this file
  INVENTORY.md            what was ported, what was not and why, format gaps
```

## catalogue.json

The catalogue describes itself, so the same format works for any third-party catalogue.

| Field | Meaning |
|---|---|
| `format` | Catalogue format version. This document describes `1`. An app that does not know the version must refuse the catalogue. |
| `id` | Short stable id of the catalogue (lower-case letters, digits, `-`). |
| `title` | Name shown to the user. |
| `maintainer` | The project or organisation that maintains the catalogue. A project name, never a person. |
| `homepage` | Where the user can read about it (an https URL). |
| `base_url` | https URL that every relative `path` is resolved against. It ends in `/`. |
| `updated` | Date of the last change (`YYYY-MM-DD`). |
| `connectors` | The list below. |

Each entry of `connectors`:

| Field | Meaning |
|---|---|
| `id` | Same as the `id` inside the definition. |
| `title`, `description` | Same text as in the definition (so the app can list a connector without downloading it). |
| `category` | `weather`, `alerts`, `space-weather`, `earthquakes`, `fire`, `hydrology`, `roads`, `traffic` (a catalogue may add its own). |
| `provider` | The organisation whose data it is. |
| `coverage` | `worldwide`, `US`, or a region name (`Idaho`). A region connector returns the same data for every place. |
| `needs_key` | `true` when the provider requires an API key. |
| `key_alias` | With `needs_key`: the alias the app stores the key under. |
| `default_on` | `true` only for keyless connectors that work for any place in their coverage. The app still only turns one on where its coverage fits. |
| `path` | Where the definition is, relative to `base_url`. An absolute https URL is also allowed. |
| `attribution` | The credit line to show with the data. |
| `license` | The licence or terms the data comes under. |
| `docs` | The provider's official documentation. |
| `sha256` | Optional: hex SHA-256 of the definition file, so the app can detect a changed or damaged file. |

Relative paths never contain `..` and never start with `/`; an app must reject a path that leaves `base_url`.

## Definition format (format 1)

The format is read by the `cache_live` crate (`packages/cache_search/native/cache_live/src/def.rs`) and described in `packages/cache_search/DESIGN.md`, "Live sources". In short:

```
{ "format": 1, "id": "...", "title": "...", "provider": "...",
  "description": "one line: what the model and the Home card see",
  "attribution": "credit line",
  "request": { "url": "https://...?lat={lat}&lon={lon}", "headers": { ... } },
  "api_key": { "alias": "name", "required": true },     only for keyed providers; {api_key} in url or headers
  "refresh_minutes": 30,                                 a fetch inside this time returns the stored copy
  "quota": { "per_minute": n, "per_day": n, "per_month": n },
  "validity": { "fixed_hours": 3 },                      or { "end_path": ..., "end_of_day": true }
  "require": ["path that must exist"],
  "timezone_path": "...",                                optional
  "items": [ { "each": "path to an array", "key": V, "label": V, "time": V, "ends": V,
               "fields": [ { "label": "...", ...V } ], "sort": [...], "point": {...}, "area": "path to GeoJSON" } ] }
```

Placeholders in the URL and headers: `{lat}` `{lon}` (4 decimals, `{lat:2}` for another count), `{name}`, `{place_key}`, `{api_key}`. Values are percent-encoded. Unknown keys in a definition are errors.

Rules for this catalogue:

1. Official, documented APIs only. No scraping: no HTML pages, no undocumented or internal endpoints, nothing that needs a browser-shaped User-Agent to get past a firewall. Every connector has a `docs` link in the catalogue.
2. Never put a key in a definition. A keyed provider uses `api_key` with an alias; the user supplies the value and the app keeps it in secure storage.
3. Never put a real person's name, callsign or e-mail in a definition, header or attribution. Where a provider asks for a contact in the User-Agent (NWS), use the project: `Cache (https://github.com/zvx-echo6/cache-packs)`.
4. Quotas follow the provider's published limits (or a polite cap where the provider publishes none, and the connector says which in INVENTORY.md).
5. Say what the data is in the one-line `description`, and give the provider's credit and licence in `attribution` / `license`.
6. Do not work around what the format cannot express. If a source needs XML or CSV parsing, paging, a radius computed by the app or a two-step lookup, list it as a format gap in INVENTORY.md instead of bending the definition.

## Adding a connector to this catalogue

1. Read the provider's documentation and confirm the rules above.
2. Write `connectors/<category>/<id>.json`. Start from a similar one (`wfigs-incidents-near.json` for GeoJSON with a radius query, `swpc-kp.json` for a plain list, `idaho511-events.json` for a keyed source).
3. Test it against the real API with the `cs` command line tool for a couple of places (`cs live-fetch --config <config.json> --request '{"source":"<id>","place":{"key":"boise","name":"Boise, ID","lat":43.615,"lon":-116.2023},"force":true}'`; a key goes in as `"api_key"`), and check that the labels, units, times and ordering read well. For a keyed source, test against a saved sample of the provider's documented response.
4. Add the entry to `catalogue.json`, update `updated`, and add the row to INVENTORY.md.

## Publishing your own catalogue

A catalogue is just static files, so anyone can publish one:

1. Make a git repository, or any static web host (GitHub Pages, an object-storage bucket, your own web server), that serves over https:
   - `catalogue.json` with the fields above (your own `id`, `title`, `maintainer`, `homepage`),
   - the definition files it points at.
2. Set `base_url` to the folder the files are served from (for example `https://example.org/cache-connectors/`) and keep `path` values relative to it. Serve JSON with `Content-Type: application/json` and permit cross-origin reads if you want a web viewer to work.
3. Give people the link to `catalogue.json`. The link is all a user needs.

How the app adds a catalogue (the app-side feature is not built yet; this is the contract the data is shaped for):

- The user pastes or scans the catalogue URL. The app downloads `catalogue.json`, checks `format`, and shows the title, maintainer, homepage and the list of connectors with their coverage, whether they need a key and who the data provider is.
- The user picks connectors. The app downloads each chosen definition, resolves `path` against `base_url`, validates it with the same parser the core uses (a definition that fails validation is refused with the reason), and saves it in the sources folder. Nothing runs or installs: a definition cannot do anything except describe an https request and a mapping.
- A connector that needs a key asks for the value once and keeps it in secure storage under `key_alias`. The key is never part of the catalogue or the definition.
- The app remembers the catalogue URL, and checks `updated` (and `sha256` when present) to offer updates. A removed connector is removed from the list on the next refresh but stays installed until the user deletes it.
- Because a definition is data only, nothing in it executes, so the same rules apply on iOS as everywhere else.

## How this catalogue is published

The Cache project publishes the files under `connectors/` to its tile host as `https://cache.echo6.co/connectors/` (so `base_url` is `https://cache.echo6.co/connectors/`), the same way it publishes layer definitions and `catalogue.json` for map layers. See INVENTORY.md, "Publishing", for the exact steps; nothing is published from this branch.
