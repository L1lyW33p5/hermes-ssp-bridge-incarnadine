#!/usr/bin/env python3
"""Modern SoundCloud-inspired capsule input for Hermes bridge.

Standalone UI process. It prints submitted text to stdout and exits.
A blank stdout means closed/cancelled.
"""
import math
import sys
import ctypes
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QMetaObject, QPropertyAnimation, QRectF, QTimer, Qt, Slot
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

ROOT = Path(__file__).resolve().parent
CLOSE_SIGNAL = ROOT / "modern_input_close.signal"

# Slightly smaller capsule requested for the modern input.
W = 405
H = 40
R = 20
MARGIN_X = 30
MARGIN_Y = 26
CANVAS_W = W + MARGIN_X * 2
CANVAS_H = H + MARGIN_Y * 2

ORANGE = QColor(102, 0, 0)
MAGMA = QColor(255, 0, 128)
CYAN = QColor(6, 182, 212)
PURPLE = QColor(128, 70, 255)
LAVENDER = QColor(188, 154, 255)
WHITE = QColor(245, 245, 247)

STAR_SEEDS = [
    (0.18, 0.34, 0.48), (0.29, 0.63, 0.19), (0.42, 0.42, 0.72),
    (0.57, 0.68, 0.35), (0.73, 0.36, 0.86), (0.86, 0.58, 0.52),
]

PARTICLE_SEEDS = [
    (0.09, 0.44, 0.18, 0.28, 1.1), (0.16, 0.62, 0.71, 0.18, 0.8),
    (0.24, 0.30, 0.37, 0.22, 1.0), (0.35, 0.66, 0.83, 0.16, 0.7),
    (0.48, 0.38, 0.05, 0.24, 1.2), (0.58, 0.70, 0.58, 0.14, 0.9),
    (0.68, 0.34, 0.29, 0.20, 1.1), (0.78, 0.58, 0.93, 0.17, 0.8),
    (0.88, 0.42, 0.46, 0.23, 1.0), (0.94, 0.64, 0.12, 0.15, 0.7),
]

EDGE_PARTICLE_SEEDS = [
    (0, 0.10, 0.16, 0.23, 0.85), (0, 0.27, 0.61, 0.18, 0.70),
    (0, 0.78, 0.82, 0.20, 0.75), (1, 0.66, 0.74, 0.21, 0.90),
    (2, 0.22, 0.57, 0.20, 0.70), (3, 0.76, 0.34, 0.24, 0.88),
]


def _safe_print(text: str) -> None:
    try:
        print(text, flush=True)
    except OSError:
        pass


class ModernCapsule(QWidget):
    def __init__(self, self_test: bool = False):
        super().__init__()
        self.self_test = self_test
        self._submitted = False
        self._closing = False
        self._pulse = 0.0
        self._mouse_hook = None
        self._mouse_proc = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(CANVAS_W, CANVAS_H)

        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - CANVAS_W) // 2
        y = screen.y() + int(screen.height() * 0.425) - MARGIN_Y
        self.move(x, y)

        self.entry = QLineEdit(self)
        self.entry.setObjectName("soundcloudInput")
        self.entry.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.Medium))
        self.entry.setFrame(False)
        self.entry.setGeometry(MARGIN_X + 26, MARGIN_Y + 3, W - 52, H - 8)
        self.entry.setStyleSheet(
            "QLineEdit#soundcloudInput {"
            "background: transparent;"
            "color: rgba(245,245,247,238);"
            "border: none;"
            "padding: 0px;"
            "selection-background-color: rgba(102,0,0,90);"
            "selection-color: white;"
            "}"
            "QLineEdit#soundcloudInput::placeholder {"
            "color: rgba(245,245,247,84);"
            "}"
        )
        self.entry.returnPressed.connect(self.submit)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._animate)
        self._tick.start(33)

        self._signal_timer = QTimer(self)
        self._signal_timer.timeout.connect(self._check_close_signal)
        self._signal_timer.start(80)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(170)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _capsule(self) -> QRectF:
        return QRectF(MARGIN_X, MARGIN_Y, W, H)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            CLOSE_SIGNAL.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        self.setWindowOpacity(0.0)
        self._fade.start()
        self._install_mouse_hook()
        self._schedule_focus()

    def _schedule_focus(self):
        self._force_focus()
        for delay in (60, 160, 320):
            QTimer.singleShot(delay, self._force_focus)

    def _force_focus(self):
        if self._submitted or self._closing:
            return

        self.raise_()
        self.activateWindow()
        self.entry.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

        if sys.platform != "win32":
            return

        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
            user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
            user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
            user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user32.SetActiveWindow.argtypes = [ctypes.c_void_p]
            user32.SetFocus.argtypes = [ctypes.c_void_p]
            kernel32.GetCurrentThreadId.restype = ctypes.c_ulong

            SW_SHOW = 5
            HWND_TOPMOST = ctypes.c_void_p(-1 & ((1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1))
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040

            user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOW)
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )

            fg_hwnd = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            our_tid = kernel32.GetCurrentThreadId()
            attached = False
            if fg_tid and fg_tid != our_tid:
                attached = bool(user32.AttachThreadInput(our_tid, fg_tid, True))

            user32.BringWindowToTop(ctypes.c_void_p(hwnd))
            user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            user32.SetActiveWindow(ctypes.c_void_p(hwnd))
            user32.SetFocus(ctypes.c_void_p(hwnd))

            if attached:
                user32.AttachThreadInput(our_tid, fg_tid, False)
        except Exception:
            pass

        self.entry.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _animate(self):
        self._pulse += 0.020
        self.update()

    def _check_close_signal(self):
        if CLOSE_SIGNAL.exists():
            try:
                CLOSE_SIGNAL.unlink()
            except Exception:
                pass
            self.close_without_submit()

    def _install_mouse_hook(self):
        if self._mouse_hook is not None or sys.platform != "win32":
            return

        user32 = ctypes.windll.user32
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        user32.CallNextHookEx.restype = ctypes.c_long
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = ctypes.c_bool
        WH_MOUSE_LL = 14
        WM_LBUTTONDOWN = 0x0201
        WM_RBUTTONDOWN = 0x0204
        WM_MBUTTONDOWN = 0x0207
        HC_ACTION = 0

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT),
                ("mouseData", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)

        def _proc(n_code, w_param, l_param):
            if n_code == HC_ACTION and w_param in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                event = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if self._point_outside_capsule(event.pt.x, event.pt.y):
                    QMetaObject.invokeMethod(
                        self,
                        "close_without_submit",
                        Qt.ConnectionType.QueuedConnection,
                    )
            return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)

        self._mouse_proc = HOOKPROC(_proc)
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._mouse_proc,
            None,
            0,
        )
        if not self._mouse_hook:
            self._mouse_proc = None

    def _uninstall_mouse_hook(self):
        if self._mouse_hook is None or sys.platform != "win32":
            return
        try:
            ctypes.windll.user32.UnhookWindowsHookEx(self._mouse_hook)
        except Exception:
            pass
        self._mouse_hook = None
        self._mouse_proc = None

    def _point_outside_capsule(self, x: int, y: int) -> bool:
        top_left = self.mapToGlobal(self.rect().topLeft())
        left = top_left.x() + MARGIN_X - 8
        top = top_left.y() + MARGIN_Y - 8
        right = left + W + 16
        bottom = top + H + 16
        return not (left <= x <= right and top <= y <= bottom)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_without_submit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        QTimer.singleShot(120, self._close_if_inactive)
        super().focusOutEvent(event)

    def _close_if_inactive(self):
        if self._submitted or self._closing:
            return
        if not self.isActiveWindow():
            self.close_without_submit()

    def submit(self):
        if self._submitted:
            return
        text = self.entry.text().strip()
        self._submitted = True
        if text:
            _safe_print(text)
        self._quit()

    @Slot()
    def close_without_submit(self):
        if self._submitted or self._closing:
            return
        self._closing = True
        self._quit()

    def _quit(self):
        self._uninstall_mouse_hook()
        self._tick.stop()
        self._signal_timer.stop()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        capsule = self._capsule()
        pulse = (math.sin(self._pulse) + 1.0) / 2.0
        path = QPainterPath()
        path.addRoundedRect(capsule, R, R)

        self._paint_atmosphere(p, capsule, pulse)
        self._paint_shadow(p, capsule)
        self._paint_edge_particles(p, capsule)
        self._paint_glass_body(p, capsule, path, pulse)
        self._paint_inside_light(p, capsule, path, pulse)
        self._paint_particles(p, capsule, pulse)
        self._paint_stars(p, capsule, pulse)

        p.end()

    def _paint_close_halo(self, p: QPainter, capsule: QRectF, pulse: float):
        # Keep the desktop outside the capsule clean. Immersive light lives inside the glass.
        return

    def _paint_atmosphere(self, p: QPainter, capsule: QRectF, pulse: float):
        # Keep atmospheric motion inside the glass so the desktop background stays clean.
        return

    def _paint_shadow(self, p: QPainter, capsule: QRectF):
        # No external drop shadow: the window should sit cleanly on dark pages.
        return

    def _paint_edge_particles(self, p: QPainter, capsule: QRectF):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        t = self._pulse
        max_dist = 13.0

        for segment, u, phase, drift, size in EDGE_PARTICLE_SEEDS:
            speed = 0.62 + drift * 1.35
            phase_pos = (t * speed + phase) % 1.0
            ease_out = 1.0 - (1.0 - phase_pos) * (1.0 - phase_pos)
            twinkle = (math.sin(t * (0.85 + drift) + phase * math.pi * 2) + 1.0) / 2.0
            dist = 2.4 + 10.6 * ease_out
            fade = max(0.0, 1.0 - phase_pos)
            alpha = int((24 + 58 * twinkle) * fade * fade)
            if alpha <= 2:
                continue

            tangent = 7.0 * math.sin(t * (0.55 + drift) + phase * math.pi * 2)
            if segment == 0:
                x = capsule.left() + R + u * (capsule.width() - 2 * R) + tangent
                y = capsule.top() - dist
            elif segment == 1:
                x = capsule.left() + R + u * (capsule.width() - 2 * R) + tangent
                y = capsule.bottom() + dist
            elif segment == 2:
                angle = math.pi / 2 + u * math.pi
                cx = capsule.left() + R
                cy = capsule.center().y()
                x = cx + math.cos(angle) * (R + dist)
                y = cy + math.sin(angle) * (R + dist) + tangent * 0.35
            else:
                angle = -math.pi / 2 + u * math.pi
                cx = capsule.right() - R
                cy = capsule.center().y()
                x = cx + math.cos(angle) * (R + dist)
                y = cy + math.sin(angle) * (R + dist) + tangent * 0.35

            color = QColor(WHITE)
            color.setAlpha(alpha)
            radius = size + 0.55 * twinkle

            glow = QRadialGradient(x, y, radius * 5.0)
            glow.setColorAt(0.0, color)
            soft = QColor(color)
            soft.setAlpha(int(alpha * 0.16))
            glow.setColorAt(0.46, soft)
            glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(glow)
            p.drawEllipse(QRectF(x - radius * 5, y - radius * 5, radius * 10, radius * 10))

            core = QColor(WHITE)
            core.setAlpha(min(120, alpha + 22))
            p.setBrush(core)
            p.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
        p.restore()

    def _paint_glass_body(self, p: QPainter, capsule: QRectF, path: QPainterPath, pulse: float):
        base = QLinearGradient(capsule.topLeft(), capsule.bottomRight())
        base.setColorAt(0.00, QColor(28, 28, 31, 232))
        base.setColorAt(0.08, QColor(15, 15, 18, 238))
        base.setColorAt(0.16, QColor(10, 10, 13, 236))
        base.setColorAt(0.58, QColor(7, 7, 10, 232))
        base.setColorAt(1.00, QColor(32, 12, 8, 224))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawPath(path)

        left_mist = QLinearGradient(capsule.left(), capsule.center().y(), capsule.left() + capsule.width() * 0.22, capsule.center().y())
        left_mist.setColorAt(0.00, QColor(245, 245, 247, 42))
        left_mist.setColorAt(0.42, QColor(245, 245, 247, 14))
        left_mist.setColorAt(1.00, QColor(245, 245, 247, 0))
        p.setBrush(left_mist)
        p.drawPath(path)

        # Thin premium rim: red on left, magma on lower edge, white on top edge.
        rim = QLinearGradient(capsule.left(), capsule.bottom(), capsule.right(), capsule.top())
        c0 = QColor(ORANGE); c0.setAlpha(228)
        c1 = QColor(MAGMA); c1.setAlpha(120)
        c2 = QColor(255, 255, 255, 48)
        rim.setColorAt(0.00, c0)
        rim.setColorAt(0.32, c1)
        rim.setColorAt(0.68, c2)
        rim.setColorAt(1.00, QColor(255, 255, 255, 18))
        pen = QPen(rim, 1.15 + 0.25 * pulse)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        inner = capsule.adjusted(2.0, 2.0, -2.0, -2.0)
        hi = QLinearGradient(inner.left(), inner.top(), inner.right(), inner.bottom())
        hi.setColorAt(0.0, QColor(245, 245, 247, 18))
        hi.setColorAt(0.42, QColor(255, 255, 255, 5))
        hi.setColorAt(1.0, QColor(102, 0, 0, 16))
        p.setPen(QPen(hi, 0.9))
        p.drawRoundedRect(inner, R - 2, R - 2)

    def _paint_inside_light(self, p: QPainter, capsule: QRectF, path: QPainterPath, pulse: float):
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)

        t = self._pulse

        # Soft aura is inside the glass and drifts subtly with the particle field.
        for cx, cy, radius, base, alpha in (
            (
                capsule.left() + 108 + 9 * math.sin(t * 0.42),
                capsule.center().y() + 3 + 4 * math.cos(t * 0.35),
                132,
                ORANGE,
                34,
            ),
            (
                capsule.right() - 118 + 12 * math.sin(t * 0.32 + 1.7),
                capsule.center().y() + 5 + 5 * math.cos(t * 0.38 + 0.8),
                130,
                LAVENDER,
                38,
            ),
            (
                capsule.center().x() + 20 + 16 * math.sin(t * 0.28 + 2.4),
                capsule.top() - 12 + 5 * math.cos(t * 0.31 + 1.1),
                92,
                CYAN,
                30,
            ),
        ):
            color = QColor(base)
            color.setAlpha(int(alpha * (0.85 + 0.15 * pulse)))
            grad = QRadialGradient(cx, cy, radius)
            grad.setColorAt(0.0, color)
            fade = QColor(color); fade.setAlpha(int(color.alpha() * 0.18))
            grad.setColorAt(0.38, fade)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(grad)
            p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        scan = QLinearGradient(capsule.left() + 70, capsule.bottom(), capsule.right() - 40, capsule.bottom())
        scan.setColorAt(0.0, QColor(102, 0, 0, 0))
        scan.setColorAt(0.26, QColor(102, 0, 0, 92 + int(28 * pulse)))
        scan.setColorAt(0.58, QColor(255, 0, 128, 66))
        scan.setColorAt(1.0, QColor(102, 0, 0, 0))
        p.setPen(QPen(scan, 1.0))
        y = capsule.bottom() - 4
        p.drawLine(int(capsule.left() + 70), int(y), int(capsule.right() - 42), int(y))
        p.restore()

    def _paint_particles(self, p: QPainter, capsule: QRectF, pulse: float):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        t = self._pulse
        for i, (sx, sy, phase, drift, size) in enumerate(PARTICLE_SEEDS):
            wave = math.sin(t * (0.55 + drift) + phase * math.pi * 2)
            bob = math.cos(t * (0.45 + drift) + phase * math.pi * 2)
            x = capsule.left() + sx * capsule.width() + wave * 12
            y = capsule.top() + sy * capsule.height() + bob * 7
            base = (ORANGE, CYAN, MAGMA)[i % 3]
            if base == CYAN:
                color = QColor(PURPLE)
            elif sx > 0.5:
                color = QColor(WHITE)
            else:
                color = QColor(base)
            color.setAlpha(int(22 + 52 * ((wave + 1.0) / 2.0)))
            r = size + 0.45 * ((bob + 1.0) / 2.0)

            glow = QRadialGradient(x, y, r * 5.5)
            glow.setColorAt(0.0, color)
            soft = QColor(color)
            soft.setAlpha(int(color.alpha() * 0.18))
            glow.setColorAt(0.45, soft)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(glow)
            p.drawEllipse(QRectF(x - r * 5.5, y - r * 5.5, r * 11, r * 11))

            core = QColor(color)
            core.setAlpha(min(170, color.alpha() + 55))
            p.setBrush(core)
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
        p.restore()

    def _paint_stars(self, p: QPainter, capsule: QRectF, pulse: float):
        p.setPen(Qt.PenStyle.NoPen)
        for i, (sx, sy, phase) in enumerate(STAR_SEEDS):
            twinkle = (math.sin(self._pulse + phase * math.pi * 2) + 1.0) / 2.0
            r = 0.8 + twinkle * 0.45
            x = capsule.left() + sx * capsule.width()
            y = capsule.top() + sy * capsule.height()
            base = (ORANGE, MAGMA, CYAN)[i % 3]
            if base == CYAN:
                color = QColor(PURPLE)
            elif sx > 0.5:
                color = QColor(WHITE)
            else:
                color = QColor(base)
            color.setAlpha(int(18 + 58 * twinkle))
            p.setBrush(color)
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))


def main(argv):
    app = QApplication(argv)
    app.setApplicationName("Hermes Modern Input")

    self_test = "--self-test" in argv
    win = ModernCapsule(self_test=self_test)
    if self_test:
        pix = win.grab()
        ok = (pix.width(), pix.height()) == (CANVAS_W, CANVAS_H)
        print(f"SELFTEST {'OK' if ok else 'FAIL'} {pix.width()}x{pix.height()} W={W} H={H}")
        return 0 if ok else 2

    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
