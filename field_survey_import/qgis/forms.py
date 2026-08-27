"""Popups: the HTML map tip AND the attribute-form widgets (handoff §6 - "either
or both"; the plugin ships both). Applied to every layer regardless of geometry
type: photo/audio/position_source aren't restricted to Points by the schema (a
surveyor can attach a photo while walking a trace - sample.zip's own data has a
Point note reading "A photo while walking boundary").

The path-resolution expression this all depends on -
`file_path(layer_property(@layer,'path')) || '/' || "<some path column>"` - was
verified against a real imported GeoPackage layer before being relied on here
(not assumed): it resolves to the .gpkg's own directory with no `|layername=`
contamination, the resulting path exists on disk, and (re-verified for the photo
gallery below) it also resolves correctly evaluated once per element inside an
array_foreach lambda.
"""
from qgis.core import QgsEditorWidgetSetup, QgsVectorLayer
from qgis.gui import QgsExternalResourceWidget, QgsFileWidget

# Shared by the map tip and anywhere else a media path needs resolving to an
# absolute, OS-correct path: media is stored relative to the GeoPackage's own
# directory (core/schema.py's photo_path/audio_path doc), so this expression is
# the one portability mechanism everything else builds on.
_RESOLVE_AUDIO_EXPR = (
    "replace(file_path(layer_property(@layer,'path')) || '/' || \"audio_path\", '\\\\', '/')"
)

# Every photo on this feature, as an array (handoff addendum). Deliberately
# `attribute(@feature, 'name')` rather than a bare `"name"` reference: on a layer
# written before this change, photo_paths/photo_count don't exist, and a bare
# reference to a MISSING column is an EVALUATION error - which (exactly like the
# switch-CASE trap below) makes QGIS leave the WHOLE [% %] block verbatim in the
# rendered tip rather than degrading gracefully. attribute() returns NULL for a
# missing field with no error at all, so an old layer falls cleanly through to
# the single photo_path - verified against a real GeoPackage written before
# photo_paths existed. try(expr, fallback) does NOT rescue this (it evaluates to
# NULL, not the fallback) - attribute() is the only fix.
_PHOTO_LIST_EXPR = (
    "coalesce("
    "from_json(attribute(@feature, 'photo_paths')), "
    "CASE WHEN attribute(@feature, 'photo_path') IS NOT NULL "
    "THEN array(attribute(@feature, 'photo_path')) ELSE array() END)"
)

# One <img> per photo, sized down as the count goes up so 2-4 fit two-per-row
# and 5+ fit three-per-row inside the tip's 320px container.
_PHOTO_IMG_EXPR = (
    "'<img src=\"file:///' || @fs_dir || '/' || @element || '\" width=\"'"
    " || (CASE WHEN @fs_n = 1 THEN 300 WHEN @fs_n <= 4 THEN 146 ELSE 96 END)"
    " || '\" style=\"margin:0 2px 2px 0;\"/>'"
)

# array_to_string() over an EMPTY array returns NULL, not '' - hence the
# coalesce wrapper, and the @fs_n = 0 short-circuit so a photo-less feature
# renders nothing rather than an empty <div>.
_PHOTO_GALLERY_INNER_EXPR = (
    "CASE WHEN @fs_n = 0 THEN '' ELSE "
    "(CASE WHEN @fs_n > 1"
    " THEN '<div style=\"color:#666;\">' || @fs_n || ' photos</div>' ELSE '' END)"
    " || coalesce(array_to_string(array_foreach(@fs_photos, " + _PHOTO_IMG_EXPR + "), ''), '')"
    " END"
)

# Built via nested string concatenation, not manual bracket-counting, so each
# with_variable's parens are guaranteed balanced by construction.
_PHOTO_GALLERY_EXPR = (
    "with_variable('fs_n', array_length(@fs_photos), " + _PHOTO_GALLERY_INNER_EXPR + ")"
)
_PHOTO_GALLERY_EXPR = (
    "with_variable('fs_photos', " + _PHOTO_LIST_EXPR + ", " + _PHOTO_GALLERY_EXPR + ")"
)
_PHOTO_GALLERY_EXPR = (
    "with_variable('fs_dir',"
    " replace(file_path(layer_property(@layer,'path')), '\\\\', '/'), "
    + _PHOTO_GALLERY_EXPR
    + ")"
)

_POSITION_SOURCE_CAVEAT = (
    # QGIS's expression engine only supports the searched-CASE form
    # (CASE WHEN cond THEN ... END) - not switch-style CASE x WHEN v THEN ...
    # (verified: the latter is a parser error, "expecting WHEN").
    "CASE"
    " WHEN \"position_source\" = 'gps' THEN"
    "   '±' || round(\"gps_accuracy_m\", 1) || ' m GPS accuracy'"
    " WHEN \"position_source\" = 'map' THEN '±' || round(\"gps_accuracy_m\", 1)"
    "   || ' m map precision — marked on the map, not visited'"
    " WHEN \"position_source\" = 'trace' THEN '±' || round(\"gps_accuracy_m\", 1)"
    "   || ' m (worst vertex on the walk)'"
    " ELSE round(\"gps_accuracy_m\", 1) || ' m (unknown position source)' END"
)

MAP_TIP_HTML = f"""
<div style="max-width:320px; font-family:sans-serif; font-size:11px;">
[% {_PHOTO_GALLERY_EXPR} %]
<b>[% CASE WHEN "note" != '' THEN "note" ELSE '(no note)' END %]</b><br/>
Recorded: [% format_date("recorded_at", 'yyyy-MM-dd HH:mm') %] UTC<br/>
[% {_POSITION_SOURCE_CAVEAT} %]<br/>
[% CASE WHEN "heading_deg" IS NOT NULL
   THEN 'Facing ' || round("heading_deg") || '° (view direction)'
   ELSE 'No compass heading recorded' END %]<br/>
[% CASE WHEN "os_grid_ref" IS NOT NULL THEN "os_grid_ref" || '<br/>' ELSE '' END %]
[% CASE WHEN "feature_label" IS NOT NULL
   THEN 'Feature: ' || "feature_label" || '<br/>' ELSE '' END %]
[% CASE WHEN "audio_path" IS NOT NULL
   THEN '🔊 voice note'
     || CASE WHEN "audio_duration_ms" IS NOT NULL
          THEN ' (' || floor("audio_duration_ms" / 60000) || ':'
               || lpad(to_string(floor(("audio_duration_ms" % 60000) / 1000)), 2, '0') || ')'
          ELSE '' END
     || ' — use Identify › Actions to play'
   ELSE '' END %]
[% CASE WHEN "trace_gaps" IS NOT NULL AND "trace_gaps" != '[]'
   THEN '<br/>⚠ contains inferred (unwalked) segments, drawn dotted' ELSE '' END %]
[% CASE WHEN "revisit_state" IS NOT NULL
   THEN '<br/>Revisit station: ' || "revisit_state"
     || CASE WHEN "revisit_reason" IS NOT NULL THEN ' (' || "revisit_reason" || ')' ELSE '' END
   ELSE '' END %]
</div>
""".strip()


def apply_map_tip(layer: QgsVectorLayer) -> None:
    layer.setMapTipTemplate(MAP_TIP_HTML)


def configure_form_widgets(layer: QgsVectorLayer, gpkg_dir: str) -> None:
    """Attribute-form side of the popup mechanism (§6): ExternalResource photo
    preview, an audio link, and a friendly ValueMap for position_source.
    """
    fields = layer.fields()

    photo_idx = fields.indexFromName("photo_path")
    if photo_idx >= 0:
        config = {
            "DocumentViewer": QgsExternalResourceWidget.Image,
            "DocumentViewerHeight": 200,
            "DocumentViewerWidth": 0,
            "RelativeStorage": QgsFileWidget.RelativeDefaultPath,
            "DefaultRoot": gpkg_dir,
            "FileWidget": True,
            "UseLink": True,
        }
        layer.setEditorWidgetSetup(photo_idx, QgsEditorWidgetSetup("ExternalResource", config))

    audio_idx = fields.indexFromName("audio_path")
    if audio_idx >= 0:
        config = {
            "DocumentViewer": QgsExternalResourceWidget.NoContent,
            "RelativeStorage": QgsFileWidget.RelativeDefaultPath,
            "DefaultRoot": gpkg_dir,
            "FileWidget": True,
            "UseLink": True,
        }
        layer.setEditorWidgetSetup(audio_idx, QgsEditorWidgetSetup("ExternalResource", config))

    pos_idx = fields.indexFromName("position_source")
    if pos_idx >= 0:
        config = {
            "map": [
                {"GPS fix": "gps"},
                {"Marked on map (not visited)": "map"},
                {"Walked trace": "trace"},
            ]
        }
        layer.setEditorWidgetSetup(pos_idx, QgsEditorWidgetSetup("ValueMap", config))

    _mark_survey_fields_read_only(layer)


def _mark_survey_fields_read_only(layer: QgsVectorLayer) -> None:
    """The 25 documented properties (handoff §3) are the app's record of what
    happened in the field - the layer stays editable overall (a user may want to
    add their own columns), but these specific fields shouldn't be hand-edited
    and silently drift from what's in the source zip.
    """
    from ..core.schema import SURVEY_FIELD_NAMES

    form_config = layer.editFormConfig()
    for name in SURVEY_FIELD_NAMES:
        idx = layer.fields().indexFromName(name)
        if idx >= 0:
            form_config.setReadOnly(idx, True)
    layer.setEditFormConfig(form_config)
