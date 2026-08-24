"""Field Survey Import — QGIS plugin entry point.

QGIS's plugin loader imports this module and calls classFactory(iface) to obtain the
plugin instance. Keep this file minimal and free of heavy imports so a broken submodule
doesn't prevent the plugin manager from listing the plugin at all.
"""


def classFactory(iface):  # noqa: N802 - QGIS-mandated name
    from .plugin import FieldSurveyPlugin

    return FieldSurveyPlugin(iface)
