#!/usr/bin/env python3
"""Apply our corrections to the published OpenStreetMapSpeeds table (default_speeds.json) and write the table the build uses.

Usage: merge_speeds.py <published.json> <corrections.json> <out.json>
corrections.json maps "<country>.<state>" (an entry the published table must have) to density -> field -> value; array
fields (way, link_exiting, link_turning, roundabout) take {road class: km/h}. null keeps Valhalla's own speed.
"""
import json
import sys

RC = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service_other"]

pub, cor = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
by = {cs.get("iso3166-1", "") + ("." + cs["iso3166-2"] if "iso3166-2" in cs else ""): cs for cs in pub}
for code, densities in cor.items():
    if code.startswith("_"):
        continue
    for density, fields in densities.items():
        for field, value in fields.items():
            if isinstance(value, dict):
                for rc, speed in value.items():
                    by[code][density][field][RC.index(rc)] = speed
            else:
                by[code][density][field] = value
json.dump(pub, open(sys.argv[3], "w"), separators=(",", ":"))
