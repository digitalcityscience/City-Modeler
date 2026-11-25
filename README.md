# City Modeler for Blender

**City Modeler** is a Blender add-on for modelling semantic 3D city models: draw and edit buildings, give their surfaces and parts meaning, and keep them in sync with a **3DCityDB 5.x** database. Files are read and written as **CityGML 2.0 / 3.0** and **CityJSON 2.0** (CityJSON 1.1 and CityJSONSeq are read as well).
Developed and maintained by [Mehmet Akif Ortak](https://github.com/orttak) as part of research work at **HafenCity University Hamburg (HCU)** within the **Next Generation City Network (NGCN)** project.

## Features

- CityGML 2.0 and 3.0 import/export (`.gml`, `.xml`) with textures, materials and semantics
- CityJSON 2.0 / 1.1 and CityJSONSeq open (`.city.json`, `.city.jsonl`); export as CityJSON 2.0 or CityJSONSeq; changes can be saved back into the CityJSON file they came from (see [File formats](#file-formats-how-citygml-and-cityjson-are-handled))
- Large files open as a preview first: outlines only, pick the buildings you need, then load them
- 3DCityDB 5.x import/export via the bundled `citydb-tool` (3DCityDB 4.x is not supported)
- Drawing tools for buildings: footprint and height, roofs (AdV roof types), windows, doors, balconies, chimneys, Copy / Paste
- Semantics: assign surface classes (roof, wall, ground …), attribute card with code lists, building parts and installations
- Focus: Building, Transportation (road, railway, track, square) and CityFurniture. Interiors and the other modules are 3DCityDB 5.x work
- Filters (BBox, GML-ID, LoD, feature type), XSD validation, geometry check with a findings list and repairs, optional CityDoctor check
- Headless CLI operation (`scripts/headless/`)

## Requirements

- Blender 5.0+
- Windows, Linux (x86_64) or macOS
- For 3DCityDB only:
  - a running **3DCityDB 5.x** database (PostgreSQL/PostGIS), see the [3DCityDB documentation](https://docs.3dcitydb.org/)
  - **Java 17+** for the bundled `citydb-tool` (found through `JAVA_HOME` or `PATH`), or **Docker** instead: tick **use Docker** in the add-on preferences
- Python 3 and Blender on the `PATH`, only to build the ZIP from source

File import/export and the drawing tools need nothing else; all Python packages ship inside the ZIP. On Linux the database password is not kept in a keychain: enter it per session or use `~/.pgpass`.

## Installation

1. Get the extension ZIP (it contains `blender_manifest.toml` and `wheels/`). There is no public release download yet, so build it from the source folder:

   ```bash
   python scripts/fetch_deps.py --python-only
   blender --command extension build --source-dir . --output-filepath city_modeler.zip
   ```

   The first command downloads the Python wheels into `wheels/` (not in git).
2. In Blender: **Edit → Preferences → Add-ons**, open the **⌄** menu at the top right, choose **Install from Disk…** and select the ZIP. Dragging the ZIP into the Blender window works too.
3. Enable **City Modeler**. It has two places:
   - **Top strip (geometry):** the row above the 3D view holds everything that draws or changes geometry. New Building, From Footprint, Roof, Edit Facade, Window, Door, Balcony, Chimney, Copy / Paste and Face Wall.
   - **Right panel (semantics and data):** the Properties editor's **Tool** tab (the first tab, above Render), also in the 3D viewport sidebar (`N`) under **Tool**. It holds what things mean and where they go. Surface classes, the attribute card with code lists, functions and classes, file and 3DCityDB open, and check / export / commit.

## Quick start

- **Open:** *File → Import → CityGML 2/3 Preview* or *Whole File*, the **Open** tab, or drag the file into the viewport. The same entries open CityJSON (`.city.json`) and CityJSONSeq (`.city.jsonl`): the file content decides the reader, not the menu name or the extension.
- **Export:** *File → Export → CityGML*, or the **Save** tab: pick **CityGML** or **CityJSON 2.0**, then export. A scene opened from a CityJSON file also shows **Save into <file>**.
- **Database:** *Properties → Tool tab → Open*: connect, pick a city model, load buildings. Blender is a working copy; *Save → Commit* writes your new, changed and deleted buildings to the database.
- **Panels:** the right panel has three tabs. **Open** (files, 3DCityDB), **Edit** (selection, semantics, attributes) and **Save** (check, export, commit).

## File formats: how CityGML and CityJSON are handled

### One model inside

Every source ends up in the same internal model, the CityGML 3.0 feature tree. A CityGML 2.0 file, a CityGML 3.0 file, a CityJSON file and a 3DCityDB city model all become the same Blender objects with the same semantics and attribute card. Editing, checking and the 3DCityDB commit do not depend on where a building came from.

- **CityGML 2.0 / 3.0** is read and written directly, including appearances (textures, materials).
- **CityJSON** is read into the same tree a CityGML 3.0 file gives. It is written by first running the CityGML 3.0 writer with its usual rules (scope, export defaults, checks), reading that result back and converting it to CityJSON 2.0. Both formats therefore follow the same export rules.

### Opening

- **Small files open completely.** A file of 20 MB or more, or with more than 30 buildings, opens as a **preview** first: only the outlines are drawn, you pick the buildings you need and load those. Both thresholds can be changed in the add-on preferences.
- **CityJSONSeq always opens as a preview** and is read line by line, never as a whole.
- Loading a whole file stops at 200 MB. Larger data belongs in 3DCityDB 5.x: import it there and load the part you need with **Open → 3DCityDB**.
- **One LoD per load.** CityGML loads LoD2 by default. CityJSON keeps its LoD text ("2.2"); the default is the highest LoD2. A building without the chosen LoD is skipped and counted in the summary, never filled from another LoD.

### Coordinates: CRS and scene origin

- The CRS comes from `srsName` (CityGML) or `metadata.referenceSystem` (CityJSON). The accepted spellings (EPSG, OGC URN/URL, compound CRS, AdV URNs) are listed in [shared/CRS_SUPPORT.md](shared/CRS_SUPPORT.md). Nothing is reprojected.
- Blender stores vertices as 32-bit floats, which lose precision at real-world coordinates such as UTM values in the millions of metres. The add-on therefore shifts the data near Blender's 0,0,0 by the **scene origin**: the centre of the first file's extent rounded to 100 m, with Z at its lowest point. The CRS and origin are kept on the World and shown at the top of the right panel. Export and commit add the origin back, so files and the database always receive the real CRS coordinates.
- A second file in the same CRS uses the same origin, so the two line up. While city objects are loaded, a file or city model in another CRS is refused: start a new Blender file (*File → New*) for it.
- When everything is closed or deleted, the old CRS and origin are forgotten and the next file sets its own. The exception is **Open → Align Other Data → Set Origin Before Opening**: an origin set there is kept until *File → New*. Use it to line the city model up with data that is already shifted, such as a point cloud's Global Shift.
- Other data (point cloud, shapefile, mesh) is moved onto the city model with **Align Selected to City Model**. For point clouds opened with Point Cloud IO it reads their shift by itself; for other data you enter the shift that data was imported with.

### Saving

- **CityGML:** 3.0 or 2.0, chosen in the export dialog.
- **CityJSON:** **Save** tab → **CityJSON 2.0** → **Export CityJSON File…**, with these options:
  - **Scope:** the whole scene, the selection, or a list of GML ids.
  - **CityJSONSeq:** a header line, then one feature per line.
  - **Vertex Precision:** 3 decimals = 1 mm by default.
- **Save into the source CityJSON file:** writes only what changed back into the file: changed, new and deleted buildings. Everything else stays exactly as it was in the file, including other city objects, other LoDs, metadata, extensions and geometry templates. The result is checked, written to a temporary file and only then renamed over the source, and only if the source was not changed on disk in the meantime. This works for buildings only.
- **3DCityDB:** *Save → Commit* writes new, changed and deleted buildings, whichever format they were opened from.

### What CityJSON covers

- **Read and written:** Building, BuildingPart, BuildingInstallation, their semantic surfaces and Window/Door; Road, Railway and TransportSquare with TrafficArea / AuxiliaryTrafficArea (the CityJSON core profile); CityFurniture, including `GeometryInstance` templates.
- **Core profile limits:** CityJSON core has no Section, Intersection, TrafficSpace or AuxiliaryTrafficSpace. Their polygons are written into their road's geometry and counted in the export summary. A Track has no core type and is not written.
- **Not read or written yet:**
  - CityJSON textures and materials. When you save into the source file, a building you changed loses its appearance; untouched buildings keep theirs.
  - Extension types (`+Type`). Save into the source file keeps them untouched.
- **Not loaded:** interiors (rooms, storeys, units, furniture) and other modules (Bridge, Tunnel, Relief, LandUse, WaterBody, Vegetation, Generic). They are counted in the summary; use 3DCityDB 5.x for them.
- **IDs:** an id that is a valid `gml:id` (for example `B.1` or `NL.IMBAG.Pand.0503100000000010`) is kept as it is. An id that is not valid (it starts with a digit, or contains a blank or `:`) gets a stable `gml:id`. Its original id is kept and written back on CityJSON export.

## Geometry Findings

**Edit → Geometry Findings → Check Geometry** checks the buildings and lists one row per finding:

- **Not planar:** with its distance from the plane, in mm.
- **Ring crosses itself.**
- **Window/door without hole.**
- **Shell not closed** and **Shell open (no solid):** with the number of open edges and T-junctions.
- **CityDoctor results:** also listed when [CityDoctor](#citydoctor-optional-geometry-validation) is set up.

Pressing a row opens Edit Mode on that building, selects the faces concerned (or the vertices of an open edge) and frames the view. The viewport draws the findings: red rings for polygons, yellow lines for open edges. A toggle on the panel turns this drawing off. Rows for non-planar polygons and open shells have a wrench button that repairs that kind of problem on the building (flatten polygons, or weld T-junctions) and checks it again.

## Drawing a building

Geometry is drawn in the row above the 3D view; meanings (classes, attributes, code lists) are set in the right panel.

1. **New Building** (footprint, then height) or **From Footprint**; **Roof** opens a gallery of roof types.
2. **Edit Facade** opens Edit Mode on the building. Select one wall face, then use **Window**, **Door**, **Balcony** or **Chimney**: drag on the selected face to place it (a chimney: drag width and depth on a roof face, then its height).
3. Each of the four tools has a small arrow beside it with its **types** and a search box: pick the type first, type a few letters to filter, the tool starts at once.
4. In Edit Facade the keys **1 Window, 2 Door, 3 Balcony, 4 Chimney** start the tools. On city objects they replace Blender's 1 / 2 / 3 vertex / edge / face keys; use the select-mode buttons in the header there.
5. **Copy** (Object Mode) and **Paste** (Edit Facade) repeat a window, door, balcony or chimney. **Face Wall** (Shift+F) turns the view to the selected wall; **Auto** does it for you.

New roofs get the importer's fallback roof colour, so drawn roofs and database roofs without their own appearance look alike.

## CityDoctor (optional geometry validation)

The **CityDoctor** button in the **Save** tab validates what an export writes with [CityDoctor2](https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctor2) (HFT Stuttgart). CityDoctor is not part of the add-on, so the button is grey until you set it up. The same steps are shown inside Blender: press the **?** button beside **CityDoctor**.

1. Open the [CityDoctorValidation downloads](https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctorreleases/-/tree/master/CityDoctorValidation), open the folder of the newest version and download the zip for your system. Java is inside these zips; nothing else has to be installed.
2. Unzip it.
3. In Blender press **?** beside **CityDoctor** (or open *Preferences → Add-ons → City Modeler*) and set **Path** to the start script in the unzipped folder.

| System | Download | Unzip to (example) | Path to set |
| --- | --- | --- | --- |
| Windows | `CityDoctorValidation-<version>-win.zip` | `C:\CityDoctor` | `C:\CityDoctor\start.bat` |
| Linux | `CityDoctorValidation-<version>-lin.zip` | `~/CityDoctor` | `~/CityDoctor/start.sh` |
| macOS | `CityDoctorValidation-<version>-mac.zip` | `~/CityDoctor` | `~/CityDoctor/start.sh` |

- The folder itself works as the path too. The add-on does not run `start.bat` / `start.sh` (they validate a sample file); it starts the Java in `runtime/` with the jars in `app/` and its own check configuration. A `config.yml` in that folder replaces the configuration.
- `CityDoctorValidation-<version>-no-runtime.zip` has no Java inside and needs Java 17+ on the `PATH`.
- macOS: if the system blocks the downloaded Java, run `xattr -dr com.apple.quarantine ~/CityDoctor` once. The macOS steps are not tested on a Mac yet.
- Your own command works as well: a script that takes one CityGML file and writes `<file>.citydoctor-report.xml` beside it. With an empty path the environment variable `CITYDOCTOR_VALIDATE_SH` is used.

## Credits

This add-on builds on the work of others:

- **CityGML Importer/Exporter** original implementation by **[Virtual City Systems](https://vc.systems/en/)** (virtualcitySYSTEMS), MIT License. This project is a continuation of that work.
- [3DCityDB](https://www.3dcitydb.org/) / `citydb-tool`, [lxml](https://lxml.de/), [pyproj](https://pyproj4.github.io/pyproj/).

## License

MIT — see [LICENSE.md](LICENSE.md). The original copyright notices are retained.
