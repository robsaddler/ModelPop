"""The gallery: find an existing model to start from.

A thin shell over ``GalleryViewModel``. Every decision about what to show, in
what order and in what words was made in the view-model, which is why the whole
journey has tests and this file has none beyond what a widget needs.

Thumbnails are fetched into memory while the window is open and never written
to disk, per ADR-0008.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modelpop.domain.licensing import THE_CLAUSE
from modelpop.presentation.gallery_view_model import Card, GalleryState, GalleryViewModel, Phase

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from modelpop.application.discovery_service import Discovery
    from modelpop.application.repository_ports import Download

__all__ = ["GalleryDialog", "LicensingDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_WARN_STYLE = "color: #E0A458; font-size: 11px;"


class LicensingDialog(QDialog):
    """The one-time clause, and the tick that records it.

    Shown once, and again only if the wording changes. It gates the feature,
    not the results: after this nothing is blocked, filtered or hidden.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog."""
        super().__init__(parent)
        self.setWindowTitle("Before you search for models")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        body = QTextBrowser()
        body.setPlainText(THE_CLAUSE)
        body.setOpenExternalLinks(False)
        body.setMinimumHeight(200)
        layout.addWidget(body)

        self._tick = QCheckBox(
            "I understand and accept responsibility for my use of third-party models."
        )
        layout.addWidget(self._tick)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._tick.toggled.connect(
            self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    @property
    def accepted_terms(self) -> bool:
        """Whether the box is ticked."""
        return self._tick.isChecked()


class _GallerySignals(QObject):
    """Carries the view-model's announcements back to the interface thread."""

    changed = Signal(object)


class GalleryDialog(QDialog):
    """Search several sources, look at what came back, pick one."""

    def __init__(
        self,
        discovery: Discovery,
        into: Path,
        parent: QWidget | None = None,
        on_chosen: Callable[[Download], None] | None = None,
    ) -> None:
        """Build the gallery around a discovery service.

        Args:
            discovery: the use-case layer.
            into: where a chosen model is downloaded. The open project, never
                a library - see ADR-0008.
            parent: the owning window.
            on_chosen: called with the download once it arrives.
        """
        super().__init__(parent)
        self._into = into
        self._on_chosen = on_chosen
        self._view = GalleryViewModel(discovery, _ThreadedRunner(self))

        # A search runs on a worker thread, so the view-model announces from
        # there. Touching a widget from a non-interface thread is undefined,
        # and in practice the list silently stops updating. The signal crosses
        # back, which is what makes the results appear.
        self._signals = _GallerySignals()
        self._signals.changed.connect(self._show)
        self._view.on_change(self._signals.changed.emit)

        self.setWindowTitle("Find a model to start from")
        self.setMinimumSize(820, 560)
        self._build()
        self._show(self._view.state)

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self._box = QLineEdit()
        self._box.setPlaceholderText("a dragon about six inches tall, hollow, in black")
        self._box.returnPressed.connect(self._search)
        search_row.addWidget(self._box, stretch=3)

        go = QPushButton("Search")
        go.clicked.connect(self._search)
        search_row.addWidget(go)
        layout.addLayout(search_row)

        paste_row = QHBoxLayout()
        self._link = QLineEdit()
        self._link.setPlaceholderText("or paste a link to a model page")
        self._link.returnPressed.connect(self._paste)
        paste_row.addWidget(self._link, stretch=3)
        fetch = QPushButton("Open link")
        fetch.clicked.connect(self._paste)
        paste_row.addWidget(fetch)
        layout.addLayout(paste_row)

        self._results = QListWidget()
        self._results.currentRowChanged.connect(self._view.select)
        self._results.itemDoubleClicked.connect(lambda _: self._use())
        layout.addWidget(self._results, stretch=1)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(_HINT_STYLE)
        detail_area = QScrollArea()
        detail_area.setWidget(self._detail)
        detail_area.setWidgetResizable(True)
        detail_area.setMaximumHeight(90)
        layout.addWidget(detail_area)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._status)

        self._problems = QLabel()
        self._problems.setWordWrap(True)
        self._problems.setStyleSheet(_WARN_STYLE)
        layout.addWidget(self._problems)

        buttons = QDialogButtonBox()
        self._use_button = buttons.addButton(
            "Use as a base", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._use_button.clicked.connect(self._use)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.reject)
        layout.addWidget(buttons)

    # --------------------------------------------------------------- commands

    def _search(self) -> None:
        if self._ensure_accepted():
            self._view.search(self._box.text())

    def _paste(self) -> None:
        if self._ensure_accepted():
            self._view.paste_link(self._link.text())

    def _use(self) -> None:
        if self._view.state.can_use_selection:
            self._view.download_selected(self._into, self._chosen)

    def _chosen(self, download: Download) -> None:
        if self._on_chosen is not None:
            self._on_chosen(download)
        self.accept()

    def _ensure_accepted(self) -> bool:
        """Show the clause if it has not been ticked. Returns whether to go on."""
        if not self._view.clause_needs_showing:
            return True
        dialog = LicensingDialog(self)
        if not dialog.exec() or not dialog.accepted_terms:
            return False
        self._view.accept_terms()
        return True

    # ---------------------------------------------------------------- display

    def _show(self, state: GalleryState) -> None:
        self._results.blockSignals(True)
        self._results.clear()
        for card in state.cards:
            self._results.addItem(_row_for(card))
        if 0 <= state.selected < len(state.cards):
            self._results.setCurrentRow(state.selected)
        self._results.blockSignals(False)

        self._status.setText(_status_for(state))
        self._problems.setText("\n".join(state.problems))
        self._problems.setVisible(bool(state.problems))
        self._use_button.setEnabled(state.can_use_selection)

        chosen = state.chosen
        self._detail.setText(
            f"{chosen.subtitle}\n{chosen.licence_detail}\n{chosen.candidate.url}"
            if chosen is not None
            else ""
        )


def _row_for(card: Card) -> QListWidgetItem:
    """One gallery row.

    A list rather than a tiled grid for now: the wording and the ordering are
    what matter, and a grid of thumbnails can be laid over the same view-model
    without changing a line of it.
    """
    bits = [card.title, f"  -  {card.subtitle}", f"  [{card.licence_badge}]"]
    if card.warns_about_derivatives:
        bits.append("  (no derivatives)")
    if card.popularity:
        bits.append(f"  -  {card.popularity}")

    item = QListWidgetItem("".join(bits))
    item.setToolTip(f"{card.licence_detail}\n{card.why}")
    if card.candidate.thumbnail_url:
        item.setData(Qt.ItemDataRole.UserRole, card.candidate.thumbnail_url)
    return item


def _status_for(state: GalleryState) -> str:
    """The line under the list."""
    if state.phase is Phase.NEEDS_ACCEPTANCE:
        return "Accept the licensing note to search."
    if state.phase is Phase.NO_SOURCES:
        missing = ", ".join(state.unconfigured)
        return (
            f"No search source is set up{f' ({missing})' if missing else ''}. "
            "Add a key in Settings, or drop a downloaded model on the window."
        )
    if state.phase is Phase.ALL_SOURCES_DOWN:
        return "No source could be reached. This is not your model's fault - try again shortly."
    if state.phase is Phase.NOTHING_MATCHED:
        return f"{state.message} You can describe it to the generator instead."
    return state.message


class _ThreadedRunner:
    """Runs the view-model's long work off the interface thread.

    The view-model asks for work to be run and does not care how. Here it goes
    to a thread; in a test it runs inline, which is why the whole journey is
    testable with no event loop and no display.

    Both the thread **and** the worker are held for as long as the work lasts.
    Keeping only the thread is the obvious version and it silently does nothing:
    the worker has no parent, so it is collected the moment this returns, and
    the queued ``started`` connection dies with it. The thread then starts, runs
    an empty event loop, and waits forever.
    """

    def __init__(self, owner: QWidget) -> None:
        """Hold the threads and their workers for as long as they run."""
        self._owner = owner
        self._live: list[tuple[QThread, _Worker]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        """Start one piece of work on its own thread."""
        thread = QThread(self._owner)
        worker = _Worker(work)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self._forget(thread))

        self._live.append((thread, worker))
        thread.start()

    def _forget(self, thread: QThread) -> None:
        self._live = [pair for pair in self._live if pair[0] is not thread]

    @property
    def running(self) -> int:
        """How many searches are in flight. For tests, and for diagnostics."""
        return len(self._live)


class _Worker(QObject):
    """Runs one callable on a thread and says when it is finished."""

    done = Signal()

    def __init__(self, work: Callable[[], None]) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        """Do the work, and report finishing even if it raised."""
        try:
            self._work()
        finally:
            self.done.emit()
