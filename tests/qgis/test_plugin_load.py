"""Milestone 0 verification: the plugin package imports cleanly and its QGIS lifecycle
(initGui / unload) runs without error against a stand-in QgisInterface.
"""
from field_survey_import import classFactory
from field_survey_import.ui.import_dialog import ImportDialog


def test_class_factory_returns_plugin(fake_iface):
    plugin = classFactory(fake_iface)
    assert plugin.iface is fake_iface


def test_init_gui_and_unload_cycle_is_clean(fake_iface):
    plugin = classFactory(fake_iface)

    plugin.initGui()
    assert len(plugin.actions) == 1
    assert plugin._provider is not None

    plugin.unload()
    assert plugin.actions == []
    assert plugin._provider is None


def test_import_dialog_constructs(fake_iface):
    # Constructed but not exec_()'d - a modal loop would block the test process.
    dialog = ImportDialog(fake_iface.mainWindow())
    assert dialog.windowTitle() == "Import Field Survey zip…"
