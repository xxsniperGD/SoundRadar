"""Settings control panel — modern dark, tabbed, compact, with presets.

Opened from the tray icon. Each change updates the Settings object, calls the
apply callback (updates the running radar live) and saves to disk.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import settings as settings_mod
from .settings import PRESET_FIELDS, Settings
from .audio import list_loopback_devices, rms_to_dbfs

ACCENT = "#4ECDC4"        # refined muted teal
ACCENT_SOFT = "rgba(78, 205, 196, 0.12)"

STYLE = f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; }}
QWidget {{ background: #101116; color: #e9ebf1; }}
QTabWidget::pane {{ border: 1px solid #20232c; border-radius: 12px; top: -1px;
                    background: #14161c; }}
QTabBar::tab {{ background: transparent; color: #757b87; padding: 9px 20px;
                border: none; margin-right: 2px; letter-spacing: 0.3px; }}
QTabBar::tab:selected {{ color: #ffffff; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: #c2c6d0; }}
QGroupBox {{ border: 1px solid #20232c; border-radius: 12px; margin-top: 16px;
             padding: 18px 16px 8px 16px; background: #181a21; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px;
                    color: #8b93a0; font-weight: 600; text-transform: uppercase;
                    letter-spacing: 0.6px; font-size: 11px; }}
QLabel#hint {{ color: #757b87; }}
QSlider::groove:horizontal {{ height: 4px; background: #262a34; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #eef1f6; width: 14px; height: 14px;
                              margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QPushButton {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 8px;
               padding: 8px 12px; color: #e9ebf1; }}
QPushButton:hover {{ background: #242833; border-color: #363b48; }}
QPushButton#accent {{ background: transparent; border: 1px solid {ACCENT};
                      color: {ACCENT}; font-weight: 600; padding: 10px;
                      letter-spacing: 0.4px; }}
QPushButton#accent:hover {{ background: {ACCENT_SOFT}; }}
QComboBox {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 8px;
             padding: 6px 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1d2029; border: 1px solid #2a2e39;
                               selection-background-color: {ACCENT};
                               selection-color: #101116; outline: none; }}
QFrame#card {{ background: #181a21; border: 1px solid #20232c; border-radius: 12px; }}
QLabel#infotitle {{ color: #ffffff; font-weight: 600; }}
QToolTip {{ background: #1d2029; color: #e9ebf1; border: 1px solid #2a2e39;
            padding: 4px 6px; }}
QInputDialog, QMessageBox {{ background: #14161c; }}
QProgressBar {{ background: #1d2029; border: 1px solid #2a2e39; border-radius: 5px;
                height: 12px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""


class SettingsWindow(QtWidgets.QWidget):
    def __init__(self, settings: Settings, on_change, on_test=None,
                 get_levels=None):
        super().__init__(None)
        self.s = settings
        self.on_change = on_change
        self.on_test = on_test
        self._get_levels = get_levels      # () -> Levels, for the Check tab
        self._diag_n = 0                   # channel count the bars are built for
        self._diag_bars = {}               # index -> (QProgressBar, value label)
        self._rows = []          # (field, slider, disp_fn, value_label)
        self._presets = settings_mod.load_presets()
        self.setWindowTitle("SoundRadar")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(440)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        title = QtWidgets.QLabel("SoundRadar")
        tf = title.font(); tf.setPointSize(15); tf.setBold(True); title.setFont(tf)
        root.addWidget(title)

        root.addLayout(self._preset_bar())

        if on_test is not None:
            tb = QtWidgets.QPushButton("◎   Test radar")
            tb.setObjectName("accent")
            tb.setToolTip("Sweep a sound around the ring for ~6s — see and tune "
                          "the radar without a game.")
            tb.clicked.connect(lambda: self.on_test())
            root.addWidget(tb)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._radar_tab(), "Radar")
        tabs.addTab(self._setup_tab(), "Setup")
        tabs.addTab(self._diag_tab(), "Check")
        root.addWidget(tabs)

        foot = QtWidgets.QLabel("Changes apply live and save automatically.")
        foot.setObjectName("hint")
        root.addWidget(foot)

        # poll the live capture for the Check tab (cheap; only paints when shown)
        self._diag_timer = QtCore.QTimer(self)
        self._diag_timer.timeout.connect(self._update_diag)
        self._diag_timer.start(120)

    # -- presets ---------------------------------------------------------
    def _preset_bar(self):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Preset"))
        self._preset_combo = QtWidgets.QComboBox()
        self._reload_preset_combo()
        self._preset_combo.activated.connect(self._on_preset_pick)
        row.addWidget(self._preset_combo, 1)
        save = QtWidgets.QPushButton("Save…")
        save.clicked.connect(self._save_preset)
        row.addWidget(save)
        dele = QtWidgets.QPushButton("Delete")
        dele.clicked.connect(self._delete_preset)
        row.addWidget(dele)
        return row

    def _reload_preset_combo(self):
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("— choose preset —")
        for name in sorted(self._presets):
            self._preset_combo.addItem(name)
        self._preset_combo.setCurrentIndex(0)
        self._preset_combo.blockSignals(False)

    def _on_preset_pick(self, idx):
        if idx <= 0:
            return
        name = self._preset_combo.itemText(idx)
        data = self._presets.get(name, {})
        for k, v in data.items():
            setattr(self.s, k, v)
        self.on_change()
        self._refresh()

    def _save_preset(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Save preset",
                                                  "Preset name:")
        name = name.strip()
        if not ok or not name:
            return
        self._presets[name] = {f: getattr(self.s, f) for f in PRESET_FIELDS}
        settings_mod.save_presets(self._presets)
        self._reload_preset_combo()
        self._preset_combo.setCurrentText(name)

    def _delete_preset(self):
        name = self._preset_combo.currentText()
        if name in self._presets:
            del self._presets[name]
            settings_mod.save_presets(self._presets)
            self._reload_preset_combo()

    # -- tabs ------------------------------------------------------------
    def _radar_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        beh = self._card("Behaviour"); g = beh.layout()
        self._row(g, 0, "Sensitivity", "sensitivity", 0, 100)
        self._row(g, 1, "Adapt", "adapt", 0, 100)
        self._row(g, 2, "Fade", "decay_ms", 100, 900)
        v.addWidget(beh)

        app = self._card("Appearance"); g = app.layout()
        self._row(g, 0, "Size", "size", 0, 100)
        self._row(g, 1, "Brightness", "gain", 50, 400, mul=100)
        self._row(g, 2, "Blocks", "segments", 6, 30, integer=True)
        self._row(g, 3, "Thickness", "thickness", 8, 70, integer=True)
        self._row(g, 4, "Opacity", "opacity", 25, 100, mul=100)
        g.addWidget(QtWidgets.QLabel("Colour"), 5, 0)
        self._sw = QtWidgets.QPushButton(); self._sw.setFixedSize(54, 22)
        self._sw.clicked.connect(self._pick_colour); self._paint_swatch()
        g.addWidget(self._sw, 5, 1, QtCore.Qt.AlignmentFlag.AlignLeft)
        v.addWidget(app)
        v.addStretch(1)
        return w

    def _setup_tab(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        cap = self._card("Capture"); g = cap.layout()
        g.addWidget(QtWidgets.QLabel("Mode"), 0, 0)
        self._mode = QtWidgets.QComboBox()
        self._mode.addItem("Stereo — no setup, left/right", "stereo")
        self._mode.addItem("Surround — 7.1 device, front/back", "surround")
        self._mode.setCurrentIndex(1 if self.s.mode == "surround" else 0)
        self._mode.currentIndexChanged.connect(self._on_mode)
        g.addWidget(self._mode, 0, 1, 1, 2)
        g.addWidget(QtWidgets.QLabel("Device"), 1, 0)
        self._dev = QtWidgets.QComboBox()
        names = [m.name for m in list_loopback_devices()]
        self._dev.addItems(names or ["(no loopback devices found)"])
        if self.s.capture_device in names:
            self._dev.setCurrentText(self.s.capture_device)
        self._dev.currentTextChanged.connect(
            lambda t: self._set("capture_device", t))
        self._dev.setEnabled(self.s.mode == "surround")
        g.addWidget(self._dev, 1, 1, 1, 2)
        note = QtWidgets.QLabel("Restart SoundRadar (tray ▸ Quit, reopen) to "
                                "apply capture changes.")
        note.setObjectName("hint"); note.setWordWrap(True)
        g.addWidget(note, 2, 0, 1, 3)
        v.addWidget(cap)

        disp = self._card("Display & audio"); g = disp.layout()
        g.addWidget(QtWidgets.QLabel("Monitor"), 0, 0)
        screens = QtWidgets.QApplication.instance().screens()
        combo = QtWidgets.QComboBox()
        for i, sc in enumerate(screens):
            geo = sc.geometry()
            star = " •" if sc == QtWidgets.QApplication.primaryScreen() else ""
            combo.addItem(f"{i + 1}: {geo.width()}×{geo.height()}{star}", i)
        combo.setCurrentIndex(max(0, min(self.s.monitor, len(screens) - 1)))
        combo.currentIndexChanged.connect(
            lambda idx: self._set("monitor", combo.itemData(idx)))
        g.addWidget(combo, 0, 1, 1, 2)
        self._row(g, 1, "Volume", "out_gain", 0, 100, mul=100)
        v.addWidget(disp)

        card = QtWidgets.QFrame(); card.setObjectName("card")
        cl = QtWidgets.QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12); cl.setSpacing(8)
        t = QtWidgets.QLabel("Keep an app off the radar")
        t.setObjectName("infotitle"); cl.addWidget(t)
        body = QtWidgets.QLabel(
            "The radar shows whatever is sent to your capture device. To stop an "
            "app (e.g. voice chat) showing up, set its output to your headphones "
            "in Windows Volume mixer — you'll still hear it, it just won't appear.")
        body.setObjectName("hint"); body.setWordWrap(True); cl.addWidget(body)
        mix = QtWidgets.QPushButton("Open Windows Volume mixer")
        mix.clicked.connect(self._open_mixer); cl.addWidget(mix)
        v.addWidget(card)
        v.addStretch(1)
        return w

    def _diag_tab(self):
        """Live capture check — per-channel levels + a surround/mono verdict.
        This is the diag.py test built into the app."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 8, 12, 12); v.setSpacing(12)

        card = QtWidgets.QGroupBox("Live capture")
        cv = QtWidgets.QVBoxLayout(card)
        cv.setContentsMargins(16, 18, 16, 12); cv.setSpacing(10)

        self._diag_verdict = QtWidgets.QLabel("Waiting for audio…")
        vf = self._diag_verdict.font(); vf.setBold(True); vf.setPointSize(13)
        self._diag_verdict.setFont(vf)
        cv.addWidget(self._diag_verdict)

        sub = QtWidgets.QLabel(
            "Play a sound with a clear direction. For real surround the bars "
            "should differ. If every bar moves together, it's being collapsed "
            "to mono.")
        sub.setObjectName("hint"); sub.setWordWrap(True)
        cv.addWidget(sub)

        self._diag_host = QtWidgets.QWidget()
        self._diag_host_v = QtWidgets.QVBoxLayout(self._diag_host)
        self._diag_host_v.setContentsMargins(0, 4, 0, 0)
        self._diag_host_v.setSpacing(6)
        cv.addWidget(self._diag_host)
        v.addWidget(card)

        tip = QtWidgets.QLabel(
            "All bars equal? Turn Windows “Mono audio” OFF and set the game to "
            "7.1. Bars at the floor (no movement) = nothing is reaching the "
            "capture device.")
        tip.setObjectName("hint"); tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        return w

    def _rebuild_diag_rows(self, n, labels):
        while self._diag_host_v.count():
            item = self._diag_host_v.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._diag_bars = {}
        for i in range(n):
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
            lab = QtWidgets.QLabel(labels[i] if i < len(labels) else f"ch{i}")
            lab.setFixedWidth(32)
            bar = QtWidgets.QProgressBar()
            bar.setMinimum(0); bar.setMaximum(100); bar.setTextVisible(False)
            bar.setFixedHeight(12)
            val = QtWidgets.QLabel("—"); val.setObjectName("hint")
            val.setFixedWidth(46)
            val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                             | QtCore.Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(lab); h.addWidget(bar, 1); h.addWidget(val)
            self._diag_host_v.addWidget(row)
            self._diag_bars[i] = (bar, val)

    def _update_diag(self):
        if self._get_levels is None or not self.isVisible():
            return
        lv = self._get_levels()
        n = int(getattr(lv, "channels", 0))
        if n <= 0 or lv.rms.size == 0:
            self._diag_verdict.setText("● No audio yet")
            self._diag_verdict.setStyleSheet("color:#757b87;")
            return
        if n != self._diag_n:
            self._rebuild_diag_rows(n, lv.labels)
            self._diag_n = n
        db = rms_to_dbfs(lv.rms)
        allvals, any_loud = [], False
        for i in range(n):
            d = float(db[i]) if i < db.size else -120.0
            allvals.append(d)
            bar, val = self._diag_bars[i]
            bar.setValue(int(max(0.0, min(100.0, (d + 60.0) / 60.0 * 100.0))))
            val.setText("—" if d <= -119.0 else f"{d:.0f} dB")
            any_loud = any_loud or d > -55.0
        # A mono collapse makes EVERY channel identical -> spread ~0. Real
        # direction leaves some channels loud and others quiet -> big spread.
        spread = (max(allvals) - min(allvals)) if allvals else 0.0
        if not any_loud:
            self._diag_verdict.setText("● Silence — nothing on this device")
            self._diag_verdict.setStyleSheet("color:#757b87;")
        elif spread > 6.0:
            self._diag_verdict.setText("● Direction detected — radar will work")
            self._diag_verdict.setStyleSheet(f"color:{ACCENT};")
        else:
            self._diag_verdict.setText("● Mono / uniform — collapsed, no direction")
            self._diag_verdict.setStyleSheet("color:#e0a030;")

    # -- helpers ---------------------------------------------------------
    def _card(self, title):
        box = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(box)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 34)
        grid.setHorizontalSpacing(12); grid.setVerticalSpacing(10)
        return box

    def _row(self, grid, r, label, field, lo, hi, mul=1, integer=False):
        def disp(val):
            return int(round(val * mul))

        def parse(x):
            v = x / mul
            return int(v) if integer else v

        name = QtWidgets.QLabel(label)
        sld = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        sld.setMinimum(int(lo)); sld.setMaximum(int(hi))
        sld.setValue(disp(getattr(self.s, field)))
        val = QtWidgets.QLabel(str(sld.value())); val.setObjectName("hint")
        val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                         | QtCore.Qt.AlignmentFlag.AlignVCenter)

        def changed(x):
            val.setText(str(x))
            self._set(field, parse(x))
        sld.valueChanged.connect(changed)
        self._rows.append((field, sld, disp, val))
        grid.addWidget(name, r, 0); grid.addWidget(sld, r, 1)
        grid.addWidget(val, r, 2)

    def _refresh(self):
        """Push current settings back into the widgets (after loading a preset)."""
        for field, sld, disp, val in self._rows:
            sld.blockSignals(True)
            sld.setValue(disp(getattr(self.s, field)))
            sld.blockSignals(False)
            val.setText(str(sld.value()))
        self._paint_swatch()

    def _on_mode(self, idx):
        mode = self._mode.itemData(idx)
        self._dev.setEnabled(mode == "surround")
        self._set("mode", mode)

    def _set(self, field, value):
        setattr(self.s, field, value)
        self.on_change()

    def _paint_swatch(self):
        self._sw.setStyleSheet(
            f"background:{self.s.color}; border:1px solid #555; border-radius:5px;")

    def _pick_colour(self):
        col = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.s.color), self, "Radar colour")
        if col.isValid():
            self.s.color = col.name().upper()
            self._paint_swatch()
            self.on_change()

    def _open_mixer(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl("ms-settings:apps-volume"))
