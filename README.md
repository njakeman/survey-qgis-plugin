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
- **Photo & audio popups**: hover a point for its photo in a map tip, or open the attribute form
  for a full preview; a "Play voice note" action opens an embedded player (falling back to your
  system's player for containers this build's Qt can't decode — verified: `.webm`/Opus needs the
  fallback here, `.m4a`/AAC plays embedded).
- **Revisit sessions**: station state (done / no access / skipped) styled as a coloured halo, plus
  a "Compare with reference photo" action for then-vs-now, when the reference session is also
  imported. Importing a revisit **without** its reference works fine — the comparison just says
  what's missing.
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
# Pure-core venv (standalone Python 3.12, plain pytest + ruff)
py -m venv .venv
.venv\Scripts\python.exe -m pip install pytest ruff

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
