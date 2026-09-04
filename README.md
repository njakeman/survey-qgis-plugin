# Field Survey Import

A QGIS 3.28+ plugin that imports zip exports from the [Field Survey](https://survey.field.works/)
app into a GeoPackage as styled, popup-ready layers — no network access, ever.

One zip becomes:

- **Points / Paths / Boundaries** layers in a GeoPackage (add to an existing one or start a new
  one per session)
- **Heading-aware symbology**: points with a recorded compass heading get a circle-with-arrow
  (♂-style) marker rotated to match; points without one get a plain circle — never a fake
  direction. Map-marked (not GPS) points are drawn hollow so they can't be mistaken for a
  measured fix.
- **Photo & audio popups**: hover a point for a gallery of every photo on it in a map tip (an
  observation can carry more than one), or open the attribute form for a full preview of the
  first; a "Play voice note" action opens an embedded player (falling back to your system's
  player for containers this build's Qt can't decode — verified: `.webm`/Opus needs the fallback
  here, `.m4a`/AAC plays embedded).
- **Revisit sessions**: station state (done / no access / skipped) styled as a coloured halo, plus
  a "Compare with reference photo" action for then-vs-now — one paired row per photo, when an
  observation has several — when the reference session is also imported. Importing a revisit
  **without** its reference works fine — the comparison just says what's missing.
- **Re-import handling**: importing the same zip twice is a no-op; a changed re-export offers
  Replace or Add alongside.

The full data contract this plugin implements is `survey-tool-qgis-plugin-handoff.md` — read it
before changing anything in `field_survey_import/core/`.

## Installing

**From a release zip**: QGIS → Plugins → Manage and Install Plugins → Install from ZIP → pick
`dist/field_survey_import-<version>.zip` (built by `scripts/package.ps1`).

**For development**, see below.

## Development setup

Two separate Python environments are involved, because the plugin's pure-Python core needs to run
without QGIS installed, and QGIS's own Python has no `pytest`:

```powershell
# Pure-core venv (standalone Python 3.12, plain pytest + ruff, plus requirements.txt
# for scripts/export_kml.py's photo downscaling - Pillow is the plugin's only
# third-party dependency, and only that one script uses it)
py -m venv .venv
.venv\Scripts\python.exe -m pip install pytest ruff
.venv\Scripts\python.exe -m pip install -r requirements.txt

# QGIS-dependent tests run through the QGIS interpreter directly - no venv needed,
# scripts/run-qgis-tests.ps1 installs pytest into it (--user, no elevation) on first run.
```

### Running the tests

```powershell
# Pure-core suite (fast, no QGIS needed)
.venv\Scripts\python.exe -m pytest

# QGIS-dependent suite (symbology rendering, GeoPackage writing, dialogs, etc.)
.\scripts\run-qgis-tests.ps1

# Lint
.venv\Scripts\python.exe -m ruff check .
```

### Deploying to a local QGIS profile

```powershell
.\scripts\deploy.ps1              # copies field_survey_import/ into the "default" profile
.\scripts\deploy.ps1 -Symlink      # symlink instead (needs Developer Mode or an elevated shell)
```

Then enable **Field Survey Import** in QGIS's Plugin Manager. With a plain copy (the default),
re-run `deploy.ps1` after each change and use the Plugin Reloader plugin (or restart QGIS) to pick
it up.

### Regenerating the shipped styles

`field_survey_import/styles/*.qml` are generated from `field_survey_import/qgis/renderers.py` and
committed, so they're inspectable/user-overridable on disk (handoff §9.3) and the layers come up
styled even if opened without the plugin installed. After changing a renderer:

```powershell
& "C:\Program Files\QGIS 3.44.8\bin\python-qgis-ltr.bat" scripts\build_styles.py
```

The renderer-building functions are also the runtime fallback if a `.qml` ever fails to load, so
they can't silently drift from what's shipped without a test noticing (`tests/qgis/test_styles.py`).

### Packaging a release

```powershell
.\scripts\package.ps1
```

Writes `dist/field_survey_import-<version>.zip` (version read from `metadata.txt`) with the single
top-level `field_survey_import/` folder QGIS's "Install from ZIP" expects.

### Sharing a session as KMZ

```powershell
.venv\Scripts\python.exe scripts\export_kml.py path\to\export.zip
```

Converts a Field Survey zip export into a single `.kmz` with every photo embedded, for sharing
with anyone using Google Earth (desktop/web/mobile) — no QGIS or the plugin needed on either end.
Runs standalone under the plain `.venv`. **Google My Maps does not render photos embedded in a
KMZ's balloons at all**, regardless of file size — only externally-hosted image URLs. If My Maps
specifically is where you're sharing this, use the HTML export below instead.

Photos are downscaled/recompressed by default before embedding (a balloon only ever displays one
at 400px or 160px wide, so a phone's full-resolution original is wasted size) — this is usually
what keeps a multi-photo session's `.kmz` under Google My Maps' 5MB upload limit, which the script
warns about if it's still exceeded. Tune or disable it:

```powershell
.venv\Scripts\python.exe scripts\export_kml.py path\to\export.zip --max-photo-dimension 800 --photo-quality 60
.venv\Scripts\python.exe scripts\export_kml.py path\to\export.zip --no-optimize-photos
```

### Sharing a session as a self-contained HTML map

```powershell
.venv\Scripts\python.exe scripts\export_html.py path\to\export.zip
```

Converts a Field Survey zip export into a single `.html` file with an interactive map (Leaflet)
and every photo/audio file embedded directly in the page as base64 data — nothing else to keep
alongside it. Opens in any browser (just double-click it), which is what makes this the option for
Google My Maps users or anyone without Google Earth — it sidesteps both the "My Maps won't show
KMZ photos" limitation above and the "needs Google Earth installed" one. The basemap and the
Leaflet library load from a CDN over *the viewer's* own internet connection when they open the
file; the plugin/this script itself makes no network calls, same as everything else here.

Basemap defaults to **OpenFreeMap**'s `liberty` vector style (`--basemap openfreemap-liberty`);
`openfreemap-bright` and `openfreemap-positron` are the same provider's other published styles.
These need a WebGL-capable browser. `--basemap esri` switches to a plain raster tile fallback that
needs no WebGL and works everywhere, at the cost of a plainer-looking map:

```powershell
.venv\Scripts\python.exe scripts\export_html.py path\to\export.zip --basemap openfreemap-bright
.venv\Scripts\python.exe scripts\export_html.py path\to\export.zip --basemap esri
```

(Two earlier raster choices were tried and rejected before landing here — OpenStreetMap's own tile
servers block a `file://`-opened page with a 403, and CARTO's free tier now returns an "API key
required" placeholder instead of a real tile. See `CLAUDE.md`.)

Takes the same photo-optimization flags as the KMZ export (`--max-photo-dimension`,
`--photo-quality`, `--no-optimize-photos`) — see above.

## Architecture

```
field_survey_import/
├── core/       pure Python, zero qgis/PyQt5 imports (enforced by a test) - the
│               zip → parsed-session model. Every handoff gotcha (mixed int/float
│               fields, absent-vs-null properties, the media join, slug naming) is
│               resolved exactly once, here.
├── qgis/       QGIS API layer: GeoPackage writing, duplicate detection, symbology,
│               map tips/form widgets, actions, revisit photo resolution. No UI.
├── ui/         dialogs and the audio player dock.
├── processing/ the Processing algorithm (same import_flow as the toolbar dialog,
│               batch-import via QGIS's "Run as batch process").
└── styles/     committed, regeneratable .qml
```

`field_survey_import/qgis/import_flow.py::import_zip()` is the single import entry point every
front end (toolbar dialog, Processing algorithm) calls — the import logic itself is written once.

See `survey-tool-qgis-plugin-handoff.md` for the data contract and `CLAUDE.md` for the fuller
architecture notes aimed at an AI coding agent picking this up.
