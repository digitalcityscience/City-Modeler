# Headless Blender Launcher

This folder contains Python launchers for running the add-on with Blender in background mode.

More detailed documentation with parameter descriptions is available in `city_modeler/HEADLESS.md`.

## Available Scripts

- `run_import.py`: Import a CityGML file into Blender and save it as `.blend`
- `run_export.py`: Export a `.blend` file to CityGML
- `run_db_import.py`: Import data from 3DCityDB into Blender and save it as `.blend`
- `run_db_export.py`: Export a `.blend` file to 3DCityDB through CityGML
- `headless_cli.py`: Shared wrapper with the subcommands `import`, `export`, `db-import`, and `db-export`
- `run_headless.ps1`: Windows PowerShell wrapper for production Blender calls
- `run_headless.bat`: Simple Windows shortcut for `run_headless.ps1`

## Recommended Blender Command

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\...\scripts\headless\headless_cli.py" `
  -- <subcommand> [options]
```

`--factory-startup` avoids side effects from saved user configuration.

## Examples

### CityGML to Blend

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\Users\ofoerster\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\city_modeler\scripts\headless\headless_cli.py" `
  -- import "C:\data\model.gml" "C:\data\model.blend"
```

### Blend to CityGML

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\Users\ofoerster\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\city_modeler\scripts\headless\headless_cli.py" `
  -- export "C:\data\model.blend" "C:\data\export.gml" --citygml-version 3.0 --srs-name EPSG:25832
```

### 3DCityDB to Blend

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\Users\ofoerster\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\city_modeler\scripts\headless\headless_cli.py" `
  -- db-import "C:\data\db_import.blend" --db-host localhost --db-port 5432 --db-name citydb --db-user postgres --db-pass secret --bbox "24487802,6820388.5,24488468,6820804.5,25832" --no-appearances
```

### Blend to 3DCityDB

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\Users\ofoerster\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\city_modeler\scripts\headless\headless_cli.py" `
  -- db-export "C:\data\model.blend" --db-host localhost --db-port 5432 --db-name citydb --db-user postgres --db-pass secret --with-appearances --overwrite-existing
```

For existing database features, headless export provides direct alias options:

- `--overwrite-existing`: delete existing features and import them again
- `--skip-existing`: leave existing features unchanged

Appearance is enabled by default. The flags can also be set explicitly:

- `--with-appearances`: include appearance/textures in the DB import
- `--no-appearances`: skip appearance/textures during DB import of the exported CityGML file

## Help

Global wrapper help:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\Users\ofoerster\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\city_modeler\scripts\headless\headless_cli.py" `
  -- --help
```

Help for a subcommand:

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" `
  --background `
  --factory-startup `
  --addons "city_modeler" `
  --python "C:\Users\ofoerster\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\city_modeler\scripts\headless\headless_cli.py" `
  -- export --help
```

Wrapper help on Windows:

```powershell
.\run_headless.ps1 -Action export -ShowHelp
```

Or through the batch file:

```bat
run_headless.bat -Action export -ShowHelp
```

## Windows Wrapper Examples

### CityGML to Blend

```powershell
.\run_headless.ps1 -Action import -Arguments "C:\data\model.gml","C:\data\model.blend"
```

### Blend to CityGML

```powershell
.\run_headless.ps1 -Action export -Arguments "C:\data\model.blend","C:\data\export.gml","--citygml-version","3.0","--srs-name","EPSG:25832"
```

### Blend to 3DCityDB

```powershell
.\run_headless.ps1 -Action db-export -Arguments "C:\data\model.blend","--db-host","localhost","--db-port","5432","--db-name","citydb","--db-user","postgres","--db-pass","secret","--with-appearances","--overwrite-existing"
```

### 3DCityDB to Blend with Bounding Box and No Appearance

```powershell
.\run_headless.ps1 -Action db-import -Arguments "C:\data\db_bbox_import.blend","--db-host","localhost","--db-port","5432","--db-name","citydb","--db-user","postgres","--db-pass","secret","--bbox","24487802,6820388.5,24488468,6820804.5,25832","--no-appearances"
```

`--bbox` uses the format `xmin,ymin,xmax,ymax[,srid]`. If an `srid` is provided, it is automatically used as `filter_crs` for DB import.

## Notes

- The DB scripts perform real database operations. Credentials and target systems must be correct.
- Optionally, `--citydb-exe`, `--use-docker` and `--docker-image` can be passed. Only 3DCityDB 5.x is supported.
- The DB launchers were syntactically validated, but they were not executed against a real database in this session.

## Isolated CAC Verification Profile

`setup_cac_verification.ps1` builds the checkout as a Blender extension and installs it into a disposable, checkout-local profile (`.cac-verification/`, git-ignored). `BLENDER_USER_RESOURCES`, `TEMP` and `TMP` point into that profile, so neither `%APPDATA%` add-ons/extensions nor the legacy `pydeps` shim are visible; the declared wheels (lxml, pyproj) come from Blender's extension site. `CAC_ADDON_MODULE` tells `cac_harness.py` to enable `bl_ext.user_default.city_modeler` and to alias the tests' `city_modeler.*` imports to those same module objects.

```powershell
# rebuild the profile and prove it from a run_cac_suite.py child process
scripts\headless\setup_cac_verification.ps1
# ... and run the full CAC suite (or -Only p4) in that profile
scripts\headless\setup_cac_verification.ps1 -SuiteOut C:\cac-out
# another checkout/worktree; a fresh worktree has no generated wheels/
scripts\headless\setup_cac_verification.ps1 -RepoRoot C:\wt -WheelsDir C:\repo\wheels -SuiteOut C:\cac-out-wt
```

`xmllint` (libxml2 >= 2.14) must be on `PATH`; on this machine it is `C:\ProgramData\anaconda3\Library\bin\xmllint.exe`. Without it `run_cac_suite.py` stops at once; before 2026-11-04 every export check failed one by one inside `cac_harness.xmllint` (37 fail + 2 xpass), which read like an order-dependent suite.

The diagnostic (`verification/test_cac_environment.py`) prints the loaded add-on path, the source stamp (HEAD and dirty files), Blender/Python versions and the `pyproj` location, and fails unless both come from the profile. The profile is a build-time copy: rebuild it (omit `-NoRebuild`) after changing add-on code.
