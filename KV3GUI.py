from __future__ import annotations

import math
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from threading import Thread
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QFileDialog, QHeaderView, QHBoxLayout,
    QListWidget, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "Kv3Tool" / "Kv3Tool.csproj"
DLL = ROOT / "Kv3Tool" / "bin" / "Release" / "net10.0" / "Kv3Tool.dll"
ENTRIES = re.compile(r"\b(Unpublished|SavedLastUsed|Favorites)\s*=|#\[\s*([0-9a-fA-F\s]*?)\]")
FLOAT = struct.Struct("<f")


def varint(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError("Invalid protobuf varint")


def fields(data: bytes):
    data = memoryview(data)
    pos = 0
    while pos < len(data):
        start = pos
        tag, pos = read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Invalid protobuf field number")
        if wire == 0:
            payload_start = pos
            _, pos = read_varint(data, pos)
        elif wire == 2:
            length, pos = read_varint(data, pos)
            payload_start = pos
            pos += length
        elif wire in (1, 5):
            payload_start = pos
            pos += 8 if wire == 1 else 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        if pos > len(data):
            raise ValueError("Truncated protobuf field")
        yield number, wire, data[payload_start:pos], data[start:pos]


def payload(data, number: int, wire: int = 2):
    for field, kind, value, _ in fields(data):
        if field == number and kind == wire:
            return value
    return b""


def replace_field(data: bytes, number: int, wire: int, value: bytes) -> bytes:
    encoded = varint((number << 3) | wire)
    if wire == 2:
        encoded += varint(len(value))
    encoded += value
    result = bytearray()
    found = False
    for field, kind, _, original in fields(data):
        if field == number and kind == wire:
            result.extend(encoded)
            found = True
        else:
            result.extend(original)
    if not found:
        result.extend(encoded)
    return bytes(result)


def categories(blob: bytes):
    build = payload(blob, 1)
    details = payload(build, 10)
    return [value for number, wire, value, _ in fields(details) if number == 1 and wire == 2]


def dimension(category: bytes, number: int) -> float | None:
    value = payload(category, number, 5)
    return FLOAT.unpack(value)[0] if value else None


def update_categories(blob: bytes, sizes: list[tuple[float | None, float | None]]) -> bytes:
    build = payload(blob, 1)
    details = payload(build, 10)
    result = bytearray()
    index = 0
    for number, wire, category, original in fields(details):
        if number == 1 and wire == 2:
            if index >= len(sizes):
                raise ValueError("Category count mismatch")
            edited = category
            for field, value in zip((4, 5), sizes[index]):
                if value is not None and value != dimension(category, field):
                    edited = replace_field(edited, field, 5, FLOAT.pack(value))
            result.extend(original if edited == category else b"\x0a" + varint(len(edited)) + edited)
            index += 1
        else:
            result.extend(original)
    if index != len(sizes):
        raise ValueError("Category count mismatch")
    if result == details:
        return blob
    return replace_field(blob, 1, 2, replace_field(build, 10, 2, bytes(result)))


def convert(action: str, source: Path, destination: Path) -> None:
    def run(command):
        process = subprocess.run(command, capture_output=True, text=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if process.returncode:
            raise RuntimeError(process.stdout + process.stderr)

    if not DLL.exists() or DLL.stat().st_mtime < max(
        PROJECT.stat().st_mtime, (PROJECT.parent / "Program.cs").stat().st_mtime
    ):
        run(["dotnet", "build", str(PROJECT), "-c", "Release", "--nologo", "-v", "quiet"])
    run(["dotnet", str(DLL), action, str(source), str(destination)])


class Editor(QWidget):
    completed = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.text = ""
        self.builds = []
        self.selected = None
        self.sizes = []
        self.current_blob = b""
        self.source = None
        self.busy = False
        self.buttons = []
        self.setWindowTitle("Deadlock Build Size Editor")
        self.resize(900, 560)
        self.setMinimumSize(720, 400)
        self.setStyleSheet("""
            QWidget { background: #10151e; color: #e4eaf3; font: 14px 'Segoe UI'; }
            QPushButton { background: #263244; border: 1px solid #344154;
                border-radius: 9px; padding: 11px 22px; }
            QPushButton:hover { background: #35465e; border-color: #526784; }
            QPushButton:pressed { background: #1b2535; }
            QPushButton:focus { border-color: #74a8ff; }
            QPushButton:disabled { color: #66758b; border-color: #263244; }
            QPushButton#export { background: #3578ed; border-color: #3578ed; }
            QPushButton#export:hover { background: #498bff; }
            QPushButton#export:pressed { background: #245dcc; }
            QListWidget, QTableWidget { background: #18212f; border: 1px solid #263244;
                border-radius: 10px; padding: 6px; outline: none; }
            QListWidget::item { padding: 11px 12px; border-radius: 6px; }
            QListWidget::item:hover { background: #243248; }
            QListWidget::item:selected { background: #2c5796; color: white; }
            QTableWidget { alternate-background-color: #1d2735;
                selection-background-color: #2c5796; }
            QTableWidget::item { padding: 8px; border: none; }
            QLineEdit { background: #243248; border: 1px solid #74a8ff;
                border-radius: 4px; padding: 4px; selection-background-color: #3578ed; }
            QSplitter::handle { background: #10151e; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 0; }
            QScrollBar::handle:vertical { background: #344154; border-radius: 5px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #526784; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        toolbar = QHBoxLayout()
        for text, command in (("Open", self.open), ("Export", self.export)):
            button = QPushButton(text)
            if text == "Export":
                button.setObjectName("export")
            button.clicked.connect(command)
            toolbar.addWidget(button)
            self.buttons.append(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.listbox = QListWidget()
        self.listbox.setMinimumWidth(200)
        self.listbox.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.listbox.currentRowChanged.connect(self.select)
        self.table = QTableWidget(0, 3)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        pane = QSplitter(Qt.Orientation.Horizontal)
        pane.addWidget(self.listbox)
        pane.addWidget(self.table)
        pane.setChildrenCollapsible(False)
        pane.setHandleWidth(16)
        pane.setSizes([300, 560])
        layout.addWidget(pane, 1)
        actions = QHBoxLayout()
        for text, command in (("+10 width this build", self.plus_all), ("Reset this build", self.reset)):
            button = QPushButton(text)
            button.clicked.connect(command)
            actions.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(actions)
        self.completed.connect(self.finish)

    def work(self, task, done, title):
        if self.busy:
            return
        self.busy = True
        self.setEnabled(False)
        self.setCursor(Qt.CursorShape.BusyCursor)
        self.done, self.error_title = done, title
        def run():
            try:
                value = task()
            except Exception as error:
                self.completed.emit(None, error)
            else:
                self.completed.emit(value, None)
        Thread(target=run, daemon=True).start()

    @Slot(object, object)
    def finish(self, value, error):
        done, self.done = self.done, None
        self.busy = False
        self.setEnabled(True)
        self.unsetCursor()
        try:
            if error:
                raise error
            done(value)
        except Exception as error:
            QMessageBox.critical(self, self.error_title, str(error))

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            event.accept()

    def open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open", "", "KV3 files (*.kv3);;All files (*)")
        if not path:
            return
        if self.builds and QMessageBox.question(self, "Open", "Discard the current editing session?") != QMessageBox.StandardButton.Yes:
            return
        def load():
            with tempfile.TemporaryDirectory(prefix="kv3-editor-") as temp:
                output = Path(temp) / "decoded.txt"
                convert("decode", Path(path), output)
                text = output.read_text(encoding="utf-8")
            builds = []
            section = "Build"
            for match in ENTRIES.finditer(text):
                if match.group(1):
                    section = match.group(1)
                    continue
                blob = bytes.fromhex(match.group(2))
                build = payload(blob, 1)
                name = bytes(payload(build, 5)).decode("utf-8")
                if not name:
                    continue
                builds.append({"name": name, "section": section, "span": match.span(2), "blob": None})
            if not builds:
                raise ValueError("No supported named build entries found.")
            return text, builds
        def loaded(value):
            self.text, self.builds = value
            self.source = Path(path)
            self.selected = None
            self.listbox.blockSignals(True)
            self.listbox.clear()
            self.listbox.addItems([build["name"] for build in self.builds])
            for index, build in enumerate(self.builds):
                self.listbox.item(index).setToolTip(build["section"])
            self.listbox.blockSignals(False)
            self.listbox.setCurrentRow(0)
        self.work(load, loaded, "Open failed")

    def original(self, build):
        start, end = build["span"]
        return bytes.fromhex(self.text[start:end])

    def commit(self):
        if self.selected is None:
            return
        sizes = []
        build = self.builds[self.selected]
        self.table.clearFocus()
        for row, previous in enumerate(self.sizes):
            values = []
            for column, fallback in enumerate(previous, 1):
                text = self.table.item(row, column).text().strip()
                value = float(text) if text else fallback
                if value is not None:
                    if not math.isfinite(value) or not 0 < value <= 100000:
                        raise ValueError("Widths and heights must be positive numbers up to 100000.")
                    value = FLOAT.unpack(FLOAT.pack(value))[0]
                values.append(value)
            sizes.append(tuple(values))
        if sizes != self.sizes:
            self.current_blob = update_categories(self.current_blob, sizes)
            build["blob"] = None if self.current_blob == self.original(build) else self.current_blob
            self.sizes = sizes

    def select(self, index):
        if self.busy or index < 0 or index == self.selected:
            return
        try:
            self.commit()
            self.show_build(index)
        except Exception as error:
            QMessageBox.critical(self, "Invalid dimensions", str(error))
            self.listbox.blockSignals(True)
            self.listbox.setCurrentRow(self.selected if self.selected is not None else -1)
            self.listbox.blockSignals(False)

    def show_build(self, index):
        build = self.builds[index]
        blob = build["blob"] if build["blob"] is not None else self.original(build)
        boxes = categories(blob)
        self.selected, self.current_blob = index, blob
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(boxes))
        self.sizes = []
        for row, box in enumerate(boxes):
            name = bytes(payload(box, 2)).decode("utf-8") or f"Box {row + 1}"
            name = name.replace("#Citadel_HeroBuilds_", "")
            width_value, height_value = dimension(box, 4), dimension(box, 5)
            for column, value in enumerate((name, width_value, height_value)):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(value if column == 0 else format(value, ".9g") if value is not None else "")
                item.setToolTip(("Box", "Width", "Height")[column])
                if column == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sizes.append((width_value, height_value))
        self.table.setUpdatesEnabled(True)

    def plus_all(self):
        try:
            self.table.clearFocus()
            values = [float(self.table.item(row, 1).text()) + 10 for row in range(self.table.rowCount())]
            if any(not math.isfinite(value) or not 0 < value <= 100000 for value in values):
                raise ValueError
            for row, value in enumerate(values):
                self.table.item(row, 1).setText(format(value, ".9g"))
        except ValueError:
            QMessageBox.critical(self, "Invalid width", "Enter widths between 0 and 99990 before adding 10.")

    def reset(self):
        if self.selected is not None:
            self.builds[self.selected]["blob"] = None
            self.table.clearFocus()
            self.show_build(self.selected)

    def export(self):
        if not self.builds:
            return
        try:
            self.commit()
            path, _ = QFileDialog.getSaveFileName(self, "Export", str(self.source.with_suffix(".edited.kv3")), "KV3 files (*.kv3)")
            if not path:
                return
            destination = Path(path if path.lower().endswith(".kv3") else path + ".kv3")
            if destination.exists():
                raise ValueError("Choose a new filename. Existing files are never overwritten.")
            edits = [build for build in self.builds if build["blob"] is not None]
            def save():
                with tempfile.TemporaryDirectory(prefix="kv3-editor-") as temp:
                    source = Path(temp) / "edited.txt"
                    with source.open("w", encoding="utf-8", newline="\n") as output:
                        pos = 0
                        for build in edits:
                            start, end = build["span"]
                            output.write(self.text[pos:start])
                            output.write("\n" + build["blob"].hex(" ") + "\n")
                            pos = end
                        output.write(self.text[pos:])
                    convert("encode", source, destination)
            self.work(save, lambda _: None, "Export failed")
        except Exception as error:
            QMessageBox.critical(self, "Export failed", str(error))


if __name__ == "__main__":
    app = QApplication([])
    app.setStyle("Fusion")
    editor = Editor()
    editor.show()
    app.exec()
