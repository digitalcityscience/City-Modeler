# City Modeler for Blender

**City Modeler** is a Blender add-on for modelling semantic 3D city models: draw and edit buildings, give their surfaces and parts meaning, and keep them in sync with a **3DCityDB 5.x** database. Files are read and written as **CityGML 2.0 / 3.0** today; **CityJSON** support is coming soon.
Developed and maintained by [Mehmet Akif Ortak](https://github.com/orttak) as part of research work at **HafenCity University Hamburg (HCU)** within the **Next Generation City Network (NGCN)** project.

## Features

- CityGML 2.0 and 3.0 import/export (`.gml`, `.xml`) with textures, materials and semantics; CityJSON is coming soon
- 3DCityDB 5.x import/export via the bundled `citydb-tool` (3DCityDB 4.x is not supported)
- Drawing tools for buildings: footprint and height, roofs (AdV roof types), windows, doors, balconies, chimneys, Copy / Paste
- Semantics: assign surface classes (roof, wall, ground …), attribute card with code lists, building parts and installations
- Focus: Building, Transportation (road, railway, track, square) and CityFurniture. Interiors and the other modules are 3DCityDB 5.x work
- Filters (BBox, GML-ID, LoD, feature type), XSD validation, optional CityDoctor geometry check
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

- **Import:** *File → Import → CityGML*, or drag a `.gml` / `.xml` file into the viewport.
- **Export:** *File → Export → CityGML*.
- **Database:** *Properties → Tool tab → Open*: connect, pick a city model, load buildings. Blender is a working copy; *Save → Commit* writes your new, changed and deleted buildings to the database.
- **Panels:** the right panel has three tabs. **Open** (files, 3DCityDB), **Edit** (selection, semantics, attributes) and **Save** (check, export, commit).

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
