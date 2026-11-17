# Performance fixtures and import benchmark (T03)

Large synthetic CityGML files for measuring freezes and memory use, and a
benchmark that records import time, phases, peak RSS, object count and the
longest main-thread block. Generated data lives **outside the repository**
(default `<repo>/../perf-data`).

| File | Runs in | Purpose |
| --- | --- | --- |
| `make_tiled_citygml.py` | Python 3.10+ (stdlib only) | Tile one CityGML 1.0/2.0/3.0 file into N buildings |
| `make_perf_sets.py` | Python | Regenerate all standard sets with one command |
| `validate_sets.py` | `blender -b` | Well-formedness (all sets) and XSD (S) with `ops/validate_auto.py` |
| `db_import_check.py` | Python + psycopg + Docker | `citydb import` into a temporary 3DCityDB v5 schema, count buildings, drop schema |
| `bench_import.py` | Python (driver) → `blender -b` (worker) | Import benchmark (`--mode browse`: T08 index/load), one Blender process per set, 15 min limit |
| `compare_results.py` | Python | Compare two benchmark result files |
| `procmem.py` | both | Peak RSS without third-party packages |

## Generator

```
python scripts/perf/make_tiled_citygml.py SRC.gml OUT.gml --count 5000 [--dx 60 --dy 70] [--gap 20]
    [--strip-envelopes] [--with-lod0] [--stats-json stats.json]
```

- Each copy of the source city model is shifted by `(i·dx, j·dy)` on a square
  grid (default step: source extent + `--gap`, rounded up to 10 m). Z is kept.
  Coordinates keep the number of decimals of the source.
- Every `gml:id` becomes `<id>_t<copy>`; `xlink:href`, appearance `target`
  (text or `uri`) and `ring` references that point at an id of the source get
  the same suffix. References to ids outside the source are left alone.
- Feature envelopes are shifted with their geometry; the CityModel envelope is
  computed for the whole output.
- `--strip-envelopes` removes every `gml:boundedBy` envelope (feature and
  CityModel): the "no envelope" case of T08.
- `--with-lod0` adds a real LoD0 footprint built from the LoD2 `GroundSurface`
  polygons (rings turned to face up): `lod0MultiSurface` in CityGML 3.0,
  `bldg:lod0FootPrint` in 2.0, at the position the XSD expects. A feature
  without a ground surface gets no LoD0. A bounding box is never written or
  labelled as LoD0.
- The source is read once with `iterparse`; each top-level member is compiled
  into a format template and every copy is written straight to the file.
  Memory depends on the source size, not the output size (~25 MB for M).

## Standard sets

```
python scripts/perf/make_perf_sets.py [--source-dir DIR] [--out DIR] [--only S M]
```

Sources: `buildings_lod{2,3}_citygml_{2_0,3_0}.gml` (Hamburg, EPSG:25832, 3 buildings
each, no LoD0) from `--source-dir`, default `$CGML_PERF_SOURCE_DIR` or
`~/Desktop/blender_test_files/buildings`. Output `--out`, default `$CGML_PERF_DATA`
or `<repo>/../perf-data`; a folder inside the repository is refused.
`<out>/manifest.json` records size, time and peak RSS of every set.

| Set | Source | Buildings | Option | Size |
| --- | --- | --- | --- | --- |
| `S` | LoD2, CityGML 3.0 | 300 | | ~6 MB |
| `M` | LoD2, CityGML 3.0 | 5,000 | | ~94 MB |
| `L` | LoD2, CityGML 3.0 | 20,000 | | ~376 MB |
| `M-lod3` | LoD3, CityGML 3.0 | 5,000 | | ~296 MB |
| `M-noenv` | LoD2, CityGML 3.0 | 5,000 | `--strip-envelopes` | ~93 MB |
| `M-lod0` | LoD2, CityGML 3.0 | 5,000 | `--with-lod0` | ~98 MB |
| `M-gml2` | LoD2, CityGML 2.0 | 5,000 | | ~90 MB |

The importer switches to its streaming path at 100 MB (`should_use_streaming`),
so `M` (~94 MB) is imported without streaming and `L` / `M-lod3` with streaming.

## Checks

Use an isolated Blender profile for every headless run (see
`tickets/README.md`, "Ajanlar için çalışma ortamı"); never the profile of a
running interactive Blender.

```
set BLENDER_USER_RESOURCES=%TEMP%\cgbl
blender -b --python scripts/perf/validate_sets.py -- ../perf-data --xsd S

rem password only in the environment
set CITYDB_PASSWORD=...
python scripts/perf/db_import_check.py ../perf-data/S.gml --expect 300
```

`db_import_check.py` needs psycopg. Without a venv, unpack the bundled wheels
(`wheels/psycopg-*`, `psycopg_binary-*-cp313-*`, `typing_extensions-*`, `tzdata-*`)
into a scratch folder with `tar -xf`, put it on `PYTHONPATH` and run the script
with Blender's Python 3.13 (`"<Blender>\5.2\python\bin\python.exe"`). Do not point
`PYTHONPATH` at a Blender profile's wheels: `--factory-startup` test runs remove them.
It creates `t03_perf_<random>` with the container's `create-schema.sql`, imports
with the compose `citydb-tool` service (`CITYDB_SCHEMA`/`CITYDB_PASSWORD` from the
environment), counts top-level `Building` rows and runs `drop-schema.sql`.

## Benchmark

```
set BLENDER_USER_RESOURCES=%TEMP%\cgbl
python scripts/perf/bench_import.py --blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --sets S
python scripts/perf/compare_results.py perf-data/results/A.json perf-data/results/B.json --markdown
```

Result: `<perf-data>/results/<date>-<branch>.json` (`--label` / `--out` to change).
Per set, in a fresh `blender -b` with an empty scene:

- `total_s`: the `cgml3.import_gml_file` call;
- `phases_s`: from the importer's progress markers. Non-streaming:
  `parse` (XML tree), `appearance`, `collect`, `build` (objects, meshes, materials),
  `post_import` (landing: origin, clip, framing, report). Streaming:
  `parse` + `setup` (root attributes), `appearance`, `stream_parse_build`
  (parse and build interleaved), `finalize`, `post_import`;
- `materials_s`: time inside `apply_materials_uvs`, i.e. the material/UV part of
  `build` (not an extra phase);
- `peak_rss_mb`: peak working set of the Blender process;
- `objects`, `mesh_objects`, `meshes`, `materials`, `polygons`;
- `longest_main_thread_block_s`: a full import is one synchronous operator
  call, so this equals `total_s`.

This benchmark measures the **full import** path. Full imports of large data
are not the goal of the series (see `tickets/README.md`, "Ana hedef"). The M/L
full-import baseline was therefore dropped; S is kept as the reference for
small files.

### Browse first (T08)

```
python scripts/perf/bench_import.py --blender "...\blender.exe" --mode browse [--sets L] [--file tile.gml ...] [--load 6]
```

Result: `<perf-data>/results/<date>-<branch>-browse.json`. Default set: L
(with only `--file`, just the files). The index is built in the worker
thread while the main thread ticks the job every 50 ms, like the modal timer
in the UI. Per file:

- `index_s`, `first_boxes_s` (first proxy mesh written), `features`, `preview`, `risks`;
- `longest_main_thread_block_s`: longest tick (draining results, writing the
  proxy mesh); `max_tick_gap_s`: largest gap between ticks (includes the 50 ms
  interval and GIL waits);
- `peak_rss_mb` (whole Blender process), `proxy_objects` (must be 1), `proxy_faces`;
- `load_*`: `--load` random buildings (seed 6) loaded with `Load LoD2`: time,
  features, bytes read, objects;
- `reopen_s` / `reopen_from_cache`: opening the same file again.

The index cache goes to a temporary folder (`CGML3_INDEX_CACHE_DIR`).
CityGML 1.0 tiles are browsed directly (no conversion needed).

A set that exceeds `--timeout-min` (default 15) is killed and reported as
`"completed": false`. Logs: `<perf-data>/results/.<set>.log`.

## Real data (optional, only with the user's approval)

The sample buildings (`DEHHALKAJ...` ids) come from Hamburg's official
**3D-Gebäudemodell LoD2-DE** (LGV, ALKIS-based, EPSG:25832, open data).
Licence: Datenlizenz Deutschland Namensnennung 2.0; attribution "Freie und
Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung (LGV)".

1. Ask the user before downloading anything. Never commit the data.
2. Download list (MetaVer, "3D-Gebäudemodell LoD2-DE Hamburg",
   <https://metaver.de/trefferanzeige?docuuid=2C1F2EEC-CF9F-4D8B-ACAC-79D8C1334D5E>).
   Release used on 2026-10-23:
   `https://daten-hamburg.de/opendata/3d_stadtmodell_lod2/LoD2-DE_HH_2026-04-28.zip`
   (629 MB; 783 tiles of 1 km × 1 km, 6.4 GB unzipped). The server answers 403
   to plain clients; `curl -L -A "Mozilla/5.0"` works.
3. Save it into its own new folder (`perf-data/real/hh2026/`) and extract only
   the tiles you need, e.g. `LoD2_32_564_5934_1_HH.gml` (the sample's area,
   460 buildings) or the largest ones (~1,300–1,900 buildings, ~28 MB each).
4. The tiles are **CityGML 1.0** and have **no feature envelopes**. The add-on's
   importer reads 2.0 and 3.0 only (a 1.0 file imports 0 features). For LoD2
   buildings, rewriting the CityGML namespaces `…/citygml/1.0`,
   `…/building/1.0`, `…/appearance/1.0`, `…/generics/1.0`,
   `…/cityobjectgroup/1.0` to their 2.0 equivalents was enough (checked with 6
   buildings); T08 does this automatically. `make_tiled_citygml.py` accepts 1.0
   sources, but `--with-lod0` is refused (1.0 has no LoD0).
5. Benchmark a converted tile next to the sets:
   `python scripts/perf/bench_import.py --blender ... --sets --file perf-data/real/<tile>_v2.gml`.
