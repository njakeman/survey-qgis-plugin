# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A QGIS 3.28+ plugin (`field_survey_import/`) that imports zip exports from the Field Survey app
into a GeoPackage as styled Points/Paths/Boundaries layers with photo/audio popups. The zip format
is a **fixed, externally-owned contract** — full spec in `survey-tool-qgis-plugin-handoff.md`
(including an **Addendum** section at the end for the multi-photo `photos` array, which is plugin-
inferred rather than app-author-specified), read it before touching `core/`. `sample.zip` (11
features, one legacy photo per observation) and `multiple-photo-test-2026-08-25.zip` (3 features,
2/4/1 photos each) are real exports used as the primary test fixtures; `tests/fixtures/build_zips.py`
builds synthetic zips for everything the two samples don't cover (revisit sessions, `trace_gaps`,
`.m4a` audio, old exports with absent keys, per-photo revisit pairing, etc).

An implementation plan with full rationale lives at
`C:\Users\neil_\.claude\plans\there-is-also-a-linear-fog.md` if deeper "why" is needed - it now
holds the KML/KMZ export plan (`scripts/export_kml.py`), which overwrote the multiple-photos-per-
observation plan the file previously held, which itself overwrote the original build-the-whole-
plugin plan (both earlier plans are complete; this file's own git history and commit messages are
the closest thing to a record of them, since the plan file itself lives outside this repo).

## Commands

Two separate Python environments, because `core/` must run without QGIS installed, and QGIS's own
Python ships no `pytest`:

```powershell
# Pure-core suite (fast, no QGIS) - core/, identity, media, reader logic
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest tests/test_reader_sample_zip.py -k test_name  # single test

# QGIS-dependent suite - GeoPackage writing, symbology rendering, dialogs, Processing
.\scripts\run-qgis-tests.ps1
# equivalently, single test:
& "C:\Program Files\QGIS 3.44.8\bin\python-qgis-ltr.bat" -m pytest tests/qgis/test_symbology.py -v

# Lint (whole repo, ruff doesn't need QGIS)
.venv\Scripts\python.exe -m ruff check .            # --fix for autofixable

# Deploy to the local "default" QGIS profile for manual testing
.\scripts\deploy.ps1

# Regenerate field_survey_import/styles/*.qml after changing qgis/renderers.py
& "C:\Program Files\QGIS 3.44.8\bin\python-qgis-ltr.bat" scripts\build_styles.py

# Package a release zip -> dist/field_survey_import-<version>.zip
.\scripts\package.ps1

# Share a session as a KMZ (Google Earth/Google Maps) - standalone, no QGIS needed
.venv\Scripts\python.exe scripts\export_kml.py <zip> [-o out.kmz]
```

QGIS 3.44.8 LTR lives at `C:\Program Files\QGIS 3.44.8`; nothing QGIS-related is on PATH, so every
QGIS-Python invocation goes through the full path to `bin\python-qgis-ltr.bat` (note: `-ltr`
suffixed — `python-qgis.bat`/`qgis.bat` don't exist on this install).

## Architecture

```
field_survey_import/
├── core/        pure Python, ZERO qgis/PyQt5 imports (enforced by
│                tests/test_core_has_no_qgis_imports.py, an AST scan - not a convention,
│                a build-breaking rule). zip -> parsed session model.
│                schema.py   the 26 documented fields (25 from handoff §3 + `photos`,
│                            the addendum's array) + 7 plugin-added columns, EXPLICITLY
│                            typed (kills the mixed-int/float trap - see Gotchas below)
│                model.py    frozen dataclasses: SurveyExport/SurveySession/Observation/
│                            PhotoRef/...
│                reader.py   zip -> SurveyExport; every handoff tolerance rule lives here
│                media.py    photo/audio resolution + extraction, with a zip-slip guard
│                identity.py content hashing, session_slug()/unique_slug()
│                kml.py      zip -> KMZ (scripts/export_kml.py's engine) - hand-built string
│                            templates, no XML/KML dependency (ElementTree can't emit CDATA)
├── qgis/        QGIS API layer, no UI code
│                import_flow.py   THE single import entry point - toolbar dialog and the
│                                 Processing algorithm both call import_zip() and nothing else
│                writer.py        GeoPackage layer + side-table (fs_sessions/fs_revisits/
│                                 fs_revisit_stations/fs_photos) writing; duplicate-detection
│                                 queries
│                duplicates.py    check_duplicate() / replace_import() / add_alongside_import()
│                renderers.py     symbology builders (also scripts/build_styles.py's source
│                                 and styling.py's runtime fallback if a .qml fails to load)
│                styling.py       apply_style(): loads styles/*.qml, falls back to renderers.py
│                forms.py         map tip HTML template + attribute-form widget config
│                actions.py       QgsAction registration (audio play, photo compare)
│                revisit.py       ref_obs_id <-> obs_id reference-photo resolution (§7)
├── ui/          import_dialog.py, conflict_dialog.py, audio_player.py (MediaDock),
│                photo_compare.py - the only files that build real QWidget UI
├── processing/  import_algorithm.py wraps import_flow.import_zip() for QGIS's Processing
│                toolbox / "Run as batch process" (batch-importing many zips)
└── styles/      points.qml / paths.qml / boundaries.qml - committed, regeneratable, and
                 what actually gets applied at runtime (renderers.py is the fallback)
```

**Everything routes through `import_flow.import_zip()`.** The dialog, the Processing algorithm,
and `duplicates.replace_import()`/`add_alongside_import()` (which call it internally after
clearing out what needs clearing) all share this one function — there is exactly one code path
that turns a zip into a GeoPackage.

**Side tables, not `QgsLayerMetadata`, for session bookkeeping** (`fs_sessions`, `fs_revisits`,
`fs_revisit_stations` in `writer.py`): duplicate detection needs a real query, a `not_visited`
revisit station has no feature to hang metadata off at all, and one GeoPackage holds many sessions
over time. `import_id` (a fresh uuid4 per import) is the real identity column — `session_id` is
deliberately **not** unique, since "Add alongside" means two rows legitimately share one
`session_id` with different `import_id`s and different layer/table names.

**`.qml` files are both committed output and disposable.** `renderers.py` builds every renderer in
PyQGIS; `scripts/build_styles.py` freezes that into `styles/*.qml`; `styling.apply_style()` loads
the `.qml` and falls back to calling `renderers.py` directly if that ever fails — so the Python
builders can't rot silently, they're exercised on every load either way (see
`tests/qgis/test_styles.py`).

## Gotchas hit while building this (verify before assuming otherwise)

- **PEP 604 (`X | None`) unions need `from __future__ import annotations`** in every `core/`
  module — the plugin's Python floor is 3.9. Runtime `isinstance(x, int | float)` doesn't get
  saved by that import (it's not an annotation) — use `isinstance(x, (int, float))`.
- **`layer.loadNamedStyle(path)` returns `(message, success)`** — message first, *not*
  `(success, message)` as the call's argument order might suggest. Getting this backwards makes
  every style application silently "succeed" while actually always falling through.
- **`loadNamedStyle()` replaces the layer's entire `<customproperties>` block.** Any
  `setCustomProperty(...)` calls must happen *after* `styling.apply_style()`, not before — the
  plugin's `field_survey/*` custom properties (used to find layers again for Replace/duplicate
  detection) get silently wiped otherwise.
- **QGIS's expression engine only supports searched-CASE** (`CASE WHEN cond THEN ... END`) — the
  switch-style `CASE x WHEN v1 THEN ... WHEN v2 THEN ... END` is a parser error ("expecting WHEN").
  Any `[% %]` block using it fails to evaluate and is left **verbatim, unrendered** in map
  tips/labels rather than erroring loudly — check for stray `[%`/`%]` in rendered output, don't
  trust silence.
- **Marker rotation is clockwise-from-north with the offset rotating with it** — verified by
  rendering and pixel-sampling (`tests/qgis/test_symbology.py`), matching `heading_deg` exactly
  with no transform needed. Re-verify against a real render if this ever seems to drift; don't
  trust it by inspection of the API alone.
- **`point_n()` is 1-based over coordinates; `trace_gaps` is 1-based over *segments*** (handoff
  §8: index `i` ⇒ the segment from coordinate `i-1` to `i`). So coordinate `i-1` is `point_n(g, i)`
  and coordinate `i` is `point_n(g, i+1)` — see the comment above `_WALKED_SEGMENTS_EXPR` in
  `renderers.py`, pinned by `test_line_gap_segments_are_1_based_over_segments_not_coordinates`.
- **This machine's Qt Multimedia backends (DirectShow/Media Foundation) decode AAC (`.m4a`) but
  not Opus (`.webm`)** — and every audio file in `sample.zip` is `.webm`. `audio_player.py` routes
  `.m4a` to the embedded `QMediaPlayer` and everything else straight to the system player. Don't
  assume a future Qt build's capability without re-checking — the routing table is a single
  `_EMBEDDED_CAPABLE_EXTENSIONS` set in `audio_player.py`.
- **`QgsField(name, QMetaType.Type)` leaves `typeName()` blank** unless passed explicitly — this
  turned out not to matter (GDAL/OGR derives the real GeoPackage column type from the QMetaType
  correctly regardless, verified by round-tripping a write/read cycle), but don't be alarmed by
  the blank string if you go looking.
- **`layer_property(@layer,'path')` on an OGR GeoPackage layer returns the plain `.gpkg` path**,
  no `|layername=` contamination — this is the whole media-path-portability mechanism
  (`forms.py`'s `_RESOLVE_AUDIO_EXPR`/`_PHOTO_GALLERY_EXPR`), verified against a real imported
  layer before being relied on. It also resolves correctly evaluated once per element **inside an
  `array_foreach` lambda** (verified 3.44.8) — no `with_variable` hoisting needed for correctness,
  only as a one-call-instead-of-N optimisation.
- **`sample.zip`'s media basenames happen to equal their owning `obs_id`.** That's a coincidence
  of this one fixture — the join is always by the literal `photo`/`audio` property value
  (handoff §2, §8); `tests/test_media.py::test_media_join_is_by_property_value_not_obs_id`
  deliberately uses a mismatched `obs_id` to prove the join doesn't lean on it.
- **A map-tip expression referencing a column that doesn't exist on a layer is an *evaluation*
  error, not a parser error** — and QGIS leaves the **whole `[% %]` block verbatim, unrendered**,
  the same silent-failure shape as the switch-CASE trap above. This bites a layer written by an
  older plugin version once a new column (e.g. `photo_paths`) is added: a bare `"photo_paths"`
  reference breaks the entire tip on that old layer, while `attribute(@feature, 'photo_paths')`
  returns `NULL` with no error and degrades cleanly. **`try(expr, fallback)` does NOT rescue
  this** — it evaluates to `NULL`, not the fallback; `attribute()` is the only fix. See
  `forms.py`'s `_PHOTO_LIST_EXPR` and `tests/qgis/test_forms.py::test_map_tip_falls_back_when_photo_paths_column_is_absent`.
- **`array_to_string()` over an *empty* array returns `NULL`, not `''`** — always wrap it in
  `coalesce(..., '')`, or a photo-less feature's gallery expression silently becomes `NULL` instead
  of an empty string (verified 3.44.8).
- **`ogr.UseExceptions()` is set process-wide (`writer.py`), so `ExecuteSQL("DELETE FROM
  <table that doesn't exist>")` raises `RuntimeError`, not a silent no-op.** Every side-table
  DELETE in `delete_session_rows_and_layers()` must guard with `ds.GetLayerByName(...) is not
  None` first — a GeoPackage written by an older plugin version (e.g. one that predates
  `fs_photos`) will lack a table a newer version's DELETE assumes exists, and Replace runs this
  delete step *before* `import_zip()`'s `ensure_side_tables()` call, so there's nothing else to
  create the table first.
- **`layer.saveNamedStyle(path)` returns `(message, success)`** — same trap, same order, as
  `loadNamedStyle()` above; `scripts/build_styles.py` had this backwards until it was noticed
  (it "worked" only because the success-path message happened to be a non-empty string).
- **`layer_property(@layer,'path')` only resolves for file-backed layers — `NULL` on a `memory`
  layer**, with no error. A test simulating "an old GeoPackage's field shape" must still write to a
  real `.gpkg` on disk and reopen it via the `ogr` provider, not use a `memory` layer, or any
  expression depending on `layer_property` silently degrades to nothing instead of exercising the
  real fallback path.
- **Zip-internal paths (KMZ member names, `core/kml.py`'s `<img src=...>` hrefs, and `core/media.py`
  generally) must be built as plain forward-slash strings, never `pathlib.Path` joins** —
  `Path("files") / name` yields backslashes on Windows, which silently corrupts both the zip member
  name and the HTML `href` it's used in.
- **`xml.sax.saxutils.escape()` inside a CDATA-wrapped HTML block isn't required by the XML spec**
  (the only sequence actually illegal there is a literal `]]>`) **but is applied anyway in
  `core/kml.py`'s map-tip-style description balloons** — for HTML-rendering hygiene, and, as a side
  effect of escaping `>`, it also turns any `]]>` in a surveyor's note into `]]&gt;`, neutralising
  the one thing that would otherwise truncate the CDATA section early. Don't remove it on the
  theory that "CDATA doesn't need escaping" - that's true for the XML layer only.
