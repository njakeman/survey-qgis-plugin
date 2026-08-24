"""Replace / Add alongside / Cancel - shown when a zip's survey_session.id matches
a session already in the destination GeoPackage but the content hash differs
(handoff §4; decision: offer all three, Cancel is the safe default).
"""
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

REPLACE = "replace"
ADD_ALONGSIDE = "add_alongside"
CANCEL = "cancel"


class ConflictDialog(QDialog):
    def __init__(self, parent, session_name: str, existing_rows: list):
        super().__init__(parent)
        self.setWindowTitle("Field Survey Import — already imported")
        self.choice = CANCEL

        latest = existing_rows[0] if existing_rows else {}
        message = (
            f'"{session_name}" has already been imported ' f'({len(existing_rows)} time(s)), '
            "but this zip's contents differ from what's on record "
            f"(previously {latest.get('point_count', 0)} points / "
            f"{latest.get('path_count', 0)} paths / {latest.get('boundary_count', 0)} boundaries, "
            f"imported {latest.get('imported_at', 'unknown time')}).\n\n"
            "What would you like to do?"
        )

        layout = QVBoxLayout(self)
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)

        replace_btn = QPushButton("Replace existing")
        replace_btn.clicked.connect(lambda: self._choose(REPLACE))
        alongside_btn = QPushButton("Add alongside")
        alongside_btn.clicked.connect(lambda: self._choose(ADD_ALONGSIDE))

        layout.addWidget(replace_btn)
        layout.addWidget(alongside_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self, choice: str):
        self.choice = choice
        self.accept()

    @staticmethod
    def ask(parent, session_name: str, existing_rows: list) -> str:
        dialog = ConflictDialog(parent, session_name, existing_rows)
        if dialog.exec_() == QDialog.Accepted:
            return dialog.choice
        return CANCEL
