#!/usr/bin/env python3
"""Convert an OSM XML file (an OSM API download) to PBF for valhalla_build_admins. Usage: osm_to_pbf.py <in.osm> <out.osm.pbf>"""
import sys

import osmium

w = osmium.SimpleWriter(sys.argv[2])


class Copy(osmium.SimpleHandler):
    def node(self, n):
        w.add_node(n)

    def way(self, x):
        w.add_way(x)

    def relation(self, r):
        w.add_relation(r)


Copy().apply_file(sys.argv[1])
w.close()
