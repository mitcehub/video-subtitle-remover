from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFontMetrics, QPolygonF

import sys
import atexit
import logging

if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

    # Windows hook to capture raw WM_MOUSEWHEEL delta when Alt is held
    # (Qt zeroes angleDelta for Alt+wheel on Windows)
    ctypes.windll.user32.CallNextHookEx.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
    ]

    _GetMsgProc = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
    )

    _last_wheel_delta = 0
    _hook_handle = None
    _hook_callback = None


    def _get_msg_proc(nCode, wParam, lParam):
        global _last_wheel_delta
        if nCode >= 0:
            msg = ctypes.cast(lParam, ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == 0x020A:  # WM_MOUSEWHEEL
                w_param = ctypes.c_ulong(msg.wParam).value
                z_delta = (w_param >> 16) & 0xFFFF
                if z_delta >= 0x8000:
                    z_delta -= 0x10000
                alt_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000)
                if alt_down:
                    _last_wheel_delta = z_delta
        return ctypes.windll.user32.CallNextHookEx(0, nCode, wParam, lParam)


    def _install_hook():
        global _hook_handle, _hook_callback
        if _hook_handle is not None:
            return
        _hook_callback = _GetMsgProc(_get_msg_proc)
        tid = ctypes.windll.kernel32.GetCurrentThreadId()
        _hook_handle = ctypes.windll.user32.SetWindowsHookExW(
            3,  # WH_GETMESSAGE
            _hook_callback,
            None,
            tid,
        )
        if not _hook_handle:
            logger = logging.getLogger(__name__)
            logger.info("[HOOK_ERROR] SetWindowsHookExW failed")


    def _uninstall_hook():
        global _hook_handle, _hook_callback
        if _hook_handle:
            ctypes.windll.user32.UnhookWindowsHookEx(_hook_handle)
            _hook_handle = None
            _hook_callback = None

    atexit.register(_uninstall_hook)
else:
    _last_wheel_delta = 0

    def _install_hook():
        pass

    def _uninstall_hook():
        pass

TRACK_COLORS = ["#2ECC71", "#FF6B35", "#35A7FF", "#4BC0C0", "#FF6384", "#9966FF", "#FF9F40", "#E74C3C", "#1ABC9C", "#9B59B6"]
_SNAP_THRESHOLD = 6  # pixel radius for snapping


def _nice_interval(raw):
    candidates = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
    return min(candidates, key=lambda x: abs(x - raw))


class TimelineWidget(QWidget):
    frame_selected = pyqtSignal(int)
    preview_seek = pyqtSignal(int)
    track_range_changed = pyqtSignal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setMinimumHeight(32)
        self.setMouseTracking(True)

        self.tracks = []
        self.current_frame = 1
        self.frame_count = 1
        self.fps = 30
        self.zoom_level = 1.0
        self.scroll_offset = 0

        self.ruler_h = 28
        self.track_h = 18
        self.track_spacing = 2
        self.handle_w = 6
        self.h_margin = 8

        self._drag_state = None
        self._drag_track_id = None
        self._drag_anchor = None
        self._drag_start_val = None
        self._last_emitted_frame = None
        self._hovered_track = None
        self._hovered_handle = None
        self._ruler_dragging = False
        self._playhead_x = None
        self._preview_x = None
        self._preview_frame = None

        _install_hook()

    def _update_fixed_height(self):
        self.setMinimumHeight(max(80, self._total_content_h()))

    def set_data(self, tracks, frame_count, fps):
        old_zoom = self.zoom_level
        old_scroll = self.scroll_offset
        self.tracks = list(tracks)
        self.frame_count = max(frame_count, 1)
        self.fps = max(fps, 1)
        self.current_frame = min(self.current_frame, self.frame_count)
        if self.tracks:
            self.zoom_level = old_zoom
            self.scroll_offset = old_scroll
        else:
            self.zoom_level = 1.0
            self.scroll_offset = 0
        self._drag_state = None
        self._ruler_dragging = False
        self._playhead_x = None
        self._preview_x = None
        self._preview_frame = None
        self._update_fixed_height()
        self.update()

    def _get_snap_xs(self, exclude_track_id=None):
        """返回可用于吸附的 x 坐标列表（像素），排除指定的轨道 ID。"""
        xs = set()
        xs.add(self._frame_to_x(self.current_frame))
        for t in self.tracks:
            if t["id"] == exclude_track_id:
                continue
            xs.add(self._frame_to_x(t["start"]))
            xs.add(self._frame_to_x(t["end"]))
        return xs

    def _snap_target(self, mx, exclude_track_id=None):
        """对鼠标 x 坐标进行吸附：若某个吸附点距离 < _SNAP_THRESHOLD 像素，返回该帧。"""
        best_dx = _SNAP_THRESHOLD
        best_frame = None
        for sx in self._get_snap_xs(exclude_track_id):
            dx = abs(mx - sx)
            if dx < best_dx:
                best_dx = dx
                best_frame = self._x_to_frame(sx)
        if best_frame is not None:
            best_frame = max(1, min(best_frame, self.frame_count))
        return best_frame

    def _frame_to_time(self, frame):
        f = max(0, min(frame - 1, (self.frame_count or 1) - 1))
        sec = int(f / self.fps)
        fr = int(f % self.fps)
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}:{fr:02d}"
        return f"{m:02d}:{s:02d}:{fr:02d}"

    def set_current_frame(self, frame):
        self.current_frame = max(1, min(frame, self.frame_count))
        if not self._ruler_dragging:
            self._playhead_x = None
        self._ensure_visible(self.current_frame)
        self.update()

    def _px_per_frame(self):
        return self._content_width() / self.frame_count * self.zoom_level

    def _content_width(self):
        return max(1, self.width() - self.h_margin * 2)

    def _frame_to_x(self, frame):
        return (frame - 1) * self._px_per_frame() - self.scroll_offset + self.h_margin

    def _x_to_frame(self, x):
        return int((x - self.h_margin + self.scroll_offset) / self._px_per_frame()) + 1

    def _track_y(self, idx):
        return self.ruler_h + idx * (self.track_h + self.track_spacing)

    def _total_content_h(self):
        return self.ruler_h + len(self.tracks) * (self.track_h + self.track_spacing)

    def _ensure_visible(self, frame):
        cx = self._frame_to_x(frame)
        w = self._content_width()
        if cx < self.h_margin:
            self.scroll_offset = max(0, (frame - 1) * self._px_per_frame())
        elif cx > self.h_margin + w:
            self.scroll_offset = (frame - 1) * self._px_per_frame() - w

    # ==================== paint ====================

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#1e1e1e"))
        self._draw_end_boundary(p)
        fm = QFontMetrics(p.font())
        self._draw_ruler(p, fm)
        self._draw_tracks(p, fm)
        self._draw_playhead(p)
        self._draw_preview(p, fm)

    def _draw_end_boundary(self, p):
        x = self._frame_to_x(self.frame_count)
        if x > self.width() - self.h_margin:
            return
        if x > self.h_margin:
            p.fillRect(int(x), 0, self.width() - int(x), self._total_content_h(), QColor("#1a1a2e"))
            p.setPen(QPen(QColor("#555"), 1))
            p.drawLine(int(x), 0, int(x), self._total_content_h())

    def _draw_ruler(self, p, fm):
        y = self.ruler_h
        pf = self._px_per_frame()
        p.setPen(QPen(QColor("#444"), 1))
        p.drawLine(0, y, self.width(), y)

        # 基础间隔（秒），基于当前缩放
        base_sec = _nice_interval(60.0 / (pf * self.fps) if pf * self.fps > 0 else 1)
        base_frames = max(1, int(round(base_sec * self.fps)))

        # 主刻度：如果标签会重叠则翻倍，但不超过总帧数（否则只显示一头一尾）
        label_w = fm.horizontalAdvance("00:00:00") + 6
        gap = base_frames * pf
        if gap < label_w and base_frames * 2 <= self.frame_count:
            for m in (2, 5, 10, 20, 50, 100):
                if base_frames * m * pf >= label_w:
                    base_frames = base_frames * m
                    break
        major_frames = min(base_frames, self.frame_count)

        # 次刻度间距（主间隔约 5 等分）
        sub_frames = max(1, major_frames // 5) if major_frames > 5 else 1

        frame = 1
        while frame <= self.frame_count:
            x = self._frame_to_x(frame)
            if self.h_margin <= x <= self.width() - self.h_margin:
                is_major = (frame - 1) % major_frames == 0
                if is_major:
                    p.setPen(QPen(QColor("#888"), 1))
                    p.drawLine(int(x), y - 9, int(x), y)
                    label = self._frame_to_time(frame)
                    p.setPen(QColor("#aaa"))
                    tw = fm.horizontalAdvance(label)
                    p.drawText(int(x) - tw // 2, 13, label)
                elif (frame - 1) % sub_frames == 0:
                    p.setPen(QPen(QColor("#555"), 1))
                    p.drawLine(int(x), y - 4, int(x), y)
            frame += sub_frames

    def _draw_tracks(self, p, fm):
        for i, track in enumerate(self.tracks):
            y0 = self._track_y(i)
            color = QColor(track.get("color", TRACK_COLORS[i % len(TRACK_COLORS)]))
            bar_left = self._frame_to_x(track["start"])
            bar_right = self._frame_to_x(track["end"])
            if bar_right < self.h_margin or bar_left > self.width() - self.h_margin:
                continue
            bar_y = y0 + (self.track_h - 10) // 2
            bar_h = 10
            bar_rect = QRectF(bar_left, bar_y, bar_right - bar_left, bar_h)
            p.fillRect(bar_rect, color.lighter(130))
            fill_color = QColor(color)
            fill_color.setAlpha(180)
            p.fillRect(bar_rect, fill_color)
            p.setPen(QPen(QColor("#fff"), 2))
            p.drawRect(QRectF(bar_left, bar_y, self.handle_w, bar_h))
            p.drawRect(QRectF(bar_right - self.handle_w, bar_y, self.handle_w, bar_h))
            txt = f"{self._frame_to_time(track['start'])}-{self._frame_to_time(track['end'])}"
            tw = fm.horizontalAdvance(txt)
            tx = bar_left + (bar_right - bar_left - tw) // 2
            if tx < self.h_margin:
                tx = bar_right + 4
            p.setPen(QColor("#fff"))
            p.drawText(QRectF(int(tx), int(bar_y), int(tw), int(bar_h)), Qt.AlignmentFlag.AlignCenter, txt)

    def _draw_playhead(self, p):
        if self._playhead_x is not None:
            x = self._playhead_x
        else:
            x = self._frame_to_x(self.current_frame)
        if x < self.h_margin or x > self.width() - self.h_margin:
            return
        p.setPen(QPen(QColor("#ff4444"), 1))
        p.drawLine(int(x), 0, int(x), self._total_content_h())
        pts = QPolygonF()
        pts << QPointF(x, 0) << QPointF(x - 5, 8) << QPointF(x + 5, 8)
        p.setBrush(QBrush(QColor("#ff4444")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(pts)

    def _draw_preview(self, p, fm):
        if self._preview_x is None or self._preview_frame is None:
            return
        x = self._preview_x
        if x < self.h_margin or x > self.width() - self.h_margin:
            return
        p.setPen(QPen(QColor("#ffaa00"), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(x), 0, int(x), self._total_content_h())
        label = f"\u5e27 {self._preview_frame}"
        tw = fm.horizontalAdvance(label) + 10
        th = fm.height() + 4
        lx = max(self.h_margin, min(int(x) - tw // 2, self.width() - tw - self.h_margin))
        ly = 4
        p.fillRect(lx, ly, tw, th, QColor("#333"))
        p.setPen(QPen(QColor("#ffaa00"), 1))
        p.drawRect(lx, ly, tw, th)
        p.setPen(QColor("#ffaa00"))
        p.drawText(lx + 5, ly + th - 4, label)

    # ==================== hit testing ====================

    def _track_hit_test(self, track_idx, mx, my):
        track = self.tracks[track_idx]
        y0 = self._track_y(track_idx)
        if not (y0 <= my <= y0 + self.track_h):
            return None
        bar_left = self._frame_to_x(track["start"])
        bar_right = self._frame_to_x(track["end"])
        if abs(mx - bar_left) <= self.handle_w + 2:
            return "handle_start"
        if abs(mx - bar_right) <= self.handle_w + 2:
            return "handle_end"
        if bar_left <= mx <= bar_right:
            return "body"
        return None

    def _hit_test(self, mx, my):
        if my < self.ruler_h:
            return (None, "ruler")
        for i in range(len(self.tracks)):
            y0 = self._track_y(i)
            if y0 <= my <= y0 + self.track_h:
                t = self._track_hit_test(i, mx, my)
                if t:
                    return (i, t)
        return (None, None)

    # ==================== mouse events ====================

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        mx, my = event.position().x(), event.position().y()
        tid, ttype = self._hit_test(mx, my)
        self._drag_state = None
        if tid is None and ttype == "ruler":
            self._ruler_dragging = True
            self._drag_state = "seek_ruler"
            self._last_emitted_frame = None
            self._playhead_x = max(self.h_margin, min(mx, self.width() - self.h_margin))
            frame = max(1, min(self._x_to_frame(mx), self.frame_count))
            self._last_emitted_frame = frame
            self.preview_seek.emit(frame)
            self.update()
            return
        if tid is not None:
            track = self.tracks[tid]
            self._drag_state = ttype
            self._drag_track_id = track["id"]
            self._drag_anchor = mx
            if ttype == "body":
                self._drag_start_val = track["start"]
            else:
                self._drag_start_val = track["start"] if "start" in ttype else track["end"]

    def mouseMoveEvent(self, event):
        mx, my = event.position().x(), event.position().y()
        if self._drag_state == "seek_ruler":
            self._playhead_x = max(self.h_margin, min(mx, self.width() - self.h_margin))
            frame = max(1, min(self._x_to_frame(self._playhead_x), self.frame_count))
            if frame != self._last_emitted_frame:
                self._last_emitted_frame = frame
                self.preview_seek.emit(frame)
            self.update()
            return
        if self._drag_state and self._drag_track_id is not None:
            track = next((t for t in self.tracks if t["id"] == self._drag_track_id), None)
            if not track:
                return
            start, end = track["start"], track["end"]
            target_raw = max(1, min(self._x_to_frame(mx), self.frame_count))
            snapped = self._snap_target(mx, exclude_track_id=track["id"])
            target = snapped if snapped is not None else target_raw
            self._preview_x = max(self.h_margin, min(mx, self.width() - self.h_margin))
            if self._drag_state == "handle_start":
                ns = max(1, min(target, end - 1))
                if ns != start:
                    track["start"] = ns
                    self.track_range_changed.emit(track["id"], ns, end)
                    self._preview_frame = ns
                    self.preview_seek.emit(ns)
                    self.update()
            elif self._drag_state == "handle_end":
                ne = min(self.frame_count, max(target, start + 1))
                if ne != end:
                    track["end"] = ne
                    self.track_range_changed.emit(track["id"], start, ne)
                    self._preview_frame = ne
                    self.preview_seek.emit(ne)
                    self.update()
            elif self._drag_state == "body":
                dx = mx - self._drag_anchor
                delta = int(dx / self._px_per_frame())
                span = end - start
                ns = max(1, min(self._drag_start_val + delta, self.frame_count - span))
                snapped_ns = self._snap_target(self._frame_to_x(ns), exclude_track_id=track["id"])
                if snapped_ns is not None:
                    ns = max(1, min(snapped_ns, self.frame_count - span))
                ne = ns + span
                if ns != start:
                    track["start"] = ns
                    track["end"] = ne
                    self.track_range_changed.emit(track["id"], ns, ne)
                    self._preview_frame = ns
                    self.preview_seek.emit(ns)
                    self.update()
            return
        tid, ttype = self._hit_test(mx, my)
        hov_track = tid if tid is not None else -1
        hov_handle = ttype if tid is not None else None
        if hov_track != self._hovered_track or hov_handle != self._hovered_handle:
            self._hovered_track = hov_track
            self._hovered_handle = hov_handle
            if hov_handle in ("handle_start", "handle_end", "body"):
                self.setCursor(Qt.CursorShape.SizeHorCursor if hov_handle != "body" else Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_state == "seek_ruler" and self._last_emitted_frame is not None:
                self.frame_selected.emit(self._last_emitted_frame)
            self._ruler_dragging = False
            self._drag_state = None
            self._playhead_x = None
            self._preview_x = None
            self._preview_frame = None
            self.update()

    def resizeEvent(self, event):
        old_w = event.oldSize().width()
        new_w = self.width()
        if old_w > 0 and new_w != old_w:
            ratio = new_w / old_w
            self.scroll_offset = max(0, self.scroll_offset * ratio)
        self._ensure_visible(self.current_frame)
        cw = self._content_width()
        max_so = max(0, cw * self.zoom_level - cw)
        self.scroll_offset = max(0, min(self.scroll_offset, max_so))
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        has_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if has_alt:
            if delta == 0:
                delta = _last_wheel_delta
            if delta != 0:
                cx = max(0, event.position().x())
                old_virtual = cx - self.h_margin + self.scroll_offset
                old_px = self._px_per_frame()
                if delta > 0:
                    self.zoom_level = min(self.zoom_level * 1.3, 500)
                else:
                    self.zoom_level = max(self.zoom_level / 1.3, 0.5)
                new_px = self._px_per_frame()
                new_virtual = old_virtual * new_px / old_px
                self.scroll_offset = max(0, new_virtual + self.h_margin - cx)
                self._ensure_visible(self.current_frame)
        else:
            if delta != 0:
                step = int(-delta / 8 * self._px_per_frame() * 3)
                self.scroll_offset = max(0, self.scroll_offset + step)
        event.accept()
        self.update()

    def sizeHint(self):
        h = self._total_content_h()
        return self.minimumSizeHint().expandedTo(self.size())
