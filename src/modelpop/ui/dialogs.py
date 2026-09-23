"""Dialogs: settings, and generating a part.

Thin. They collect input, hand it to the view-model, and show what came back.
The logic they trigger is tested without any of this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from modelpop.ai import ANTHROPIC_KEY_NAME
from modelpop.application.ai_ports import AiSettings, ModelChoice, ModelRole
from modelpop.repositories import (
    ACCESS_WARNING,
    MYMINIFACTORY_KEY_NAME,
    THINGIVERSE_KEY_NAME,
)

if TYPE_CHECKING:
    from modelpop.ai.secrets import LayeredSecretStore

__all__ = ["EditDialog", "GenerateDialog", "RunLogDialog", "SettingsDialog"]

_MODELS = [
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5",
    "claude-fable-5-1",
]
_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# palette(mid) resolves almost to the background on a dark theme, which makes
# explanatory text invisible - the opposite of what a hint is for.
_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"


class SettingsDialog(QDialog):
    """Where the user puts their API key and picks models.

    The key is written to the OS credential store, never to a file in the
    project. The field shows whether one is already set without ever displaying
    it: a key on screen is a key in a screenshot.
    """

    def __init__(
        self, secrets: LayeredSecretStore, settings: AiSettings, parent: QWidget | None = None
    ) -> None:
        """Build the dialog around the current settings."""
        super().__init__(parent)
        self._secrets = secrets
        self._settings = settings

        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_key_group())
        layout.addWidget(self._build_sources_group())
        layout.addWidget(self._build_model_group())
        layout.addWidget(self._build_limits_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ build

    def _build_key_group(self) -> QGroupBox:
        group = QGroupBox("Anthropic API key")
        form = QFormLayout(group)

        self._key_field = QLineEdit()
        self._key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_field.setPlaceholderText("sk-ant-...")

        source = self._secrets.source_of(ANTHROPIC_KEY_NAME)
        if source != "not set":
            self._key_field.setPlaceholderText(f"a key is already set ({source})")

        row = QHBoxLayout()
        row.addWidget(self._key_field)
        forget = QPushButton("Forget")
        forget.clicked.connect(self._forget_key)
        row.addWidget(forget)
        form.addRow("Key", row)

        note = QLabel(
            "Stored in the operating system credential store, never in a file in "
            "this project. An exported ANTHROPIC_API_KEY takes precedence."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        form.addRow(note)
        return group

    def _build_sources_group(self) -> QGroupBox:
        """Credentials for the model repositories.

        Both are free and optional, and the gallery works with neither - a
        downloaded file dropped on the window needs no account at all. The
        Thingiverse warning is shown in full rather than summarised, because
        granting an app full read and write on your account to run a search is
        a surprising thing and burying it would be wrong.
        """
        group = QGroupBox("Where to search for models")
        form = QFormLayout(group)

        self._source_fields: dict[str, QLineEdit] = {}
        for name, label, hint in (
            (
                MYMINIFACTORY_KEY_NAME,
                "MyMiniFactory key",
                "Free, from myminifactory.com/settings/developer. Searching needs only "
                "this key; downloading in the app needs a connected account.",
            ),
            (
                THINGIVERSE_KEY_NAME,
                "Thingiverse token",
                ACCESS_WARNING,
            ),
        ):
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            source = self._secrets.source_of(name)
            field.setPlaceholderText(
                f"already set ({source})" if source != "not set" else "not set"
            )

            row = QHBoxLayout()
            row.addWidget(field)
            forget = QPushButton("Forget")
            forget.clicked.connect(lambda _=False, n=name, f=field: self._forget(n, f))
            row.addWidget(forget)
            form.addRow(label, row)

            note = QLabel(hint)
            note.setWordWrap(True)
            note.setStyleSheet(_HINT_STYLE)
            form.addRow(note)
            self._source_fields[name] = field

        aside = QLabel(
            "MakerWorld has no public API and its terms do not permit automated "
            "access, so ModelPop never contacts it. Download the 3MF yourself and "
            "drop it on the window - it carries the Bambu print profile, which is "
            "better than anything a search would return."
        )
        aside.setWordWrap(True)
        aside.setStyleSheet(_HINT_STYLE)
        form.addRow(aside)
        return group

    def _build_model_group(self) -> QGroupBox:
        group = QGroupBox("Models")
        form = QFormLayout(group)
        self._model_fields: dict[ModelRole, tuple[QComboBox, QComboBox]] = {}

        labels = {
            ModelRole.CAD_CODEGEN: "Writing CAD code",
            ModelRole.CRITIQUE: "Judging a result",
            ModelRole.ROUTING: "Understanding the request",
            ModelRole.NAMING: "Naming things",
        }
        for role, label in labels.items():
            choice = self._settings.choice_for(role)

            model = QComboBox()
            model.addItems(_MODELS)
            model.setEditable(True)
            model.setCurrentText(choice.model)

            effort = QComboBox()
            effort.addItems(_EFFORTS)
            effort.setCurrentText(choice.effort)

            row = QHBoxLayout()
            row.addWidget(model, stretch=3)
            row.addWidget(QLabel("effort"))
            row.addWidget(effort, stretch=1)
            form.addRow(label, row)
            self._model_fields[role] = (model, effort)

        return group

    def _build_limits_group(self) -> QGroupBox:
        group = QGroupBox("Limits")
        form = QFormLayout(group)

        self._attempts = QSpinBox()
        self._attempts.setRange(1, 20)
        self._attempts.setValue(self._settings.max_attempts)
        form.addRow("Attempts per part", self._attempts)

        self._spend = QDoubleSpinBox()
        self._spend.setRange(0.0, 100.0)
        self._spend.setSingleStep(0.5)
        self._spend.setPrefix("$ ")
        self._spend.setSpecialValueText("no limit")
        self._spend.setValue(self._settings.spend_limit_usd)
        form.addRow("Spend limit per part", self._spend)

        return group

    # ----------------------------------------------------------------- saving

    def _forget_key(self) -> None:
        self._secrets.delete(ANTHROPIC_KEY_NAME)
        self._key_field.clear()
        self._key_field.setPlaceholderText("no key set")

    def _forget(self, name: str, field: QLineEdit) -> None:
        self._secrets.delete(name)
        field.clear()
        field.setPlaceholderText("not set")

    def _save(self) -> None:
        typed = self._key_field.text().strip()
        if typed:
            try:
                self._secrets.set(ANTHROPIC_KEY_NAME, typed)
            except RuntimeError:
                # The credential store refused. Better to say nothing was saved
                # than to let the user believe it was.
                self._key_field.setPlaceholderText("could not save the key")
                return

        for name, field in self._source_fields.items():
            value = field.text().strip()
            if not value:
                continue
            try:
                self._secrets.set(name, value)
            except RuntimeError:
                field.clear()
                field.setPlaceholderText("could not save it")
                return

        self.accept()

    def settings(self) -> AiSettings:
        """The settings as edited."""
        roles = {
            role: ModelChoice(
                model=model.currentText().strip(),
                effort=effort.currentText(),
                max_tokens=self._settings.choice_for(role).max_tokens,
            )
            for role, (model, effort) in self._model_fields.items()
        }
        return AiSettings(
            roles=roles,
            max_attempts=self._attempts.value(),
            spend_limit_usd=self._spend.value(),
        )


class GenerateDialog(QDialog):
    """Describe a part and let the model write it."""

    EXAMPLES = (
        "a wall bracket for a 35 mm pipe, 60 mm wide, with two M4 holes 40 mm apart",
        "a rectangular box 80 x 50 x 30 mm with 2 mm walls and an open top",
        "a hexagonal knob 30 mm across with a 6 mm shaft hole and a 20 mm skirt",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog."""
        super().__init__(parent)
        self.setWindowTitle("Generate a part")
        self.setMinimumSize(620, 420)

        layout = QVBoxLayout(self)

        heading = QLabel("Describe the part, with its dimensions.")
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)

        note = QLabel(
            "State exact sizes in millimetres. The finished solid is measured "
            "against them and corrected automatically if it does not match. "
            "This works for mechanical parts; for figurines and organic shapes, "
            "search for a model instead."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        layout.addWidget(note)

        self._request = QPlainTextEdit()
        self._request.setPlaceholderText(self.EXAMPLES[0])
        layout.addWidget(self._request, stretch=1)

        layout.addWidget(QLabel("Examples:"))
        for example in self.EXAMPLES:
            button = QPushButton(example)
            button.setStyleSheet("text-align: left; padding: 6px;")
            button.clicked.connect(lambda _=False, text=example: self._request.setPlainText(text))
            layout.addWidget(button)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Generate")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._request.textChanged.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self._request.toPlainText().strip()))

    def request(self) -> str:
        """What the user asked for."""
        return self._request.toPlainText().strip()


class RunLogDialog(QDialog):
    """Show what a generation run did, and the script it settled on."""

    def __init__(self, log: str, script: str, parent: QWidget | None = None) -> None:
        """Build the dialog around a finished run."""
        super().__init__(parent)
        self.setWindowTitle("How this part was made")
        self.setMinimumSize(760, 560)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Attempts</b>"))

        attempts = QPlainTextEdit(log)
        attempts.setReadOnly(True)
        attempts.setMaximumHeight(160)
        layout.addWidget(attempts)

        layout.addWidget(QLabel("<b>The script</b>"))
        code = QPlainTextEdit(script)
        code.setReadOnly(True)
        code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(code, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)


class EditDialog(QDialog):
    """Describe a change to the part on screen."""

    EXAMPLES = (
        "make the walls 3 mm thick",
        "round the vertical corners with a 4 mm radius",
        "move the holes 10 mm further apart",
        "add a 2 mm chamfer to the top edges",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog."""
        super().__init__(parent)
        self.setWindowTitle("Change this part")
        self.setMinimumSize(560, 340)

        layout = QVBoxLayout(self)

        heading = QLabel("What should change?")
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)

        note = QLabel(
            "The script that produced this part is rewritten and re-checked, so "
            "a change cannot quietly break the dimensions or stop it fitting the "
            "printer. Undo is one step away if you do not like the result."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        layout.addWidget(note)

        self._instruction = QPlainTextEdit()
        self._instruction.setPlaceholderText(self.EXAMPLES[0])
        layout.addWidget(self._instruction, stretch=1)

        for example in self.EXAMPLES:
            button = QPushButton(example)
            button.setStyleSheet("text-align: left; padding: 5px;")
            button.clicked.connect(
                lambda _=False, text=example: self._instruction.setPlainText(text)
            )
            layout.addWidget(button)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Change it")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._instruction.textChanged.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(self._instruction.toPlainText().strip()))

    def instruction(self) -> str:
        """The change the user described."""
        return self._instruction.toPlainText().strip()
