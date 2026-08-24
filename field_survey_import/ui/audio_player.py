"""Voice-note playback (handoff §3, §6). Verified on this machine's QGIS 3.44
build: Qt Multimedia's only backends are DirectShow/Media Foundation, which
decode AAC (.m4a) but not Opus (.webm) - and every audio file in sample.zip is
.webm. So the routing is: .m4a attempts the embedded player; anything else
(chiefly .webm) goes straight to the system player, which handles WebM/Opus
fine on Windows. "Open in system player" stays permanently visible either way,
not merely a fallback button that appears on failure (decision: this must work
regardless of which containers a given machine's Qt build can decode).

Failure detection: mediaStatusChanged reaching InvalidMedia, the error signal,
or (belt and braces) a watchdog timer if playback never starts - all three
verified to fire against a deliberately unplayable file before being relied on.
"""
from pathlib import Path

from qgis.core import QgsFeature, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    from qgis.PyQt.QtMultimedia import QMediaContent, QMediaPlayer

    _HAS_QT_MULTIMEDIA = True
except ImportError:  # pragma: no cover - depends on the local Qt build
    _HAS_QT_MULTIMEDIA = False

# Containers the embedded player is even attempted for. Extended to cover a
# future Qt build gaining Opus support would need re-verifying, not assuming -
# see the plan's audio de-risking check.
_EMBEDDED_CAPABLE_EXTENSIONS = {".m4a"}

_WATCHDOG_MS = 2500


def _format_ms(duration_ms) -> str:
    if duration_ms is None:
        return ""
    total_seconds = int(duration_ms) // 1000
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def resolve_audio_path(layer: QgsVectorLayer, feature: QgsFeature) -> Path | None:
    audio_path = feature["audio_path"] if "audio_path" in feature.fields().names() else None
    if not audio_path:
        return None
    gpkg_path = layer.source().split("|")[0]
    return Path(gpkg_path).parent / audio_path


def play_from_action(layer_id: str, fid: int) -> None:
    """Entry point for the QgsAction registered by qgis/actions.py. Deliberately
    takes only a layer id and integer fid - never attribute values interpolated
    into the action's Python source - so there's no quoting/escaping hazard.
    """
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None:
        return
    feature = layer.getFeature(fid)
    if not feature.isValid():
        return
    path = resolve_audio_path(layer, feature)
    if path is None or not path.exists():
        return
    field_names = feature.fields().names()
    duration_ms = feature["audio_duration_ms"] if "audio_duration_ms" in field_names else None

    dock = MediaDock.instance()
    dock.play(path, duration_ms=duration_ms)
    dock.show()
    dock.raise_()


class MediaDock(QDockWidget):
    """A single shared dock, reused across plays rather than one per feature."""

    _instance = None

    @classmethod
    def instance(cls, parent=None) -> "MediaDock":
        if cls._instance is None:
            cls._instance = MediaDock(parent)
        return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        if cls._instance is not None:
            cls._instance.deleteLater()
            cls._instance = None

    def __init__(self, parent=None):
        super().__init__("Field Survey — Voice note", parent)
        self.setAllowedAreas(Qt.AllDockWidgetAreas)

        self._current_path: Path | None = None
        self._player = QMediaPlayer(self) if _HAS_QT_MULTIMEDIA else None
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog_timeout)

        body = QWidget(self)
        layout = QVBoxLayout(body)

        info_row = QHBoxLayout()
        self.filename_label = QLabel("")
        self.duration_label = QLabel("")
        info_row.addWidget(self.filename_label, stretch=1)
        info_row.addWidget(self.duration_label)
        layout.addLayout(info_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        controls_row = QHBoxLayout()
        self.play_pause_button = QPushButton("Play / Pause")
        self.play_pause_button.clicked.connect(self._toggle_play_pause)
        self.slider = QSlider(Qt.Horizontal)
        self.system_player_button = QPushButton("Open in system player")
        self.system_player_button.clicked.connect(self._open_in_system_player)
        controls_row.addWidget(self.play_pause_button)
        controls_row.addWidget(self.slider, stretch=1)
        controls_row.addWidget(self.system_player_button)
        layout.addLayout(controls_row)

        self.setWidget(body)

        if self._player is not None:
            self._player.mediaStatusChanged.connect(self._on_status_changed)
            self._player.error.connect(self._on_error)
            self._player.positionChanged.connect(self._on_position_changed)
            self._player.durationChanged.connect(self._on_duration_changed)
            self.slider.sliderMoved.connect(self._player.setPosition)
        else:
            self.play_pause_button.setEnabled(False)
            self.slider.setEnabled(False)

    # -- public API ---------------------------------------------------------------

    def play(self, path: Path, *, duration_ms: int | None) -> None:
        self._current_path = path
        self.filename_label.setText(path.name)
        self.duration_label.setText(_format_ms(duration_ms))
        self.status_label.setText("")

        if self._player is not None and path.suffix.lower() in _EMBEDDED_CAPABLE_EXTENSIONS:
            self._play_embedded(path)
        else:
            reason = f"'{path.suffix}' isn't supported by the embedded player on this system."
            self._play_system(path, reason=reason)

    # -- embedded playback ---------------------------------------------------------

    def _play_embedded(self, path: Path) -> None:
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path))))
        self._player.play()
        self.status_label.setText("Playing…")
        self._watchdog.start(_WATCHDOG_MS)

    def _toggle_play_pause(self) -> None:
        if self._player is None or self._current_path is None:
            return
        if self._player.state() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_status_changed(self, status) -> None:
        if status == QMediaPlayer.InvalidMedia:
            self._watchdog.stop()
            self._play_system(
                self._current_path, reason="This system's Qt build could not decode this file."
            )
        elif status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self._watchdog.stop()

    def _on_error(self, _error) -> None:
        if self._player is not None and self._player.error() != QMediaPlayer.NoError:
            self._watchdog.stop()
            reason = self._player.errorString() or "Playback error."
            self._play_system(self._current_path, reason=reason)

    def _on_watchdog_timeout(self) -> None:
        self._play_system(self._current_path, reason="The embedded player didn't respond in time.")

    def _on_position_changed(self, position_ms: int) -> None:
        if self._player is not None and self._player.duration() > 0:
            self.slider.setMaximum(self._player.duration())
            self.slider.setValue(position_ms)

    def _on_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self.slider.setMaximum(duration_ms)

    # -- fallback -------------------------------------------------------------------

    def _play_system(self, path: Path | None, *, reason: str) -> None:
        if path is None:
            return
        if self._player is not None:
            self._player.stop()
        self.status_label.setText(f"{reason} Opened in your system player instead.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_in_system_player(self) -> None:
        if self._current_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_path)))
