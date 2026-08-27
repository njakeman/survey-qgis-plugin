"""Then-vs-now photo comparison for revisit sessions (handoff §7): a genuinely
valuable stretch feature, but plain import must not depend on the reference
being present - so this dialog is equally happy to say plainly what's missing.

Renders one Then/Now row per photo on the observation (handoff addendum -
per-photo ref_photo), not just the first, in a QScrollArea so an observation
with several photos doesn't blow out the dialog's height.
"""
import json
from pathlib import Path

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..qgis import revisit

_PHOTO_WIDTH = 380


def _photo_panel(title: str, path: Path | None, reason: str | None) -> QVBoxLayout:
    layout = QVBoxLayout()
    heading = QLabel(f"<b>{title}</b>")
    layout.addWidget(heading)

    if path is not None and path.exists():
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            scaled = pixmap.scaledToWidth(_PHOTO_WIDTH, Qt.SmoothTransformation)
            image_label = QLabel()
            image_label.setPixmap(scaled)
            layout.addWidget(image_label)
        else:
            layout.addWidget(QLabel("(photo file could not be read)"))
    else:
        message = QLabel(reason or "No photo available")
        message.setWordWrap(True)
        message.setFixedWidth(_PHOTO_WIDTH)
        layout.addWidget(message)

    return layout


class PhotoCompareDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        pairs: list,  # list[revisit.PhotoPair]
        reference_session_name: str | None,
        reference_not_found_reason: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Field Survey — Then vs now")

        outer = QVBoxLayout(self)
        then_title_base = (
            f"Then — {reference_session_name}" if reference_session_name else "Then"
        )

        if reference_not_found_reason is not None:
            banner = QLabel(reference_not_found_reason)
            banner.setWordWrap(True)
            outer.addWidget(banner)

        if not pairs:
            outer.addWidget(QLabel("This observation isn't part of a revisit (no ref_obs_id)."))
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rows_layout = QVBoxLayout(content)

        multi = len(pairs) > 1
        for pair in pairs:
            row = QHBoxLayout()
            then_title = then_title_base
            now_title = f"Now ({pair.seq + 1}/{len(pairs)})" if multi else "Now"
            row.addLayout(_photo_panel(then_title, pair.then_path, pair.not_found_reason))
            row.addLayout(
                _photo_panel(now_title, pair.now_path, "No photo on this observation")
            )
            rows_layout.addLayout(row)

        scroll.setWidget(content)
        outer.addWidget(scroll)


def compare_from_action(layer_id: str, fid: int) -> None:
    """Entry point for the "Compare with reference photo" QgsAction. Takes only
    the layer id and integer fid - same reasoning as play_from_action.
    """
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None:
        return
    feature = layer.getFeature(fid)
    if not feature.isValid():
        return

    field_names = feature.fields().names()
    ref_obs_id = feature["ref_obs_id"] if "ref_obs_id" in field_names else None
    if not ref_obs_id:
        dialog = PhotoCompareDialog(None, pairs=[], reference_session_name=None)
        dialog.exec_()
        return

    gpkg_path = Path(layer.source().split("|")[0])
    session_id = feature["session_id"] if "session_id" in field_names else None

    from ..qgis import writer

    # Prefer the custom property import_dialog.py sets on load - the most-recent
    # fs_sessions row for session_id is the wrong import once a session has been
    # "Add alongside"-ed more than once. Fall back to that query for a layer
    # opened outside the plugin (e.g. from the Browser), where the property
    # was never set.
    import_id = layer.customProperty("field_survey/import_id")
    if not import_id and session_id:
        revisit_rows = writer.query_sessions_by_session_id(gpkg_path, session_id)
        import_id = revisit_rows[0]["import_id"] if revisit_rows else None

    reference_session_id = _lookup_reference_session_id(gpkg_path, import_id) if import_id else None

    photos_raw = feature["photos"] if "photos" in field_names else None
    photo_paths_raw = feature["photo_paths"] if "photo_paths" in field_names else None
    ref_photos = [e.get("ref_photo") for e in json.loads(photos_raw)] if photos_raw else []
    photo_paths = json.loads(photo_paths_raw) if photo_paths_raw else []
    # Both are written from the same obs.photos/media_paths in the same order
    # (import_flow.py) - a length mismatch would mean extraction silently
    # dropped a photo, which resolve_media's MediaJoinError should already have
    # prevented; zip() just declines to guess if it ever happens anyway.
    now_photos = tuple(zip(photo_paths, ref_photos))

    comparison = revisit.resolve_reference_photos(
        gpkg_path,
        reference_session_id=reference_session_id,
        ref_obs_id=ref_obs_id,
        now_photos=now_photos,
    )

    dialog = PhotoCompareDialog(
        None,
        pairs=list(comparison.pairs),
        reference_session_name=comparison.reference_session_name,
        reference_not_found_reason=comparison.not_found_reason,
    )
    dialog.exec_()


def _lookup_reference_session_id(gpkg_path: Path, import_id: str) -> str | None:
    from osgeo import ogr

    ds = ogr.Open(str(gpkg_path))
    if ds is None:
        return None
    try:
        layer = ds.GetLayerByName("fs_revisits")
        if layer is None:
            return None
        escaped = import_id.replace("'", "''")
        layer.SetAttributeFilter(f"import_id = '{escaped}'")
        feature = next(iter(layer), None)
        return feature.GetField("reference_session_id") if feature is not None else None
    finally:
        ds = None  # noqa: F841
