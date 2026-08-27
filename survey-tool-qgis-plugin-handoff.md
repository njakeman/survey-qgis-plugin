# Handoff: QGIS import plugin for the Field Survey app's export zips

You are starting a **green-field QGIS plugin project**. This document is the complete brief:
the export format it describes is a fixed contract produced by an existing, deployed field
survey app — you consume it, you never change it, and you do not need (and probably do not
have) the app's source repo. Everything you need is here.

## 1. What you are building

The Field Survey app (https://survey.field.works/) is an offline-first PWA a surveyor uses in
the field on a phone: it records point observations (GPS fix, compass heading, note, photo,
voice note), walked **paths** (LineString traces — hedgerows, tracks, watercourses) and walked
**boundaries** (Polygon traces — field perimeters), and exports each survey session as a single
zip. The zip is the only way data leaves the device.

The plugin's job: **import one of those zips into QGIS** so the surveyor's office-side self (or
a colleague) can see everything the phone captured, with:

1. **Import**: pick a `.zip` → the session's features land in the project as layers, with the
   photos and voice notes available on disk.
2. **Popups**: clicking/identifying a feature shows its attributes, **displays its photo**, and
   lets the user **play its voice note**.
3. **Symbology**:
   - Points that carry a compass heading are drawn as a **circle with an arrow radiating from
     its edge at the heading angle** — like the biology "male" symbol (♂) — so the map shows
     the direction the surveyor was facing (i.e. the direction the photo looks). Points with no
     heading get a plain circle; never fake a direction.
   - Paths (LineString) and boundaries (Polygon) styled distinctly and sensibly (solid line;
     outlined polygon with a light fill).
   - See §5 for the full symbology spec, including the honest-data rules the app itself
     follows.

Target: standard PyQGIS plugin, QGIS 3.28 LTR or newer, Python ≥3.9, **no network access** —
the whole workflow is offline, matching the app's own ethos.

## 2. The zip — exact layout

```
<session-slug>-<YYYY-MM-DD>.zip      e.g. hedgerow-survey-2026-08-24.zip
├── session.geojson                  always present, always this exact name
├── photos/<photoId>.jpg             zero or more; always JPEG, ~1600 px long edge
└── audio/<audioId>.webm             zero or more voice notes
    audio/<audioId>.m4a              (.webm = Opus, .m4a = AAC — the only two containers)
```

- All ids (`photoId`, `audioId`, `obs_id`, session id) are **ULIDs** (26-char Crockford
  base32).
- The zip is produced by the browser library client-zip, which streams with **data
  descriptors** — local file headers understate sizes; the central directory is authoritative.
  Python's `zipfile` handles this correctly; just don't hand-parse local headers.
- The zip guarantees it **never claims a file it doesn't contain**: if a feature's `photo` or
  `audio` property is non-null, that file is in the zip. The converse also holds — join media
  to features **by the `photo`/`audio` property strings, never by reconstructing
  `obs_id + '.jpg'`** (the photo id is not the observation id, and revisit reference photos —
  §7 — make the wrong join actively harmful).

## 3. `session.geojson` — the schema

One RFC 7946 FeatureCollection, coordinates in **WGS84 (EPSG:4326)**, lon-lat order as GeoJSON
requires. Serialised canonically (sorted keys, fixed indent) — identical data always produces
identical bytes, so hashing a file is a meaningful identity check.

### Foreign members (RFC 7946 §6.1 — top-level keys QGIS ignores, you should not)

Always present:

```json
"survey_session": {
  "id": "01J5X…",              // ULID — the stable session identity across re-exports
  "name": "Hedgerow survey",
  "started_at": "2026-08-24T09:12:00.000Z",
  "ended_at": "2026-08-24T11:40:00.000Z"   // null if exported mid-session
}
```

Present **only** on revisit sessions (§7):

```json
"survey_revisit": {
  "reference_file": "spring-baseline-2026-04-12.zip",
  "reference_hash": "…",                   // hash of the reference zip
  "reference_session_id": "01J2A…",        // may be null
  "reference_session_name": "Spring baseline",
  "reference_started_at": "2026-04-12T09:00:00.000Z",
  "stations": [
    { "ref_obs_id": "01J2A…", "state": "done", "reason": null },
    { "ref_obs_id": "01J2B…", "state": "no_access", "reason": "bull in field" }
  ]
}
```

Station `state` ∈ `done | skipped | no_access | not_visited`; `reason` is a free-text string or
null (only ever set on `no_access`).

### Geometry

- **Point** — an ordinary observation (`position_source` `gps` or `map`).
- **LineString** — a walked path trace (`position_source: 'trace'`).
- **Polygon** — a walked boundary trace (`position_source: 'trace'`), always a single closed
  ring, first coordinate = last.

### Feature properties

Every feature carries **every key below**, with `null` where a value is absent — the column set
is deliberately stable so attribute tables don't depend on which rows happened to have data.
(Old exports from earlier app versions may lack the newer keys entirely — tolerate absence, but
never rely on it.)

| Property | Type | Meaning |
|---|---|---|
| `obs_id` | string (ULID) | Observation id. Stable across re-exports of the same session. |
| `recorded_at` | ISO 8601 UTC | When the surveyor tapped Save. |
| `fix_at` | ISO 8601 UTC | When the position was actually measured. **Deliberately distinct** from `recorded_at` — a surveyor can stand at a point, type for 40 s, then save. For traces: the walk's start. |
| `lat`, `lon` | number | The representative point, always present even for traces (path → distance-midpoint of the line; boundary → area-weighted centroid). This is what list views and joins use. |
| `gps_accuracy_m` | number | **Meaning depends on `position_source`** (see below). Always metres, always the honest figure. |
| `altitude_m` | number \| null | GPS altitude. Deliberately null for map-picked points (the far side of a valley is not at your own height). |
| `altitude_accuracy_m` | number \| null | |
| `heading_deg` | number \| null | **Compass heading the surveyor was facing at capture — the view direction, and the direction the photo looks.** 0 = north, clockwise (as the device compass reports it — the app does not normalise magnetic vs true north). Null when the compass was denied/unavailable (the app degrades rather than guessing). **This drives the arrow symbology.** |
| `heading_accuracy_deg` | number \| null | Compass uncertainty. |
| `note` | string | Free text, may be empty string. |
| `photo` | string \| null | Filename inside the zip, e.g. `photos/…` **without** the directory — the value is `<photoId>.jpg`; the entry is at `photos/<that value>`. Null = no photo. |
| `audio` | string \| null | Voice-note filename, `<audioId>.webm` or `<audioId>.m4a`; entry at `audio/<that value>`. Extension tells you the container: `.webm` = Opus, `.m4a` = AAC. |
| `audio_duration_ms` | number \| null | Measured at record time — usable without opening the file (e.g. show "0:42" on a play button). |
| `feature_layer`, `feature_id`, `feature_label` | string \| null | If the observation was started from a tapped feature of the surveyor's own GeoJSON reference layer in the app: that layer's id, the feature's id, and a human label. Both id halves present or both null, never one. |
| `os_grid_ref` | string \| null | Ordnance Survey grid reference (OSTN15-correct). Null outside Great Britain. A restatement of lat/lon — display convenience only. |
| `position_source` | `"gps"` \| `"map"` \| `"trace"` | How the coordinates were obtained. See below. |
| `trace_length_m` | number \| null | Walked length (path) or perimeter (boundary). Null on every Point row. |
| `trace_gaps` | int[] \| null | Segments the app **inferred rather than measured**: index `i` means the segment from coordinate `i−1` to coordinate `i` spans a stretch where the fix stream went silent (app backgrounded, or paused). 1-based over segments; null/absent = no gaps. |
| `ref_obs_id` | string \| null | Revisit pairing: the reference-session observation this one re-photographs (§7). |
| `ref_photo` | string \| null | That reference station's photo filename **inside the reference zip** — not this zip. |
| `session_name` | string | Denormalised copy of the session name, on every row. |
| `app_version` | string | App version that produced the export. |

### `position_source` and what `gps_accuracy_m` means

This is the one subtlety that must not be flattened:

- `gps` — a measured fix; `gps_accuracy_m` is the GPS accuracy.
- `map` — the surveyor **marked a point on the map they could see but not reach**;
  `gps_accuracy_m` is the map precision at the zoom it was picked at. ±12 m measured and ±12 m
  eyeballed from 300 m away are distinguished **only** by this field — keep the distinction
  visible (§5).
- `trace` — a walked geometry; `gps_accuracy_m` is the **worst vertex's** fix accuracy.

### Worked example (one Point feature, abridged)

```json
{
  "type": "Feature",
  "geometry": { "type": "Point", "coordinates": [-0.14, 51.5002] },
  "properties": {
    "obs_id": "01J60QG2N4X7ZK8W9YB3C5D6E7",
    "recorded_at": "2026-08-24T10:05:41.000Z",
    "fix_at": "2026-08-24T10:05:02.000Z",
    "lat": 51.5002, "lon": -0.14,
    "gps_accuracy_m": 4.1,
    "altitude_m": 33.2, "altitude_accuracy_m": 6.5,
    "heading_deg": 38, "heading_accuracy_deg": 15,
    "note": "Stone stile, west boundary.",
    "photo": "01J60QGABCDEF….jpg",
    "audio": "01J60QGXYZ….m4a",
    "audio_duration_ms": 12400,
    "feature_layer": null, "feature_id": null, "feature_label": null,
    "os_grid_ref": "TQ 29154 80456",
    "position_source": "gps",
    "trace_length_m": null, "trace_gaps": null,
    "ref_obs_id": null, "ref_photo": null,
    "session_name": "Hedgerow survey",
    "app_version": "1.0.0"
  }
}
```

## 4. Import behaviour

- Split by geometry into three layers per session — **Points / Paths / Boundaries** — since
  QGIS symbolises per-layer and the three have different renderers. Name them from
  `survey_session.name` + `started_at` date.
- **Persist to a GeoPackage** (one per import, or one per project) rather than leaving a
  temporary/scratch layer — the import should outlive the zip and the QGIS session. Keep the
  foreign-member session metadata (id, name, started_at, ended_at) — GeoPackage layer metadata
  or a small side table; it is not on the features.
- **Extract media** (`photos/`, `audio/`) to a directory beside the GeoPackage (e.g.
  `<gpkg-dir>/<session-slug>-media/`), and store a usable path or make paths derivable — the
  popups need real file paths. Prefer relative paths / project-relative resolution so the
  project folder can be moved or shared.
- Importing the same zip twice should not silently duplicate — at minimum detect via
  `survey_session.id` (+ file hash, since re-exports of a changed session share the id) and ask.
- A surveyor will import **many sessions** over time into one project; the layer naming and
  media directory scheme must not collide.

## 5. Symbology

Mirror the app's own visual grammar where it applies; the rules below are its spirit.

**Points:**
- `heading_deg` non-null → **circle + arrow**: a circle marker with a second arrow/line marker
  layer radiating outward from the circle's edge, data-defined rotation bound to `heading_deg`
  (♂-style). QGIS marker rotation is clockwise-from-north, matching the data — verify with a
  known fixture rather than assuming.
- `heading_deg` null → plain circle. **Never render a default-north arrow** — a static arrow
  implying a measured direction is worse than none.
- Keep `position_source` visible: e.g. solid fill for `gps`, hollow/dashed-outline for `map`
  (an eyeballed point must not masquerade as a measured one). Trace representative points
  aren't separately drawn — the geometry itself is the record.
- Optional (nice): translucent accuracy disc scaled from `gps_accuracy_m` (geometry generator
  or a second renderer), off by default.

**Paths (LineString):** solid line, distinct hue from boundaries.

**Boundaries (Polygon):** solid outline, light translucent fill.

**`trace_gaps` (stretch goal, matches the app):** segments listed in `trace_gaps` are inferred,
not walked — the app draws them **dotted**. A geometry-generator sub-renderer that splits the
line by gap indices would reproduce this. If skipped, at least surface `trace_gaps` in the
popup so inferred stretches aren't silently presented as measured.

**Revisit sessions (nice-to-have):** where `survey_revisit` exists, offer station-state-aware
styling and show the pairing (`ref_obs_id`, station state, `no_access` reasons) in popups.

## 6. Popups: photos and audio

Requirements, with the mechanism left to you:

- **Photo**: visible in the identify/popup flow. QGIS HTML **map tips** render `<img>` from a
  file path natively and are the low-friction route; an attribute-form photo widget
  (`ExternalResource` with photo preview) is the more integrated one. Either or both.
- **Audio**: playable per feature. QGIS has no built-in audio player, so choose between
  (a) an "open in system player" action (`QDesktopServices.openUrl`) — simplest and codec-safe,
  (b) a small Qt Multimedia player in a dock/dialog — nicer, but note `.webm`/Opus support
  varies by platform Qt build; test both containers, and fall back to (a) on failure.
  Show `audio_duration_ms` on the control either way.
- Popup should also surface: `note`, `recorded_at`, `gps_accuracy_m` (+ its `position_source`
  caveat), `os_grid_ref`, `feature_label` when present.

## 7. Revisit sessions — what the pairing means

The app has a "revisit" mode: a surveyor loads a **previous** session's export zip as a
reference and re-photographs its stations, building a longitudinal record. In the resulting
export:

- `ref_obs_id` on a feature = the observation **in the reference session** this one revisits.
- `ref_photo` = that reference observation's photo filename **inside the reference zip** —
  which this zip does **not** contain (self-describing, not self-contained).
- `survey_revisit.stations` lists **every** reference station with its end state, including
  ones never revisited (`not_visited`) and ones claimed unreachable (`no_access` + reason).

If the user has also imported the reference session, `ref_obs_id` ↔ `obs_id` is the join for
then-vs-now photo comparison — a genuinely valuable stretch feature, but plain import must not
depend on the reference being present.

## 8. Gotchas checklist

- Data-descriptor zips: trust the central directory (`zipfile` does).
- Join media by the `photo`/`audio` property values, never `obs_id + '.jpg'`.
- `.webm` = Opus, `.m4a` = AAC; nothing else ever occurs.
- `recorded_at` ≠ `fix_at`; don't collapse them.
- `gps_accuracy_m` semantics depend on `position_source`.
- `heading_deg` is degrees clockwise from north; null is common (compass denied) — degrade.
- Traces still carry `lat`/`lon` (representative point) — don't treat them as the geometry.
- Polygons are single-ring; a self-intersecting ring (figure-eight walk) is **valid data** the
  app deliberately saves with a warning — import it, don't reject it.
- Properties may be absent (not just null) in old exports; read defensively.
- `trace_gaps` indexes **segments** (i ⇒ coords i−1 → i), not vertices.
- No network, ever, in the plugin.

## 9. Suggested first steps

1. Get a real export zip from the user for a fixture (they can export any session from the
   app's share sheet), plus hand-write a minimal `session.geojson` fixture from §3 for unit
   tests.
2. Skeleton: `metadata.txt`, plugin class registering a toolbar action / Processing algorithm
   ("Import Field Survey zip…"), an import module (pure Python: zip → parsed session model —
   testable without QGIS), then the QGIS layer/GeoPackage/symbology/popup wiring on top.
3. Build symbology as `.qml` style files applied on import (inspectable, user-overridable)
   rather than only in code.

---

## ADDENDUM — plugin-inferred rules for multiple photos (2026-08-27)

**This section is not part of the original contract above.** Everything from here down was
inferred by the plugin author from one real export (`multiple-photo-test-2026-08-25.zip`, 3
Point features, 7 photos, 1 audio file) rather than specified by the app author. Treat it as a
best-effort reading of the new format, not a guarantee — replace it with a real specification
if one becomes available, especially the per-photo `ref_photo` semantics (§7 below), which no
observed export actually populates.

### What changed

A feature's `properties` can now carry a `photos` array alongside the existing scalar `photo`:

```jsonc
"photo": "01M0WZQ5Z1KSYZ7G1T11D60VXM.jpg",   // RETAINED — see "Backward/forward compat" below
"ref_photo": null,                            // RETAINED, still scalar
"photos": [
  { "photo": "01M0WZQ5Z1KSYZ7G1T11D60VXM.jpg", "ref_photo": null },
  { "photo": "01M0WZQ5Z20VBH80BPAWCNR17Q.jpg", "ref_photo": null }
]
```

Observed facts (all three features in the fixture):
- `photo == photos[0].photo` in every case — the scalar mirrors the first array entry.
- `photos` entries are objects with exactly two keys, `photo` (string, matches the existing
  `photo` property's join rule — a literal filename in `photos/`, never `obs_id`-derived) and
  `ref_photo` (string or null — the same meaning §7 gives the top-level `ref_photo`, but now
  scoped to one photo instead of one observation).
- Counts across the fixture's three features: 2, 4, 1 — all 7 files present under `photos/`,
  join clean.
- `obs_id` does not equal any photo's basename here either (e.g. obs `…D60VXK` → photos
  `…D60VXM`/`…D60VXN`) — re-confirms §8's "join by literal property value" rule for the new
  array exactly as for the old scalar.

### Backward/forward compatibility assumed by the plugin

- Old exports (e.g. `sample.zip`) have **no `photos` key at all** — tolerate its total absence,
  not just `null`, per §8's existing "properties may be absent in old exports" rule.
- `photos` absent, `null`, or `[]` but the scalar `photo` is non-null → synthesise a single
  `{photo, ref_photo}` entry from the scalars, so no code path ever has to treat "old-format
  single photo" as a special case distinct from "new-format array of one".
- If a future export ever has a non-null scalar `photo` that is **not** present in the `photos`
  array (not observed, but not ruled out), the plugin treats the array as incomplete and
  prepends the scalar rather than silently dropping it — matching this document's long-standing
  principle of never losing data on a format the plugin doesn't fully anticipate.
- An entry that is a bare string rather than `{photo, ref_photo}` is tolerated as
  `{photo: <that string>, ref_photo: null}` (not observed, but cheap to allow).

### §7 revisited: per-photo reference pairing

Every observed `ref_photo` in this fixture is `null`, so **per-photo revisit pairing is
untested against real data.** The plugin's assumption, by direct analogy with the existing
top-level `ref_obs_id`/`ref_photo` semantics: a non-null `photos[i].ref_photo` names a file
**inside the reference session's zip**, and should be resolved directly against that reference
session's photos (by filename) rather than through the coarser `ref_obs_id ↔ obs_id`
observation-level join. When `photos[i].ref_photo` is null, the plugin falls back to that
observation-level join, exactly as the original single-photo format worked.
