"""Settings control panel — live sliders + colour picker for every knob.

Opened from the tray icon. Each change updates the Settings object, calls the
apply callback (which updates the running radar live) and saves to disk.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .settings import Settings


class SettingsWindow(QtWidgets.QWidget):
    def __init__(self, settings: Settings, on_change):
        super().__init__(None)
        self.s = settings
        self.on_change = on_change
        self.setWindowTitle("SoundRadar — Settings")
        self.setMinimumWidth(380)
        lay = QtWidgets.QVBoxLayout(self)

        lay.addWidget(self._heading("Behaviour"))
        self._slider(lay, "Sensitivity", 0, 100, settings.sensitivity,
                     lambda v: self._set("sensitivity", v),
                     "react to more / quieter sounds")
        self._slider(lay, "Adapt (events vs constant)", 0, 100, settings.adapt,
                     lambda v: self._set("adapt", v),
                     "higher = steady background fades, only changes show")
        self._slider(lay, "Fade speed (ms)", 100, 900, settings.decay_ms,
                     lambda v: self._set("decay_ms", v),
                     "how slowly a block fades out")

        lay.addWidget(self._heading("Look"))
        self._slider(lay, "Size / growth", 0, 100, settings.size,
                     lambda v: self._set("size", v),
                     "how big blocks grow with loudness")
        self._slider(lay, "Brightness", 50, 400, settings.gain * 100,
                     lambda v: self._set("gain", v / 100.0))
        self._slider(lay, "Number of blocks", 6, 30, settings.segments,
                     lambda v: self._set("segments", int(v)))
        self._slider(lay, "Bar thickness (px)", 8, 70, settings.thickness,
                     lambda v: self._set("thickness", int(v)))
        self._slider(lay, "Opacity (%)", 25, 100, settings.opacity * 100,
                     lambda v: self._set("opacity", v / 100.0))

        # monitor selector (only meaningful with >1 screen, but always shown)
        screens = QtWidgets.QApplication.instance().screens()
        mrow = QtWidgets.QHBoxLayout()
        mrow.addWidget(QtWidgets.QLabel("Monitor"))
        combo = QtWidgets.QComboBox()
        for i, sc in enumerate(screens):
            g = sc.geometry()
            primary = " (primary)" if sc == QtWidgets.QApplication.primaryScreen() else ""
            combo.addItem(f"{i + 1}: {g.width()}x{g.height()}{primary}", i)
        combo.setCurrentIndex(max(0, min(settings.monitor, len(screens) - 1)))
        combo.currentIndexChanged.connect(
            lambda idx: self._set("monitor", combo.itemData(idx)))
        mrow.addWidget(combo)
        mrow.addStretch(1)
        lay.addLayout(mrow)

        # colour picker row
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Colour"))
        self._col_btn = QtWidgets.QPushButton()
        self._col_btn.setFixedWidth(120)
        self._paint_btn()
        self._col_btn.clicked.connect(self._pick_colour)
        row.addWidget(self._col_btn)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(self._heading("Audio"))
        self._slider(lay, "Headphone volume (%)", 0, 100, settings.out_gain * 100,
                     lambda v: self._set("out_gain", v / 100.0),
                     "volume of the mono mix in your headphones")

        note = QtWidgets.QLabel("Changes apply live and are saved automatically.")
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)

    # -- helpers ---------------------------------------------------------
    def _heading(self, text):
        lbl = QtWidgets.QLabel(text)
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        lbl.setContentsMargins(0, 8, 0, 0)
        return lbl

    def _slider(self, lay, label, lo, hi, value, on_val, tip=""):
        row = QtWidgets.QVBoxLayout()
        head = QtWidgets.QHBoxLayout()
        name = QtWidgets.QLabel(label)
        val_lbl = QtWidgets.QLabel(str(int(value)))
        val_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        head.addWidget(name)
        head.addWidget(val_lbl)
        row.addLayout(head)
        sld = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        sld.setMinimum(int(lo)); sld.setMaximum(int(hi))
        sld.setValue(int(value))
        if tip:
            sld.setToolTip(tip); name.setToolTip(tip)

        def changed(v):
            val_lbl.setText(str(v))
            on_val(v)
        sld.valueChanged.connect(changed)
        row.addWidget(sld)
        lay.addLayout(row)

    def _set(self, field, value):
        setattr(self.s, field, value)
        self.on_change()

    def _paint_btn(self):
        c = self.s.color
        self._col_btn.setStyleSheet(
            f"background-color: {c}; color: white; border: 1px solid #888;")
        self._col_btn.setText(c)

    def _pick_colour(self):
        cur = QtGui.QColor(self.s.color)
        col = QtWidgets.QColorDialog.getColor(cur, self, "Pick radar colour")
        if col.isValid():
            self.s.color = col.name().upper()
            self._paint_btn()
            self.on_change()
