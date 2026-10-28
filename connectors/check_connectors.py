#!/usr/bin/env python3
"""Checks the connector catalogue before it is published (standard library only).

The checks are the rules in connectors/README.md that need no parser: the catalogue is well formed and points only at files that are here,
every definition is JSON whose id matches its catalogue entry, requests are https, and no definition holds a key or an e-mail address.
The format itself is checked by the core (`cs live-check`, and the app checks every definition with the same parser before it installs it).
"""
import hashlib
import json
import os
import re
import sys

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
KEYISH = re.compile(r"(?i)(api[_-]?key|map[_-]?key|token|secret|password)=([A-Za-z0-9]{12,})")


def urls(node):
    """Every string in a definition that looks like a request URL."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "url" and isinstance(v, str):
                yield v
            else:
                yield from urls(v)
    elif isinstance(node, list):
        for v in node:
            yield from urls(v)


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


def main(root):
    errors = []
    cat_path = os.path.join(root, "catalogue.json")
    with open(cat_path, encoding="utf-8") as f:
        cat = json.load(f)
    if cat.get("format") != 1:
        errors.append(f"catalogue format is {cat.get('format')!r}, expected 1")
    if not ID.match(str(cat.get("id", ""))):
        errors.append("catalogue id is missing or invalid")
    expected = (os.environ.get("R2_PUBLIC_URL") or "https://cache.echo6.co").rstrip("/") + "/connectors/"
    if cat.get("base_url") != expected:
        errors.append(f"base_url is {cat.get('base_url')!r}, expected {expected!r}")
    seen = set()
    listed = set()
    for e in cat.get("connectors", []):
        cid = e.get("id", "")
        if not ID.match(cid):
            errors.append(f"bad connector id {cid!r}")
            continue
        if cid in seen:
            errors.append(f"{cid}: listed twice")
        seen.add(cid)
        path = e.get("path", "")
        if path.startswith("/") or ".." in path.split("/") or "\\" in path or "://" in path:
            errors.append(f"{cid}: path {path!r} leaves the catalogue folder")
            continue
        listed.add(os.path.normpath(path))
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            errors.append(f"{cid}: {path} does not exist")
            continue
        raw = open(full, "rb").read()
        try:
            d = json.loads(raw.decode("utf-8"))
        except ValueError as x:
            errors.append(f"{cid}: {path} is not JSON ({x})")
            continue
        if d.get("id") != cid:
            errors.append(f"{cid}: the file's id is {d.get('id')!r}")
        if e.get("sha256") and hashlib.sha256(raw).hexdigest() != e["sha256"].lower():
            errors.append(f"{cid}: sha256 does not match the file")
        if e.get("needs_key") and not e.get("key_alias"):
            errors.append(f"{cid}: needs_key without key_alias")
        if bool(e.get("needs_key")) != bool(d.get("api_key")):
            errors.append(f"{cid}: needs_key does not match the definition's api_key")
        for u in urls(d):
            # a url that is only a value from an earlier step ({point:...}) is filled in by the engine, from a response of an https request
            if not (u.startswith("https://") or u.startswith("{")):
                errors.append(f"{cid}: request url {u[:60]!r} is not https")
        for s in strings(d):
            if EMAIL.search(s):
                errors.append(f"{cid}: holds an e-mail address")
            if KEYISH.search(s):
                errors.append(f"{cid}: looks like it holds a key")
    # definitions on disk that the catalogue does not list
    for dirpath, _, files in os.walk(root):
        for name in files:
            rel = os.path.normpath(os.path.relpath(os.path.join(dirpath, name), root))
            if name.endswith(".json") and rel != "catalogue.json" and rel not in listed:
                errors.append(f"{rel}: not listed in catalogue.json")
    for e in errors:
        print("error:", e)
    print(f"{len(seen)} connectors checked, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "connectors"))
