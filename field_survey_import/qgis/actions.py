"""Registers the "Play voice note" QgsAction (handoff §6). Passes only the layer
id and integer fid to Python - never attribute values interpolated into the
action's source - so there's no quoting/escaping hazard from a note containing
a quote character or worse.
"""
from qgis.core import QgsAction, QgsVectorLayer

AUDIO_ACTION_NAME = "Play voice note"
COMPARE_ACTION_NAME = "Compare with reference photo"


def register_audio_action(layer: QgsVectorLayer) -> None:
    manager = layer.actions()
    if any(a.name() == AUDIO_ACTION_NAME for a in manager.actions()):
        return  # re-applying styles/forms on an already-configured layer

    action = QgsAction(
        QgsAction.GenericPython,
        AUDIO_ACTION_NAME,
        "from field_survey_import.ui.audio_player import play_from_action\n"
        "play_from_action('[% @layer_id %]', [% $id %])",
    )
    action.setActionScopes({"Feature", "Canvas"})
    manager.addAction(action)


def register_compare_action(layer: QgsVectorLayer) -> None:
    """Only meaningful on a revisit session's points layer (handoff §7) - the
    caller decides when to call this (import_dialog.py, gated on
    result.is_revisit), so it never appears on an ordinary import's layers.
    """
    manager = layer.actions()
    if any(a.name() == COMPARE_ACTION_NAME for a in manager.actions()):
        return

    action = QgsAction(
        QgsAction.GenericPython,
        COMPARE_ACTION_NAME,
        "from field_survey_import.ui.photo_compare import compare_from_action\n"
        "compare_from_action('[% @layer_id %]', [% $id %])",
    )
    action.setActionScopes({"Feature", "Canvas"})
    manager.addAction(action)
