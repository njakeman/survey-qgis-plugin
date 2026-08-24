"""Audio playback wiring (handoff §3, §6). Doesn't attempt to actually hear
anything play (no audio hardware in CI) - verifies the resolvable-path logic,
container routing decision, and the QgsAction registration/idempotency.
"""
from pathlib import Path

from qgis.core import QgsVectorLayer

from field_survey_import.qgis import actions, import_flow
from field_survey_import.ui.audio_player import (
    _EMBEDDED_CAPABLE_EXTENSIONS,
    _format_ms,
    resolve_audio_path,
)


def test_format_ms():
    assert _format_ms(None) == ""
    assert _format_ms(0) == "0:00"
    assert _format_ms(12400) == "0:12"
    assert _format_ms(8400) == "0:08"
    assert _format_ms(65000) == "1:05"


def test_only_m4a_is_embedded_capable():
    assert ".m4a" in _EMBEDDED_CAPABLE_EXTENSIONS
    assert ".webm" not in _EMBEDDED_CAPABLE_EXTENSIONS  # verified: not decodable on this build


def test_resolve_audio_path_for_real_feature(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)
    table = next(wl.table_name for wl in result.layers if wl.geometry_type.value == "Point")
    layer = QgsVectorLayer(f"{gpkg_path}|layername={table}", "pts", "ogr")

    feature_with_audio = next(f for f in layer.getFeatures() if f["audio_path"])
    path = resolve_audio_path(layer, feature_with_audio)
    assert path is not None
    assert path.exists()
    assert path.suffix == ".webm"  # every sample.zip audio file is webm

    feature_without_audio = next(f for f in layer.getFeatures() if not f["audio_path"])
    assert resolve_audio_path(layer, feature_without_audio) is None


def test_register_audio_action_is_idempotent(sample_zip_path, tmp_path):
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)
    table = next(wl.table_name for wl in result.layers if wl.geometry_type.value == "Point")
    layer = QgsVectorLayer(f"{gpkg_path}|layername={table}", "pts", "ogr")

    actions.register_audio_action(layer)
    actions.register_audio_action(layer)  # applying styles/forms twice must not duplicate
    matching = [a for a in layer.actions().actions() if a.name() == actions.AUDIO_ACTION_NAME]
    assert len(matching) == 1


def test_audio_action_command_only_passes_layer_id_and_fid(sample_zip_path, tmp_path):
    # No feature attribute value is ever interpolated into the action's Python
    # source - only [% @layer_id %] and [% $id %] - so a note/label containing a
    # quote can never break out of the action's command string.
    gpkg_path = tmp_path / "survey.gpkg"
    result = import_flow.import_zip(sample_zip_path, gpkg_path)
    table = next(wl.table_name for wl in result.layers if wl.geometry_type.value == "Point")
    layer = QgsVectorLayer(f"{gpkg_path}|layername={table}", "pts", "ogr")
    actions.register_audio_action(layer)

    action = next(a for a in layer.actions().actions() if a.name() == actions.AUDIO_ACTION_NAME)
    command = action.command()
    assert "@layer_id" in command
    assert "$id" in command
    for field_name in ("note", "photo", "audio", "feature_label"):
        assert f'"{field_name}"' not in command


def test_media_dock_constructs_and_routes_webm_to_system_player(fake_iface, monkeypatch):
    from field_survey_import.ui import audio_player

    audio_player.MediaDock.reset_instance_for_tests()
    dock = audio_player.MediaDock.instance(fake_iface.mainWindow())
    assert dock.windowTitle() == "Field Survey — Voice note"

    opened = []
    monkeypatch.setattr(audio_player.QDesktopServices, "openUrl", lambda url: opened.append(url))

    # A .webm file must never even attempt the embedded player - it should route
    # straight to the (mocked) system player without touching QMediaPlayer.
    fake_path = Path(__file__)  # any real, existing file - content doesn't matter for routing
    webm_path = fake_path.with_suffix(".webm")
    webm_path.write_bytes(b"not real webm data")
    try:
        dock.play(webm_path, duration_ms=5000)
        assert dock.duration_label.text() == "0:05"
        assert "isn't supported by the embedded player" in dock.status_label.text()
        assert len(opened) == 1  # the fallback really did fire, not silently skipped
    finally:
        webm_path.unlink(missing_ok=True)
        audio_player.MediaDock.reset_instance_for_tests()
