import logging
logger = logging.getLogger(__name__)
import cv2
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QMenu
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QEvent
from PyQt6.QtGui import QAction, QShortcut, QCursor
from PyQt6 import QtCore, QtWidgets, QtGui
from qfluentwidgets import HollowHandleStyle
from infra.config import config, tr
from ui.component.timeline_widget import TRACK_COLORS
from ui.coordinate_mapper import CoordinateMapper


class VideoDisplayComponent(QWidget):
    """视频显示组件，包含视频预览和选择框功能"""
    
    # 定义信号
    tracks_changed = pyqtSignal(list)  # 轨道变化信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        
        # 初始化变量
        self.is_drawing = False
        self.current_draw_rect = (0, 0, 0, 0)  # 正在绘制的选区 (ymin, ymax, xmin, xmax)
        self._track_id_counter = 1
        self.tracks = []  # 轨道列表：[{id, ymin,ymax,xmin,xmax, start,end, enabled,collapsed, color}, ...]
        self.active_track_index = -1
        self.drag_start_pos = None
        self.resize_edge = None
        self.edge_size = 10
        self.enable_mouse_events = True
        self._last_frame = None
        self.fill_mode = False
        
        # 创建右键菜单
        self.__init_context_menu()
        
        # 获取屏幕大小
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            self.screen_width = screen.size().width()
            self.screen_height = screen.size().height()
        else:
            self.screen_width, self.screen_height = 1920, 1080
        
        # 设置视频预览区域大小（根据屏幕宽度动态调整）
        self.video_preview_width = 960
        self.video_preview_height = self.video_preview_width * 9 // 16
        if self.screen_width // 2 < 960:
            self.video_preview_width = 640
            self.video_preview_height = self.video_preview_width * 9 // 16
            
        # 视频相关参数
        self.frame_width = None
        self.frame_height = None
        self.scaled_width = None
        self.scaled_height = None
        self.border_left = 0
        self.border_top = 0
        self.fps = 30
        self._coord_mapper = CoordinateMapper()

        self.__init_widgets()
        self.__init_shortcuts()
        
    def __init_widgets(self):
        """初始化组件"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 视频显示标签
        self.video_display = QtWidgets.QLabel()
        self.video_display.setStyleSheet("""
            background-color: black;
            border: 0px solid transparent;
        """)
        self.video_display.setMinimumWidth(200)
        self.video_display.setMinimumHeight(1)
        self.video_display.setMouseTracking(True)
        self.video_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_display.mousePressEvent = self.selection_mouse_press
        self.video_display.mouseMoveEvent = self.selection_mouse_move
        self.video_display.mouseReleaseEvent = self.selection_mouse_release
        self.video_display.setObjectName('videoDisplay')

        # 状态叠加标签
        self.status_label = QtWidgets.QLabel(self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet('color: #ccc; font-size: 16px; background: #111; border-radius: 10px;')
        self.status_label.hide()

        # 视频画面（填满剩余空间）
        main_layout.addWidget(self.video_display, 1)

        # 视频滑块（隐藏，仅用于值跟踪）
        self.video_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.video_slider.setMinimum(1)
        self.video_slider.setFixedHeight(22)
        self.video_slider.setMaximum(100)
        self.video_slider.setValue(1)
        self.video_slider.setStyle(HollowHandleStyle({
            "handle.color": QtGui.QColor(255, 255, 255),
            "handle.ring-width": 4,
            "handle.hollow-radius": 6,
            "handle.margin": 1
        }))
        self.video_slider.hide()
    
    def __init_shortcuts(self):
        """初始化快捷键"""
        self.shortcut_delete_selection = QShortcut(QtGui.QKeySequence.StandardKey.Delete, self)
        self.shortcut_delete_selection.activated.connect(self.__handle_delete_selection)
        self.shortcut_delete_selection.setContext(Qt.ShortcutContext.ApplicationShortcut)

        # 添加左右键控制slider的快捷键
        self.shortcut_right = QShortcut(QtGui.QKeySequence(Qt.Key.Key_Right), self)
        self.shortcut_right.activated.connect(lambda: self.__adjust_slider_value(self.fps))
        self.shortcut_right.setContext(Qt.ShortcutContext.ApplicationShortcut)

        self.shortcut_left = QShortcut(QtGui.QKeySequence(Qt.Key.Key_Left), self)
        self.shortcut_left.activated.connect(lambda: self.__adjust_slider_value(-self.fps))
        self.shortcut_left.setContext(Qt.ShortcutContext.ApplicationShortcut)

        # 添加Ctrl+左右键控制slider的快捷键
        self.shortcut_ctrl_right = QShortcut(QtGui.QKeySequence("Ctrl+Right"), self)
        self.shortcut_ctrl_right.activated.connect(lambda: self.__adjust_slider_value(self.fps*5))
        self.shortcut_ctrl_right.setContext(Qt.ShortcutContext.ApplicationShortcut)

        self.shortcut_ctrl_left = QShortcut(QtGui.QKeySequence("Ctrl+Left"), self)
        self.shortcut_ctrl_left.activated.connect(lambda: self.__adjust_slider_value(-self.fps*5))
        self.shortcut_ctrl_left.setContext(Qt.ShortcutContext.ApplicationShortcut)

        # 添加Shift+左右键控制slider的快捷键
        self.shortcut_shift_right = QShortcut(QtGui.QKeySequence("Shift+Right"), self)
        self.shortcut_shift_right.activated.connect(lambda: self.__adjust_slider_value(1))
        self.shortcut_shift_right.setContext(Qt.ShortcutContext.ApplicationShortcut)

        self.shortcut_shift_left = QShortcut(QtGui.QKeySequence("Shift+Left"), self)
        self.shortcut_shift_left.activated.connect(lambda: self.__adjust_slider_value(-1))
        self.shortcut_shift_left.setContext(Qt.ShortcutContext.ApplicationShortcut)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.status_label.setGeometry(self.video_display.geometry())
        if self._last_frame is not None:
            self.update_video_display(self._last_frame, draw_selection=True)

    def update_video_display(self, frame, draw_selection=True):
        """更新视频显示（单次缩放，无双重 resize）"""
        if frame is None:
            return

        self._last_frame = frame

        label_w = self.video_display.width()
        label_h = self.video_display.height()
        if label_w <= 0 or label_h <= 0:
            label_w = self.video_preview_width
            label_h = self.video_preview_height

        h, w = frame.shape[:2]
        if self.fill_mode:
            target_w = label_w
            target_h = label_h
        else:
            frame_ratio = w / h
            target_w = label_w
            target_h = int(target_w / frame_ratio)
            if target_h > label_h:
                target_h = label_h
                target_w = int(target_h * frame_ratio)

        frame = cv2.resize(frame, (target_w, target_h))
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_frame.shape
        bytes_per_line = ch * w
        image = QtGui.QImage(rgb_frame.data, w, h, bytes_per_line, QtGui.QImage.Format.Format_RGB888)
        pix = QtGui.QPixmap.fromImage(image)

        # 创建 QLabel 大小的黑底画布，帧居中放置
        x_off = (label_w - target_w) // 2
        y_off = (label_h - target_h) // 2
        canvas = QtGui.QPixmap(label_w, label_h)
        canvas.fill(QtGui.QColor(0, 0, 0))

        painter = QtGui.QPainter(canvas)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

        painter.drawPixmap(x_off, y_off, pix)
        painter.end()

        # 保存画布供选择框绘制
        self.current_pixmap = canvas.copy()
        # 记录帧在画布中的偏移和尺寸（供坐标转换）
        self.border_left = x_off
        self.border_top = y_off
        self.scaled_width = target_w
        self.scaled_height = target_h
        self._update_coord_mapper()
        self.video_display.setPixmap(canvas)

        self.update_preview_with_rect(draw_selection=draw_selection)

    # ---- 坐标转换辅助 ----
    def _get_frame_params(self):
        sw = getattr(self, 'scaled_width', 0) or self.video_display.width()
        sh = getattr(self, 'scaled_height', 0) or self.video_display.height()
        bl = getattr(self, 'border_left', 0) or 0
        bt = getattr(self, 'border_top', 0) or 0
        return sw, sh, bl, bt

    def _pos_to_norm(self, pos):
        return self._coord_mapper.pos_to_norm(pos)

    def _norm_to_rect(self, xmin, ymin, xmax, ymax):
        return self._coord_mapper.norm_to_rect(xmin, ymin, xmax, ymax)
    
    def _draw_handles(self, painter, rect, color):
        hs = 4
        painter.setBrush(QtGui.QBrush(color))
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
        pts = [
            (rect.left(), rect.top()),
            (rect.center().x(), rect.top()),
            (rect.right(), rect.top()),
            (rect.right(), rect.center().y()),
            (rect.right(), rect.bottom()),
            (rect.center().x(), rect.bottom()),
            (rect.left(), rect.bottom()),
            (rect.left(), rect.center().y()),
        ]
        for px, py in pts:
            painter.drawRect(px - hs, py - hs, hs * 2, hs * 2)

    def update_preview_with_rect(self, rect=None, draw_selection=True):
        """更新带有选择框的预览"""
        if not hasattr(self, 'current_pixmap') or self.current_pixmap is None:
            return
            
        if rect is not None and self.active_track_index >= 0:
            t = self.tracks[self.active_track_index]
            t["ymin"], t["ymax"], t["xmin"], t["xmax"] = rect

        pixmap_copy = self.current_pixmap.copy()
        painter = QtGui.QPainter(pixmap_copy)

        if draw_selection:
            current_frame = self.video_slider.value()
            for i, t in enumerate(self.tracks):
                if not t.get("enabled", True):
                    continue
                if not (t["start"] <= current_frame <= t["end"]):
                    continue
                color = QtGui.QColor(t.get("color", "#FF6B35"))
                if i == self.active_track_index:
                    pen = QtGui.QPen(color)
                else:
                    pen = QtGui.QPen(QtGui.QColor(255, 255, 0))
                pen.setWidth(2)
                painter.setPen(pen)
                pixel_rect = self._norm_to_rect(t["xmin"], t["ymin"], t["xmax"], t["ymax"])
                painter.drawRect(pixel_rect)
                if i == self.active_track_index:
                    self._draw_handles(painter, pixel_rect, color)

            if self.is_drawing and self.current_draw_rect and any(self.current_draw_rect):
                draw_color = self._get_next_track_color()
                pen = QtGui.QPen(QtGui.QColor(draw_color))
                pen.setWidth(2)
                painter.setPen(pen)
                ymin, ymax, xmin, xmax = self.current_draw_rect
                pixel_rect = self._norm_to_rect(xmin, ymin, xmax, ymax)
                painter.drawRect(pixel_rect)

        painter.end()
        self.video_display.setPixmap(pixmap_copy)
    
    def selection_mouse_press(self, event):
        """鼠标按下事件处理"""
        if not self.enable_mouse_events:
            return
        
        if event.button() == Qt.MouseButton.RightButton:
            global_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            self.context_menu.exec(global_pos)
            return

        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        y_ratio, x_ratio = self._pos_to_norm(pos)

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.is_drawing = True
            self.current_draw_rect = (y_ratio, y_ratio, x_ratio, x_ratio)
            self.drag_start_pos = (y_ratio, x_ratio)
            self.resize_edge = None
            self.active_track_index = -1
            return
        
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self.clear_selections()
            return
        
        clicked_idx = -1
        current_frame = self.video_slider.value()
        for i, t in enumerate(self.tracks):
            if not (t["start"] <= current_frame <= t["end"]):
                continue
            px_rect = self._norm_to_rect(t["xmin"], t["ymin"], t["xmax"], t["ymax"])
            if self.is_on_rect_edge(pos, px_rect):
                clicked_idx = i
                self.active_track_index = i
                self.resize_edge = self.get_resize_edge(pos, px_rect)
                self.drag_start_pos = (y_ratio, x_ratio)
                return
            elif px_rect.contains(pos):
                clicked_idx = i
                self.active_track_index = i
                self.resize_edge = "move"
                self.drag_start_pos = (y_ratio, x_ratio)
                self.update_preview_with_rect()
                return
        
        if clicked_idx == -1:
            self.is_drawing = True
            self.current_draw_rect = (y_ratio, y_ratio, x_ratio, x_ratio)
            self.drag_start_pos = (y_ratio, x_ratio)
            self.resize_edge = None
            self.active_track_index = -1

    def is_on_rect_edge(self, pos, pixel_rect):
        """检查点是否在矩形边缘
        注意：这里的pixel_rect是已经转换为像素坐标的QRect对象
        """
        # 右下角
        if abs(pos.x() - pixel_rect.right()) <= self.edge_size and abs(pos.y() - pixel_rect.bottom()) <= self.edge_size:
            return True
        # 右上角
        elif abs(pos.x() - pixel_rect.right()) <= self.edge_size and abs(pos.y() - pixel_rect.top()) <= self.edge_size:
            return True
        # 左下角
        elif abs(pos.x() - pixel_rect.left()) <= self.edge_size and abs(pos.y() - pixel_rect.bottom()) <= self.edge_size:
            return True
        # 左上角
        elif abs(pos.x() - pixel_rect.left()) <= self.edge_size and abs(pos.y() - pixel_rect.top()) <= self.edge_size:
            return True
        # 左边缘
        elif abs(pos.x() - pixel_rect.left()) <= self.edge_size and pixel_rect.top() <= pos.y() <= pixel_rect.bottom():
            return True
        # 右边缘
        elif abs(pos.x() - pixel_rect.right()) <= self.edge_size and pixel_rect.top() <= pos.y() <= pixel_rect.bottom():
            return True
        # 上边缘
        elif abs(pos.y() - pixel_rect.top()) <= self.edge_size and pixel_rect.left() <= pos.x() <= pixel_rect.right():
            return True
        # 下边缘
        elif abs(pos.y() - pixel_rect.bottom()) <= self.edge_size and pixel_rect.left() <= pos.x() <= pixel_rect.right():
            return True
        return False

    def get_resize_edge(self, pos, rect):
        """获取调整大小的边缘类型"""
        # 右下角
        if abs(pos.x() - rect.right()) <= self.edge_size and abs(pos.y() - rect.bottom()) <= self.edge_size:
            return "bottomright"
        # 右上角
        elif abs(pos.x() - rect.right()) <= self.edge_size and abs(pos.y() - rect.top()) <= self.edge_size:
            return "topright"
        # 左下角
        elif abs(pos.x() - rect.left()) <= self.edge_size and abs(pos.y() - rect.bottom()) <= self.edge_size:
            return "bottomleft"
        # 左上角
        elif abs(pos.x() - rect.left()) <= self.edge_size and abs(pos.y() - rect.top()) <= self.edge_size:
            return "topleft"
        # 左边缘
        elif abs(pos.x() - rect.left()) <= self.edge_size and rect.top() <= pos.y() <= rect.bottom():
            return "left"
        # 右边缘
        elif abs(pos.x() - rect.right()) <= self.edge_size and rect.top() <= pos.y() <= rect.bottom():
            return "right"
        # 上边缘
        elif abs(pos.y() - rect.top()) <= self.edge_size and rect.left() <= pos.x() <= rect.right():
            return "top"
        # 下边缘
        elif abs(pos.y() - rect.bottom()) <= self.edge_size and rect.left() <= pos.x() <= rect.right():
            return "bottom"
        return None

    def selection_mouse_move(self, event):
        """鼠标移动事件处理"""
        if not self.enable_mouse_events:
            return
        
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        y_ratio, x_ratio = self._pos_to_norm(pos)
        
        if self.is_drawing:
            _, _, sx, _ = self.current_draw_rect
            sy, _, _, _ = self.current_draw_rect
            self.current_draw_rect = (sy, y_ratio, sx, x_ratio)
            self.update_preview_with_rect()
        elif self.resize_edge and self.active_track_index >= 0:
            t = self.tracks[self.active_track_index]
            sy, sx = self.drag_start_pos
            
            if self.resize_edge == "move":
                span_y = t["ymax"] - t["ymin"]
                span_x = t["xmax"] - t["xmin"]
                new_ymin = max(0, min(1 - span_y, t["ymin"] + (y_ratio - sy)))
                new_xmin = max(0, min(1 - span_x, t["xmin"] + (x_ratio - sx)))
                t["ymin"], t["ymax"] = new_ymin, new_ymin + span_y
                t["xmin"], t["xmax"] = new_xmin, new_xmin + span_x
                self.drag_start_pos = (y_ratio, x_ratio)
            else:
                ymin, ymax, xmin, xmax = t["ymin"], t["ymax"], t["xmin"], t["xmax"]
                if "left" in self.resize_edge:
                    xmin = min(xmax - 0.01, x_ratio)
                if "right" in self.resize_edge:
                    xmax = max(xmin + 0.01, x_ratio)
                if "top" in self.resize_edge:
                    ymin = min(ymax - 0.01, y_ratio)
                if "bottom" in self.resize_edge:
                    ymax = max(ymin + 0.01, y_ratio)
                xmin, xmax = max(0, min(xmin, 1)), max(0, min(xmax, 1))
                ymin, ymax = max(0, min(ymin, 1)), max(0, min(ymax, 1))
                if xmin > xmax: xmin, xmax = xmax, xmin
                if ymin > ymax: ymin, ymax = ymax, ymin
                t["ymin"], t["ymax"], t["xmin"], t["xmax"] = ymin, ymax, xmin, xmax
            self.update_preview_with_rect()
        else:
            self.update_cursor_shape(pos)
    
    def selection_mouse_release(self, event):
        """鼠标释放事件处理"""
        if not self.enable_mouse_events:
            return
            
        if self.is_drawing:
            ymin, ymax, xmin, xmax = self.current_draw_rect
            if ymin > ymax: ymin, ymax = ymax, ymin
            if xmin > xmax: xmin, xmax = xmax, xmin
            sw, sh, _, _ = self._get_frame_params()
            pw = (xmax - xmin) * sw
            ph = (ymax - ymin) * sh
            if pw > 5 and ph > 5:
                current_frame = self.video_slider.value()
                track_id = self._track_id_counter
                track = {
                    "id": track_id,
                    "ymin": ymin, "ymax": ymax, "xmin": xmin, "xmax": xmax,
                    "start": 1, "end": max(self.video_slider.maximum(), 1),
                    "enabled": True, "collapsed": False,
                    "color": self._get_track_color(track_id),
                }
                self._track_id_counter += 1
                self.tracks.append(track)
                self.active_track_index = len(self.tracks) - 1
                self.tracks_changed.emit(self.tracks)
            self.is_drawing = False
            self.current_draw_rect = (0, 0, 0, 0)
        elif self.resize_edge and self.active_track_index >= 0:
            self.tracks_changed.emit(self.tracks)
            self.resize_edge = None
        
    def update_cursor_shape(self, pos):
        """根据鼠标位置更新光标形状"""
        def _px_rect(t):
            return self._norm_to_rect(t["xmin"], t["ymin"], t["xmax"], t["ymax"])

        current_frame = self.video_slider.value()

        # active track first
        active_t = self.tracks[self.active_track_index] if (0 <= self.active_track_index < len(self.tracks)) else None
        if active_t and (active_t["start"] <= current_frame <= active_t["end"]):
            px = _px_rect(active_t)
            if self.is_on_rect_edge(pos, px):
                et = self.get_resize_edge(pos, px)
                cursor_map = {
                    "left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
                    "top": Qt.CursorShape.SizeVerCursor, "bottom": Qt.CursorShape.SizeVerCursor,
                    "topleft": Qt.CursorShape.SizeFDiagCursor, "bottomright": Qt.CursorShape.SizeFDiagCursor,
                    "topright": Qt.CursorShape.SizeBDiagCursor, "bottomleft": Qt.CursorShape.SizeBDiagCursor,
                }
                if et in cursor_map:
                    self.video_display.setCursor(cursor_map[et])
                    return
            elif px.contains(pos):
                self.video_display.setCursor(Qt.CursorShape.SizeAllCursor)
                return

        for t in self.tracks:
            if not (t["start"] <= current_frame <= t["end"]):
                continue
            px = _px_rect(t)
            if self.is_on_rect_edge(pos, px):
                et = self.get_resize_edge(pos, px)
                cursor_map = {
                    "left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
                    "top": Qt.CursorShape.SizeVerCursor, "bottom": Qt.CursorShape.SizeVerCursor,
                    "topleft": Qt.CursorShape.SizeFDiagCursor, "bottomright": Qt.CursorShape.SizeFDiagCursor,
                    "topright": Qt.CursorShape.SizeBDiagCursor, "bottomleft": Qt.CursorShape.SizeBDiagCursor,
                }
                if et in cursor_map:
                    self.video_display.setCursor(cursor_map[et])
                    return
            elif px.contains(pos):
                self.video_display.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        self.video_display.setCursor(Qt.CursorShape.ArrowCursor)
    
    def set_video_parameters(self, frame_width, frame_height, fps=30, display_mode=0):
        """设置视频参数"""
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.fps = fps
        self.fill_mode = (display_mode == 1)
        self._update_coord_mapper()
    
    def set_display_mode(self, mode):
        self.fill_mode = (mode == 1)
        self._coord_mapper.fill_mode = self.fill_mode
    
    def get_tracks(self):
        """获取轨道列表"""
        return self.tracks

    def get_ab_sections(self):
        """从轨道导出AB分区（0-indexed，兼容后端）"""
        return [range(t["start"] - 1, t["end"]) for t in self.tracks if t.get("enabled", True)]

    def _update_coord_mapper(self):
        self._coord_mapper.set_video_params(
            self.frame_width, self.frame_height,
            self.scaled_width, self.scaled_height,
            self.border_left, self.border_top,
            self.fill_mode
        )

    def preview_coordinates_to_video_coordinates(self, preview_selection_rects):
        """将归一化坐标 (0~1 对应显示区域) 转换为原始视频像素坐标"""
        self._update_coord_mapper()
        return self._coord_mapper.preview_to_video(preview_selection_rects)

    @staticmethod
    def _get_track_color(track_id):
        """根据轨道编号取颜色，第一条绿色，后续从调色板循环，超出的随机生成"""
        idx = track_id - 1
        if idx < len(TRACK_COLORS):
            return TRACK_COLORS[idx]
        # 调色板用完 → 随机生成明快颜色
        import random
        h = random.randint(0, 359)
        s = random.randint(55, 100)
        v = random.randint(70, 100)
        h /= 360
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(h, s / 100, v / 100)
        return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"

    def _get_next_track_color(self):
        """新轨道在绘制中应使用的颜色（与最终轨道颜色同步）"""
        return self._get_track_color(self._track_id_counter)

    def show_status(self, text):
        self.status_label.setText(text)
        self.status_label.show()
        self.video_display.hide()

    def hide_status(self):
        self.status_label.hide()
        self.video_display.show()

    def clear_display(self):
        """清空视频显示"""
        self._last_frame = None
        self.current_pixmap = None
        self.video_display.clear()
        self.video_display.setPixmap(QtGui.QPixmap())

    def set_dragger_enabled(self, enabled):
        """设置拖动器是否可用"""
        self.enable_mouse_events = enabled
        self.video_display.setMouseTracking(enabled)
        self.video_display.setCursor(Qt.CursorShape.ArrowCursor)

    def clear_selections(self):
        """清除所有选区"""
        self.tracks = []
        self.active_track_index = -1
        self._track_id_counter = 1
        self.update_preview_with_rect()
        self.tracks_changed.emit(self.tracks)

    def __handle_delete_selection(self):
        """处理删除当前选区的逻辑"""
        try:
            if 0 <= self.active_track_index < len(self.tracks):
                self.tracks.pop(self.active_track_index)
                if self.tracks:
                    self.active_track_index = len(self.tracks) - 1
                else:
                    self.active_track_index = -1
                self.update_preview_with_rect()
                self.tracks_changed.emit(self.tracks)
                return True
            return False
        finally:
            global_pos = QCursor.pos()
            pos = self.video_display.mapFromGlobal(global_pos)
            self.update_cursor_shape(pos)


    
    def __adjust_slider_value(self, delta):
        """调整视频滑块的值"""
        current_value = self.video_slider.value()
        max_value = self.video_slider.maximum()
        new_value = current_value + int(delta)
        
        # 确保新值在有效范围内
        if new_value < self.video_slider.minimum():
            new_value = self.video_slider.minimum()
        elif new_value > max_value:
            new_value = max_value
            
        # 设置新值
        self.video_slider.setValue(new_value)

    def eventFilter(self, obj, event):
        """事件过滤器"""
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self.__handle_delete_selection():
                return True
        return super().eventFilter(obj, event)

    def __init_context_menu(self):
        """初始化右键菜单"""
        self.context_menu = QMenu(self)
        self.action_delete_selection = QAction(tr['SubtitleExtractorGUI']['DeleteSelection'], self)
        self.action_delete_selection.setShortcut("DELETE")
        self.action_delete_selection.triggered.connect(self.__handle_delete_selection)
        self.context_menu.addAction(self.action_delete_selection)

    def closeEvent(self, event):
        """窗口关闭时断开信号连接"""
        try:
            self.action_delete_selection.triggered.disconnect(self.__handle_delete_selection)
            self.shortcut_delete_selection.activated.disconnect(self.__handle_delete_selection)
        except Exception as e:
            logger.exception("Error during close window: %s", e)
        super().closeEvent(event)