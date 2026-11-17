"""Mirror the http schemas that the xmllint wrappers import, for offline xmllint.

libxml2 >= 2.14 has no built-in HTTP client, so ``xmllint --schema
xmllint_v2/wrapper_v2.xsd`` cannot fetch ``http://schemas.opengis.net/...``
and fails to compile the schema (exit 5). This tool follows every
xs:import/xs:include from both wrappers, downloads the remote ones into
``<cache_dir>`` once, and writes ``<cache_dir>/catalog.xml`` rewriting those
URLs to the local copies. cac_harness.xmllint() chains that catalog before
the committed one.

    python scripts/headless/cac_schema_cache.py <cache_dir>

Plain Python, no Blender. Needs network once.
"""
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HEADLESS_DIR = Path(__file__).resolve().parent
WRAPPERS = [HEADLESS_DIR / "xmllint_v2" / "wrapper_v2.xsd", HEADLESS_DIR / "xmllint_v3" / "wrapper_v3.xsd"]
XS = "{http://www.w3.org/2001/XMLSchema}"


def local_path(cache_dir, url):
    parts = urllib.parse.urlsplit(url)
    return cache_dir / parts.netloc / parts.path.lstrip("/")


def fetch(url, target):
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "cac-schema-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            target.write_bytes(resp.read())
        print(f"fetched {url}")
    return target


def references(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise SystemExit(f"cannot parse {path}: {exc}")
    for tag in ("import", "include", "redefine"):
        for elem in root.iter(XS + tag):
            loc = elem.get("schemaLocation")
            if loc:
                yield loc


def mirror(cache_dir):
    cache_dir = Path(cache_dir).resolve()
    hosts = set()
    seen = set()
    # (document location as URL or file path, its local file)
    queue = [(w.as_uri(), w) for w in WRAPPERS]
    while queue:
        base_url, path = queue.pop()
        if base_url in seen:
            continue
        seen.add(base_url)
        for loc in references(path):
            url = urllib.parse.urljoin(base_url, loc)
            scheme = urllib.parse.urlsplit(url).scheme
            if scheme in ("http", "https"):
                hosts.add(f"{scheme}://{urllib.parse.urlsplit(url).netloc}/")
                target = fetch(url, local_path(cache_dir, url))
            elif scheme == "file":
                target = Path(urllib.request.url2pathname(urllib.parse.urlsplit(url).path))
                if not target.is_file():
                    continue  # the committed catalog.xml redirects these (e.g. xAL)
            else:
                continue
            queue.append((url, target))

    lines = ['<?xml version="1.0"?>',
             '<catalog xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">']
    for host in sorted(hosts):
        netloc = urllib.parse.urlsplit(host).netloc
        prefix = (cache_dir / netloc).as_uri() + "/"
        lines.append(f'  <rewriteURI uriStartString="{host}" rewritePrefix="{prefix}"/>')
        lines.append(f'  <rewriteSystem systemIdStartString="{host}" rewritePrefix="{prefix}"/>')
    lines.append("</catalog>")
    catalog = cache_dir / "catalog.xml"
    catalog.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(seen)} schema documents, catalog: {catalog}")
    return catalog


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    mirror(sys.argv[1])
