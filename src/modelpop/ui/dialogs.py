"""Dialogs: settings, and generating a part.

Thin. They collect input, hand it to the view-model, and show what came back.
The logic they trigger is tested without any of this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
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
from modelpop.application.mesh_generation_ports import (
    Background,
    Detail,
    GenerationOptions,
)
from modelpop.application.workspace import MAX_VARIANTS
from modelpop.domain.photo_scale import PhotoScale
from modelpop.domain.printer import PrinterConnection
from modelpop.domain.units import Length
from modelpop.printing import ACCESS_CODE_NAME, HOST_NAME, SERIAL_NAME
from modelpop.repositories import (
    ACCESS_WARNING,
    MYMINIFACTORY_KEY_NAME,
    THINGIVERSE_KEY_NAME,
)
from modelpop.ui.measure_dialog import MeasureDialog

if TYPE_CHECKING:
    from pathlib import Path

    from modelpop.ai.secrets import LayeredSecretStore

__all__ = [
    "EditDialog",
    "GenerateDialog",
    "GenerateFromImageDialog",
    "RunLogDialog",
    "SettingsDialog",
]

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
        self,
        secrets: LayeredSecretStore,
        settings: AiSettings,
        parent: QWidget | None = None,
        generation_status: str = "",
        graphics_status: str = "",
    ) -> None:
        """Build the dialog around the current settings.

        Args:
            secrets: where credentials are kept.
            settings: the model and limit choices.
            parent: the owning window.
            generation_status: what the mesh generator found, in words. Passed
                in rather than queried, because probing it starts an
                interpreter and the dialog must open immediately.
            graphics_status: which card the viewport is drawing on. Passed in
                for the same reason, and because only the live window has a
                graphics context to ask.
        """
        super().__init__(parent)
        self._secrets = secrets
        self._settings = settings
        self._generation_status = generation_status
        self._graphics_status = graphics_status

        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_key_group())
        layout.addWidget(self._build_sources_group())
        layout.addWidget(self._build_model_group())
        layout.addWidget(self._build_limits_group())
        layout.addWidget(self._build_generation_group())
        layout.addWidget(self._build_graphics_group())
        layout.addWidget(self._build_printer_group())

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

    def _build_graphics_group(self) -> QGroupBox:
        """Which card the viewport is drawing on.

        Shown because the answer is surprising and otherwise invisible: on a
        laptop with switchable graphics, OpenGL lands on the *integrated* chip
        even with a discrete card present, and nothing the application can do
        changes that. Somebody looking at a slow viewport should be able to
        read what is drawing it instead of guessing.
        """
        group = QGroupBox("The viewport")
        form = QFormLayout(group)

        status = QLabel(self._graphics_status or "Not checked.")
        status.setWordWrap(True)
        form.addRow(status)

        note = QLabel(
            "Measured on this machine: about 70 frames a second at 400,000 "
            "triangles on integrated graphics, which is well ahead of what the "
            "viewport displays. If yours is slow, the switch is in your graphics "
            "driver's control panel, not here."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        form.addRow(note)
        return group

    def _build_printer_group(self) -> QGroupBox:
        """Where the printer is, and whether jobs really go to it.

        The send switch defaults to **off**, and that is the point of it. A
        print is the only thing this application does that cannot be undone
        from inside it: it starts a machine in another room on a spool of
        filament. Everything up to that is a file on disk.

        All three values come from the credential store. Only the access code
        is a secret, but splitting an address from the code that opens it
        across two mechanisms helps nobody, and it is the one place this
        application persists anything at all.
        """
        group = QGroupBox("The printer")
        form = QFormLayout(group)

        self._printer_fields: dict[str, QLineEdit] = {}
        for name, label, secret in (
            (HOST_NAME, "Address", False),
            (SERIAL_NAME, "Serial", False),
            (ACCESS_CODE_NAME, "Access code", True),
        ):
            field = QLineEdit()
            if secret:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            stored = self._secrets.get(name)
            if secret:
                source = self._secrets.source_of(name)
                field.setPlaceholderText(
                    f"already set ({source})" if source != "not set" else "not set"
                )
            elif stored:
                field.setText(stored)

            row = QHBoxLayout()
            row.addWidget(field)
            forget = QPushButton("Forget")
            forget.clicked.connect(lambda _=False, n=name, f=field: self._forget(n, f))
            row.addWidget(forget)
            form.addRow(label, row)
            self._printer_fields[name] = field

        self._send_for_real = QCheckBox("Really send jobs to this printer")
        form.addRow(self._send_for_real)

        note = QLabel(
            "All three are on the printer's own network screen. Leave the box "
            "unticked and ModelPop describes what it would send without sending "
            "it, which is how it behaves until you say otherwise. LAN mode only: "
            "nothing goes through a Bambu account or a server on the internet."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        form.addRow(note)
        return group

    @property
    def printer_connection(self) -> PrinterConnection:
        """The printer as edited, taking what is stored for anything left blank."""
        return PrinterConnection(
            host=self._printer_fields[HOST_NAME].text().strip()
            or (self._secrets.get(HOST_NAME) or ""),
            serial=self._printer_fields[SERIAL_NAME].text().strip()
            or (self._secrets.get(SERIAL_NAME) or ""),
            access_code=self._printer_fields[ACCESS_CODE_NAME].text().strip()
            or (self._secrets.get(ACCESS_CODE_NAME) or ""),
        )

    @property
    def send_for_real(self) -> bool:
        """Whether jobs should actually reach the printer."""
        return self._send_for_real.isChecked()

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

    def _build_generation_group(self) -> QGroupBox:
        """What the picture-to-model environment found.

        Shown whether or not it works, because "not installed" and "no CUDA"
        need different things from the user and a silent absence tells them
        neither.
        """
        group = QGroupBox("Making a model from a picture")
        form = QFormLayout(group)

        status = QLabel(self._generation_status or "Not checked.")
        status.setWordWrap(True)
        form.addRow(status)

        note = QLabel(
            "This runs in its own Python environment with PyTorch, because those "
            "dependencies are several gigabytes and would otherwise be everyone's. "
            "Set it up with: uv run python scripts/setup_generation.py"
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        form.addRow(note)
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

        for name, field in (self._source_fields | self._printer_fields).items():
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


class GenerateFromImageDialog(QDialog):
    """Choose how to turn a picture into a shape.

    Two settings, both of which change the answer rather than decorating it:
    how long to spend, and how to separate the subject from its background.
    """

    def __init__(
        self,
        image_name: str,
        parent: QWidget | None = None,
        image: Path | None = None,
    ) -> None:
        """Build the dialog for one picture.

        Args:
            image_name: what to call it on screen.
            parent: the owning widget.
            image: the file itself, when there is one to measure on. Optional
                so a caller with only a name still gets the rest of the dialog.
        """
        super().__init__(parent)
        self.setWindowTitle("Make a model from a picture")
        self.setMinimumWidth(460)
        self._image = image
        self._measured: PhotoScale | None = None

        form = QFormLayout(self)
        form.addRow(QLabel(f"<b>{image_name}</b>"))

        self._detail = QComboBox()
        for detail in Detail:
            self._detail.addItem(f"{detail.value.title()} - {detail.describe}")
        self._detail.setCurrentIndex(list(Detail).index(Detail.STANDARD))
        form.addRow("Detail", self._detail)

        self._background = QComboBox()
        for background in Background:
            self._background.addItem(f"{background.value.title()} - {background.describe}")
        form.addRow("Background", self._background)

        self._seed = QSpinBox()
        self._seed.setRange(0, 2_000_000_000)
        self._seed.setSpecialValueText("pick one")
        form.addRow("Seed", self._seed)

        self._how_many = QSpinBox()
        self._how_many.setRange(1, MAX_VARIANTS)
        self._how_many.setValue(1)
        self._how_many.setSuffix(" shape(s)")
        self._how_many.setToolTip(
            "Asked twice, the generator answers twice differently. The first "
            "answer is rarely the best one and the only way to tell is to see "
            "the others - but each takes about a minute."
        )
        form.addRow("Make", self._how_many)

        size_row = QHBoxLayout()
        self._size = QDoubleSpinBox()
        self._size.setRange(1.0, 1000.0)
        self._size.setValue(100.0)
        self._size.setSuffix(" mm")
        size_row.addWidget(self._size)

        self._measure_button = QPushButton("Measure it from the photo...")
        self._measure_button.setToolTip(
            "Draw a line along a ruler in the shot and one across the subject, "
            "and the model comes out the size the real thing is"
        )
        self._measure_button.setEnabled(image is not None)
        self._measure_button.clicked.connect(self._measure)
        size_row.addWidget(self._measure_button)
        form.addRow("Size", size_row)

        self._provenance = QLabel()
        self._provenance.setWordWrap(True)
        self._provenance.setStyleSheet(_HINT_STYLE)
        form.addRow(self._provenance)

        note = QLabel(
            "A stated seed makes a run repeatable, which is the only way to iterate "
            "on a picture rather than gamble on it. The first run is slow - it loads "
            "about ten gigabytes of weights."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._say_where_the_size_came_from()

    def options(self) -> GenerationOptions:
        """What the user chose."""
        return GenerationOptions(
            detail=list(Detail)[self._detail.currentIndex()],
            background=list(Background)[self._background.currentIndex()],
            seed=int(self._seed.value()),
            size=Length.mm(self._size.value()),
            size_was_measured=self._measured is not None,
        )

    @property
    def how_many(self) -> int:
        """How many different shapes to ask for.

        One by default. Several is the better answer and costs a minute each,
        so it is offered rather than assumed.
        """
        return int(self._how_many.value())

    @property
    def measured(self) -> PhotoScale | None:
        """What was measured off the photograph, if anything was.

        The distinction the whole feature turns on: a size that came from here
        can be checked with calipers, and one that did not cannot.
        """
        return self._measured

    def _measure(self) -> None:
        """Measure the subject against something of known size in the shot."""
        if self._image is None:
            return
        dialog = MeasureDialog(self._image, self)
        if not dialog.loaded:
            self._provenance.setText("That picture could not be opened to measure on.")
            return
        if not dialog.exec():
            return

        self._measured = dialog.scale
        self._size.setValue(min(self._measured.subject.millimetres, self._size.maximum()))
        self._say_where_the_size_came_from()

    def _say_where_the_size_came_from(self) -> None:
        """Never let a chosen size pass for a measured one."""
        if self._measured is not None:
            self._provenance.setText(f"Measured from the photo. {self._measured.describe()}")
        elif self._image is None:
            self._provenance.setText(
                "A picture has no scale, so this size is chosen, not measured. "
                "Use Resize afterwards if you know the real one."
            )
        else:
            self._provenance.setText(
                "A picture has no scale, so this size is chosen, not measured. "
                "If there is a ruler, a coin or a bank card in the shot, measure "
                "against it instead."
            )


class ResizeDialog(QDialog):
    """Ask how big the model really is.

    Typed rather than dialled, because people say "6 inches" and "150mm", not
    "152.4". The parsing lives in the domain, so this box accepts anything the
    rest of the app understands.
    """

    def __init__(self, current: str = "", parent: QWidget | None = None) -> None:
        """Build the dialog, showing what it measures now."""
        super().__init__(parent)
        self.setWindowTitle("Resize the model")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        if current:
            form.addRow(QLabel(f"It is currently <b>{current}</b> at its largest."))

        self._size = QLineEdit()
        self._size.setPlaceholderText("6 inches, or 150mm")
        self._size.textChanged.connect(self._check)
        form.addRow("Make it", self._size)

        self._reading = QLabel()
        self._reading.setStyleSheet(_HINT_STYLE)
        form.addRow(self._reading)

        note = QLabel(
            "Scales the whole model so it stands that tall, keeping its "
            "proportions. Useful for anything that came out of a picture, where "
            "the scale is arbitrary until you say otherwise."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        form.addRow(note)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        form.addRow(self._buttons)

    def _check(self, text: str) -> None:
        """Show what was understood, as it is typed.

        Reading the number back is the only way the user finds out that "6" on
        its own means six millimetres before they apply it.
        """
        size = self.size_wanted
        usable = size is not None and size.millimetres > 0
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(usable)
        if not text.strip():
            self._reading.setText("")
        elif usable and size is not None:
            self._reading.setText(f"Read as {size.format()}.")
        else:
            self._reading.setText("Not understood. Try \u201c6 inches\u201d or \u201c150mm\u201d.")

    @property
    def size_wanted(self) -> Length | None:
        """What was typed, as a length, or ``None`` if it made no sense."""
        text = self._size.text().strip()
        if not text:
            return None
        try:
            return Length.parse(text)
        except (ValueError, TypeError):
            return None
