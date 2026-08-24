"""Then-vs-now photo comparison for revisit sessions (handoff §7): a genuinely
valuable stretch feature, but plain import must not depend on the reference
being present - so this dialog is equally happy to say plainly what's missing.
"""
from pathlib import Path

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

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
        now_photo: Path | None,
        then_photo: Path | None,
        then_reason: str | None,
        then_session_name: str | None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Field Survey — Then vs now")

        layout = QHBoxLayout(self)
        then_title = f"Then — {then_session_name}" if then_session_name else "Then"
        layout.addLayout(_photo_panel(then_title, then_photo, then_reason))
        layout.addLayout(_photo_panel("Now", now_photo, "No photo on this observation"))


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
        dialog = PhotoCompareDialog(
            None,
            now_photo=None,
            then_photo=None,
            then_reason="This observation isn't part of a revisit (no ref_obs_id).",
            then_session_name=None,
        )
        dialog.exec_()
        return

    gpkg_path = Path(layer.source().split("|")[0])
    session_id = feature["session_id"] if "session_id" in field_names else None

    from ..qgis import writer

    reference_session_id = None
    revisit_rows = writer.query_sessions_by_session_id(gpkg_path, session_id) if session_id else []
    # query_sessions_by_session_id reads fs_sessions; the reference id lives in
    # fs_revisits for THIS session's own import_id, so look that up specifically.
    if revisit_rows:
        import_id = revisit_rows[0]["import_id"]
        reference_session_id = _lookup_reference_session_id(gpkg_path, import_id)

    ref = revisit.resolve_reference_photo(gpkg_path, reference_session_id, ref_obs_id)

    now_photo_path = feature["photo_path"] if "photo_path" in field_names else None
    now_photo = gpkg_path.parent / now_photo_path if now_photo_path else None

    dialog = PhotoCompareDialog(
        None,
        now_photo=now_photo,
        then_photo=ref.path,
        then_reason=ref.not_found_reason,
        then_session_name=ref.reference_session_name,
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
