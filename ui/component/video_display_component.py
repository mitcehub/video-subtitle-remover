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
        self._track_id_counter = 0
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
        self.__init_shotcuts()
        
    def __init_widgets(self):
        """初始化组件"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 视频显示标签
        self.video_display = QtWidgets.QLabel()
        self.video_display.setStyleSheet("""
            background-color: black;
            border-radius: 10px;
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
    
    def __init_shotcuts(self):
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

        # 上圆角裁剪路径
        path = QtGui.QPainterPath()
        radius = 8
        fx, fy = x_off, y_off
        fw, fh = target_w, target_h
        path.moveTo(fx + radius, fy)
        path.lineTo(fx + fw - radius, fy)
        path.arcTo(fx + fw - radius * 2, fy, radius * 2, radius * 2, 90, -90)
        path.lineTo(fx + fw, fy + fh)
        path.lineTo(fx, fy + fh)
        path.lineTo(fx, fy + radius)
        path.arcTo(fx, fy, radius * 2, radius * 2, 180, -90)
        path.closeSubpath()

        painter.setClipPath(path)
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

    def _draw_ratio_indicator(self, painter, track, pixel_rect):
        if self.frame_width is None or self.frame_height is None:
            return
        fw, fh = self.frame_width, self.frame_height
        vw = (track["xmax"] - track["xmin"]) * fw
        vh = (track["ymax"] - track["ymin"]) * fh
        if vh <= 0 or vw <= 0:
            return
        ratio = vw / vh
        STTN_TARGET = 640 / 120
        err = abs(ratio - STTN_TARGET) / STTN_TARGET

        tx = pixel_rect.left()
        ty = pixel_rect.top() - 8

        painter.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold))
        if config.useBestRatioConstraint.value:
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 200, 83)))
            r = QtCore.QRect(tx, ty - 20, 300, 24)
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"比例：{STTN_TARGET:.1f}:1  锁定已开启")
        else:
            if err < 0.05:
                color = QtGui.QColor(0, 200, 83)
            elif err < 0.15:
                color = QtGui.QColor(255, 193, 7)
            elif err < 0.30:
                color = QtGui.QColor(255, 152, 0)
            else:
                color = QtGui.QColor(244, 67, 54)
            painter.setFont(QtGui.QFont("Arial", 10, QtGui.QFont.Weight.Bold))
            painter.setPen(QtGui.QPen(color))
            r = QtCore.QRect(tx, ty - 20, 400, 24)
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"最佳: {STTN_TARGET:.1f}:1  当前: {ratio:.1f}:1  锁定已关闭")

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
                    self._draw_ratio_indicator(painter, t, pixel_rect)

            if self.is_drawing and self.current_draw_rect and any(self.current_draw_rect):
                pen = QtGui.QPen(QtGui.QColor(0, 255, 0))
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
            # STTN 模式固定启用比例锁定选项
            self.action_lock_ratio.setVisible(True)
            self.action_lock_ratio.setChecked(config.useBestRatioConstraint.value)
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
                if config.useBestRatioConstraint.value and self.resize_edge != "move" and self._get_target_ratio() is not None:
                    self._snap_to_target_ratio(t)
                    self.update_preview_with_rect()
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

    def _get_target_ratio(self):
        if self.frame_width is None or self.frame_height is None:
            return None
        return (640 / 120) * (self.frame_height / self.frame_width)

    def _snap_to_target_ratio(self, t):
        target = self._get_target_ratio()
        if target is None:
            return
        cy = (t["ymin"] + t["ymax"]) / 2
        cx = (t["xmin"] + t["xmax"]) / 2
        w = t["xmax"] - t["xmin"]
        h = t["ymax"] - t["ymin"]
        if w <= 0 or h <= 0:
            return
        cur = w / h
        if abs(cur - target) / target < 0.001:
            return
        if cur > target:
            new_h = w / target
            y1 = cy - new_h / 2
            y2 = cy + new_h / 2
            if y1 < 0:
                y1, y2 = 0, new_h
            elif y2 > 1:
                y1, y2 = 1 - new_h, 1
            if y1 >= 0 and y2 <= 1:
                t["ymin"], t["ymax"] = y1, y2
        else:
            new_w = h * target
            x1 = cx - new_w / 2
            x2 = cx + new_w / 2
            if x1 < 0:
                x1, x2 = 0, new_w
            elif x2 > 1:
                x1, x2 = 1 - new_w, 1
            if x1 >= 0 and x2 <= 1:
                t["xmin"], t["xmax"] = x1, x2

    def _apply_constrained_resize(self, t, edge, x_ratio, y_ratio):
        target = self._get_target_ratio()
        if target is None:
            return False
        cx = (t["xmin"] + t["xmax"]) / 2
        cy = (t["ymin"] + t["ymax"]) / 2

        if edge == "right":
            xmax = max(cx + 0.005, x_ratio)
            w = (xmax - cx) * 2
            h = w / target
        elif edge == "left":
            xmin = min(cx - 0.005, x_ratio)
            w = (cx - xmin) * 2
            h = w / target
        elif edge == "bottom":
            ymax = max(cy + 0.005, y_ratio)
            h = (ymax - cy) * 2
            w = h * target
        elif edge == "top":
            ymin = min(cy - 0.005, y_ratio)
            h = (cy - ymin) * 2
            w = h * target
        elif edge in ("bottomright", "topright", "bottomleft", "topleft"):
            use_x = abs(x_ratio - cx) >= abs(y_ratio - cy)
            if use_x:
                w = abs(x_ratio - cx) * 2
                h = w / target
            else:
                h = abs(y_ratio - cy) * 2
                w = h * target
        else:
            return False

        x1 = cx - w / 2
        x2 = cx + w / 2
        y1 = cy - h / 2
        y2 = cy + h / 2
        if x1 < 0 or x2 > 1 or y1 < 0 or y2 > 1:
            return False
        t["xmin"], t["xmax"] = x1, x2
        t["ymin"], t["ymax"] = y1, y2
        return True

    def selection_mouse_move(self, event):
        """鼠标移动事件处理"""
        if not self.enable_mouse_events:
            return
        
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        y_ratio, x_ratio = self._pos_to_norm(pos)
        
        if self.is_drawing:
            target = self._get_target_ratio()
            if config.useBestRatioConstraint.value and target is not None:
                _, _, anchor_x, _ = self.current_draw_rect
                anchor_y, _, _, _ = self.current_draw_rect
                dw = x_ratio - anchor_x
                dh = y_ratio - anchor_y
                sx = 1 if dw >= 0 else -1
                sy_ = 1 if dh >= 0 else -1
                w = abs(dw)
                h = abs(dh)
                if h < 1e-6:
                    h = w / target if w > 1e-6 else 0.01
                elif w < 1e-6:
                    w = h * target
                elif w / h >= target:
                    h = w / target
                else:
                    w = h * target
                self.current_draw_rect = (anchor_y, anchor_y + sy_ * h, anchor_x, anchor_x + sx * w)
            else:
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
            elif config.useBestRatioConstraint.value and self._get_target_ratio() is not None:
                self._apply_constrained_resize(t, self.resize_edge, x_ratio, y_ratio)
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
                colors = TRACK_COLORS
                track = {
                    "id": self._track_id_counter,
                    "ymin": ymin, "ymax": ymax, "xmin": xmin, "xmax": xmax,
                    "start": 1, "end": max(self.video_slider.maximum(), 1),
                    "enabled": True, "collapsed": False,
                    "color": colors[self._track_id_counter % len(colors)],
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
        self._track_id_counter = 0
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
        self.action_lock_ratio = QAction("锁定选框最佳比例", self)
        self.action_lock_ratio.setCheckable(True)
        self.action_lock_ratio.setChecked(config.useBestRatioConstraint.value)
        self.action_lock_ratio.triggered.connect(self._on_lock_ratio_toggled)
        self.context_menu.addAction(self.action_lock_ratio)

        self.context_menu.addSeparator()
        self.action_delete_selection = QAction(tr['SubtitleExtractorGUI']['DeleteSelection'], self)
        self.action_delete_selection.setShortcut("DELETE")
        self.action_delete_selection.triggered.connect(self.__handle_delete_selection)
        self.context_menu.addAction(self.action_delete_selection)

    def _on_lock_ratio_toggled(self, checked):
        config.set(config.useBestRatioConstraint, checked)
        self.action_lock_ratio.setChecked(checked)
        if checked:
            for t in self.tracks:
                self._snap_to_target_ratio(t)
        self.update_preview_with_rect()

    def closeEvent(self, event):
        """窗口关闭时断开信号连接"""
        try:
            self.action_delete_selection.triggered.disconnect(self.__handle_delete_selection)
            self.shortcut_delete_selection.activated.disconnect(self.__handle_delete_selection)
        except Exception as e:
            logger.exception("Error during close window: %s", e)
        super().closeEvent(event)