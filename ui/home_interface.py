import os
import cv2
import queue
import threading
import multiprocessing
import traceback
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QFrame, QTextEdit
from PyQt6.QtCore import pyqtSlot, pyqtSignal, Qt, QTimer
from PyQt6 import QtWidgets, QtGui
from datetime import datetime
from qfluentwidgets import (PushButton, CardWidget, TextEdit, FluentIcon, PrimaryPushButton, ToolButton)
from ui.setting_interface import SettingInterface
from ui.component.video_display_component import VideoDisplayComponent
from ui.component.timeline_widget import TimelineWidget

from ui.icon.my_fluent_icon import MyFluentIcon
from infra.config import config, tr
from ui.subtitle_remover_remote_call import SubtitleRemoverRemoteCall
from ui.playback_controller import PlaybackController
from app.pipeline import ProcessingPipeline
from infra.process_manager import ProcessManager
from infra.utils import get_readable_path
from core.video_io.video_reader import create_video_capture
from ui.preview_decoder import PreviewDecoder


class HomeInterface(QWidget):
    progress_signal = pyqtSignal(int, bool)
    append_log_signal = pyqtSignal(list)
    update_preview_with_comp_signal = pyqtSignal(list)
    task_error_signal = pyqtSignal(object)
    toggle_buttons_signal = pyqtSignal(bool)

    MODE_SINGLE = 0
    MODE_COMPARE = 1

    DISPLAY_FIT = 0
    DISPLAY_FILL = 1

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HomeInterface")
        self.video_path = None
        self.output_path = None
        self.video_cap = None
        self.output_cap = None
        self.fps = None
        self.frame_count = None
        self.frame_width = None
        self.frame_height = None
        self.se = None
        self.current_mode = self.MODE_COMPARE
        self.display_mode = self.DISPLAY_FIT
        self._has_processed = False
        self._processing_finished_called = False

        self.auto_scroll = True
        self._stop_event = threading.Event()
        self._worker_thread = None
        self.running_process = None
        self._video_cap_lock = threading.Lock()

        # 存储最后一帧原片和处理结果（供 Compare 模式使用）
        self._last_ori_frame = None
        self._last_comp_frame = None
        self._result_frame = None
        self._preview_decoder = None
        self._preview_queue = queue.Queue(maxsize=2)
        self._proc_pid = None

        self.__init_widgets()
        self._playback = PlaybackController(self)
        self._pipeline = ProcessingPipeline()
        self.progress_signal.connect(self.update_progress)
        self.append_log_signal.connect(self.append_log)
        self.update_preview_with_comp_signal.connect(self.update_preview_with_comp)
        self.task_error_signal.connect(self.on_task_error)
        self.toggle_buttons_signal.connect(self._toggle_buttons)

    def __init_widgets(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(0)

        # ============ 中间面板 ============
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 8, 0)
        center_layout.setSpacing(6)

        self.video_display_component = VideoDisplayComponent(self)
        self.video_display_component.tracks_changed.connect(self._on_tracks_changed)
        center_layout.addWidget(self.video_display_component, 1)

        self.video_display = self.video_display_component.video_display
        self.video_slider = self.video_display_component.video_slider
        self.video_slider.valueChanged.connect(self.slider_changed)
        self.video_slider.valueChanged.connect(lambda v: self.timeline.set_current_frame(v))

        # 空格键切换播放/暂停
        self._shortcut_space = QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_space.activated.connect(self._toggle_playback)

        # 控制栏：时间码 | 播放控制居中 | 显示模式 | 视图模式
        control_bar = QWidget()
        control_layout = QHBoxLayout(control_bar)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(4)

        self.time_label = QtWidgets.QLabel('00:00:00 / 00:00:00')
        self.time_label.setStyleSheet('color: #aaa; font-size: 12px;')
        self.time_label.setFixedWidth(180)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        control_layout.addWidget(self.time_label)

        control_layout.addStretch()

        self.jump_start_btn = ToolButton(MyFluentIcon.SkipStart, self)
        self.jump_start_btn.setToolTip(tr['Main']['JumpToStart'])
        self.jump_start_btn.setEnabled(False)
        self.jump_start_btn.setFixedWidth(32)
        control_layout.addWidget(self.jump_start_btn)

        self.prev_btn = ToolButton(MyFluentIcon.PrevFrame, self)
        self.prev_btn.setToolTip(tr['Main']['PreviousFrame'])
        self.prev_btn.setEnabled(False)
        self.prev_btn.setFixedWidth(32)
        control_layout.addWidget(self.prev_btn)

        self.play_btn = ToolButton(FluentIcon.PLAY, self)
        self.play_btn.setToolTip(tr['Main']['Play'])
        self.play_btn.setEnabled(False)
        self.play_btn.setFixedWidth(32)
        control_layout.addWidget(self.play_btn)

        self.speed_btn = ToolButton(MyFluentIcon.Speed1x, self)
        self.speed_btn.setToolTip(tr['Main']['PlaySpeed'])
        self.speed_btn.setEnabled(False)
        self.speed_btn.setFixedWidth(32)
        control_layout.addWidget(self.speed_btn)

        self.next_btn = ToolButton(MyFluentIcon.NextFrame, self)
        self.next_btn.setToolTip(tr['Main']['NextFrame'])
        self.next_btn.setEnabled(False)
        self.next_btn.setFixedWidth(32)
        control_layout.addWidget(self.next_btn)

        self.jump_end_btn = ToolButton(MyFluentIcon.SkipEnd, self)
        self.jump_end_btn.setToolTip(tr['Main']['JumpToEnd'])
        self.jump_end_btn.setEnabled(False)
        self.jump_end_btn.setFixedWidth(32)
        control_layout.addWidget(self.jump_end_btn)

        control_layout.addStretch()

        # 显示模式
        self.fit_btn = PushButton(tr['Main']['FitView'], self)
        self.fit_btn.setEnabled(False)
        self.fill_btn = PushButton(tr['Main']['FillView'], self)
        self.fill_btn.setEnabled(False)
        control_layout.addWidget(self.fit_btn)
        control_layout.addWidget(self.fill_btn)

        self.fit_btn.clicked.connect(lambda: self.set_display_mode(self.DISPLAY_FIT))
        self.fill_btn.clicked.connect(lambda: self.set_display_mode(self.DISPLAY_FILL))

        # 播放控制图标缓存（供 PlaybackController 使用）
        self._play_icon = FluentIcon.PLAY
        self._pause_icon = FluentIcon.PAUSE
        self._speed1x_icon = MyFluentIcon.Speed1x
        self._speed2x_icon = MyFluentIcon.Speed2x
        self._speed4x_icon = MyFluentIcon.Speed4x

        self.jump_start_btn.clicked.connect(lambda: self._playback.jump_to_start())
        self.prev_btn.clicked.connect(lambda: self._playback.step_backward())
        self.play_btn.clicked.connect(lambda: self._playback.toggle())
        self.speed_btn.clicked.connect(lambda: self._playback.cycle_speed())
        self.next_btn.clicked.connect(lambda: self._playback.step_forward())
        self.jump_end_btn.clicked.connect(lambda: self._playback.jump_to_end())

        # 键盘快捷键
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Home), self).activated.connect(lambda: self._playback.jump_to_start())
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_End), self).activated.connect(lambda: self._playback.jump_to_end())
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_I), self).activated.connect(self._jump_to_in_point)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_O), self).activated.connect(self._jump_to_out_point)

        center_layout.addWidget(control_bar)

        # 时间轴（固定高度）
        self.timeline = TimelineWidget(self)
        self.timeline.frame_selected.connect(self._on_timeline_frame_selected)
        self.timeline.preview_seek.connect(self._on_preview_seek)
        # 不持久化轨道数据到配置文件，开新视频时自动清空
        center_layout.addWidget(self.timeline, 0)

        self._preview_poll_timer = QTimer(self)
        self._preview_poll_timer.setInterval(30)
        self._preview_poll_timer.timeout.connect(self._poll_preview_frame)

        # ============ 右侧边栏 ============
        right_panel = QWidget()
        right_panel.setMinimumWidth(300)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        # 基础设置
        settings_container = CardWidget(self)
        self.setting_interface = SettingInterface(settings_container)
        settings_container.setLayout(self.setting_interface)
        right_layout.addWidget(settings_container)

        # 输出日志
        self.output_text = TextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(80)
        self.output_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.output_text.document().setDocumentMargin(4)
        self.output_text.setStyleSheet("background: transparent; border: none;")
        self.output_text.verticalScrollBar().valueChanged.connect(self._on_scroll_change)
        right_layout.addWidget(self.output_text, 1)

        # 操作按钮
        action_card = CardWidget(self)
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(12, 10, 12, 10)
        action_layout.setSpacing(6)

        self.file_button = PushButton(tr['SubtitleExtractorGUI']['Open'], self)
        self.file_button.setIcon(FluentIcon.FOLDER)
        self.file_button.clicked.connect(self.open_file)
        self.file_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        action_layout.addWidget(self.file_button)

        self.run_button = PrimaryPushButton(tr['SubtitleExtractorGUI']['Run'], self)
        self.run_button.setIcon(FluentIcon.PLAY)
        self.run_button.clicked.connect(self.run_button_clicked)
        action_layout.addWidget(self.run_button)

        self.stop_button = PushButton(tr['SubtitleExtractorGUI']['Stop'], self)
        self.stop_button.setIcon(MyFluentIcon.Stop)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop_button_clicked)
        action_layout.addWidget(self.stop_button)

        right_layout.addWidget(action_card)

        # ============ 双栏 QSplitter（中栏 + 右栏） ============
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(center_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([900, 220])
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setHandleWidth(4)

        main_layout.addWidget(self.splitter, 1)

    # ==================== 时间标签更新 ====================

    def _update_time_label(self, frame_num, fps, total_frames):
        if fps <= 0 or total_frames <= 0:
            return
        def tc(fn):
            fn_0 = max(0, min(fn - 1, (total_frames or 1) - 1))
            sec = int(fn_0 / fps)
            fr = int(fn_0 % fps)
            m, s = divmod(sec, 60)
            h, m = divmod(m, 60)
            if h > 0:
                return f'{h:02d}:{m:02d}:{s:02d}:{fr:02d}'
            return f'{m:02d}:{s:02d}:{fr:02d}'
        self.time_label.setText(f'{tc(frame_num)} / {tc(total_frames)}')

    # ==================== 模式切换 ====================

    def _set_mode(self, mode):
        self.current_mode = mode
        self.slider_changed(self.video_slider.value())

    def _set_disp_buttons_enabled(self, enabled):
        self.fit_btn.setEnabled(enabled)
        self.fill_btn.setEnabled(enabled)
        if enabled:
            active_s = 'QPushButton { background: #409eff; color: white; border: none; border-radius: 4px; padding: 4px 8px; }'
            inactive_s = 'QPushButton { background: transparent; color: #ccc; border: none; border-radius: 4px; padding: 4px 8px; }'
            self.fit_btn.setStyleSheet(active_s if self.display_mode == self.DISPLAY_FIT else inactive_s)
            self.fill_btn.setStyleSheet(active_s if self.display_mode == self.DISPLAY_FILL else inactive_s)
        else:
            s = 'QPushButton { background: transparent; color: #666; border: none; border-radius: 4px; padding: 4px 8px; }'
            self.fit_btn.setStyleSheet(s)
            self.fill_btn.setStyleSheet(s)

    def reset_processed_state(self):
        self._has_processed = False
        self._last_ori_frame = None
        self._last_comp_frame = None
        self._result_frame = None
        self.output_path = None
        self._playback._playback_speed = 1
        self.speed_btn.setIcon(MyFluentIcon.Speed1x)
        self.speed_btn.setToolTip(tr['Main']['PlaySpeed'])
        if self.output_cap:
            self.output_cap.release()
            self.output_cap = None
        self._stop_event.clear()
        if self._playback.is_playing:
            self._playback.stop()
        self.video_display_component.clear_selections()
        self.output_text.clear()



    # ==================== 播放控制 ====================

    def _jump_to_in_point(self):
        tracks = self.video_display_component.get_tracks()
        min_start = None
        for t in tracks:
            if t.get("enabled", True):
                s = t.get("start", 1)
                if min_start is None or s < min_start:
                    min_start = s
        if min_start is not None:
            self.video_slider.setValue(min_start)

    def _jump_to_out_point(self):
        tracks = self.video_display_component.get_tracks()
        max_end = None
        for t in tracks:
            if t.get("enabled", True):
                e = t.get("end", self.video_slider.maximum())
                if max_end is None or e > max_end:
                    max_end = e
        if max_end is not None:
            self.video_slider.setValue(max_end)

    def _toggle_playback(self):
        self._playback.toggle()

    def _playback_tick(self):
        # Compare 模式仍用 seek 保持双轨同步
        if self.current_mode == self.MODE_COMPARE:
            v = self.video_slider.value() + 1
            if v > self.video_slider.maximum():
                self._playback.stop()
                return
            self.video_slider.setValue(v)
            return
        # Single 模式：顺序读取（不 seek），流畅播放
        cap = self.output_cap if (self._has_processed and self.output_cap and self.output_cap.isOpened()) else self.video_cap
        if cap is None or not cap.isOpened():
            self._playback.stop()
            return
        ret, frame = cap.read()
        if not ret:
            self._playback.stop()
            return
        fi = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        if self.fps and self.frame_count:
            self._update_time_label(fi, self.fps, self.frame_count)
        self.video_slider.blockSignals(True)
        self.video_slider.setValue(fi)
        self.video_slider.blockSignals(False)
        self.timeline.set_current_frame(fi)
        self.update_preview(frame)

    def _set_playback_buttons_enabled(self, enabled):
        self._playback.set_buttons_enabled(enabled)

    # ==================== 显示控制 ====================

    def set_display_mode(self, mode):
        self.display_mode = mode
        self.video_display_component.set_display_mode(mode)
        active_s = 'QPushButton { background: #409eff; color: white; border: none; border-radius: 4px; padding: 4px 8px; }'
        inactive_s = 'QPushButton { background: transparent; color: #ccc; border: none; border-radius: 4px; padding: 4px 8px; }'
        self.fit_btn.setStyleSheet(active_s if mode == self.DISPLAY_FIT else inactive_s)
        self.fill_btn.setStyleSheet(active_s if mode == self.DISPLAY_FILL else inactive_s)
        self.slider_changed(self.video_slider.value())

    # ==================== 视频加载与预览 ====================

    def slider_changed(self, value):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        if self.fps and self.frame_count:
            self._update_time_label(value, self.fps, self.frame_count)

        if self.current_mode == self.MODE_COMPARE:
            # Compare: 左右拼接（原片 + 结果）
            ori_frame = None
            with self._video_cap_lock:
                if self.video_cap and self.video_cap.isOpened():
                    self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, value - 1))
                    ret, ori_frame = self.video_cap.read()
                    if not ret:
                        ori_frame = None
            comp_frame = None
            if self.output_cap and self.output_cap.isOpened():
                self.output_cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, value - 1))
                ret, comp_frame = self.output_cap.read()
                if not ret:
                    comp_frame = None
            # 处理中缓存帧兜底
            if ori_frame is None and self._last_ori_frame is not None:
                ori_frame = self._last_ori_frame
            if comp_frame is None and self._last_comp_frame is not None:
                comp_frame = self._last_comp_frame
            if ori_frame is not None and comp_frame is not None:
                if ori_frame.shape[0] != comp_frame.shape[0]:
                    h = min(ori_frame.shape[0], comp_frame.shape[0])
                    ori_frame = cv2.resize(ori_frame, (int(ori_frame.shape[1] * h / ori_frame.shape[0]), h))
                    comp_frame = cv2.resize(comp_frame, (int(comp_frame.shape[1] * h / comp_frame.shape[0]), h))
                combined = cv2.hconcat([ori_frame, comp_frame])
                resized = self._img_resize(combined)
                self.video_display_component.update_video_display(resized, draw_selection=False)
            elif ori_frame is not None:
                # 无处理结果时只显示原片
                resized = self._img_resize(ori_frame)
                self.video_display_component.update_video_display(resized, draw_selection=False)
            return

        cap = self.output_cap if (self._has_processed and self.output_cap and self.output_cap.isOpened()) else self.video_cap
        frame = None
        if cap is not None and cap.isOpened():
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, value - 1))
            ret, frame = cap.read()
            if not ret:
                frame = None
                logger.debug('slider_changed: frame=%d seek failed', value)
        else:
            logger.debug('slider_changed: cap not available for frame=%d', value)
        if frame is not None:
            self.update_preview(frame)
        else:
            self.video_display_component.clear_display()

    def update_preview(self, frame):
        resized_frame = self._img_resize(frame)
        self.video_display_component.set_video_parameters(
            self.frame_width, self.frame_height,
            self.fps if self.fps is not None else 30,
            self.display_mode,
        )
        self.video_display_component.update_video_display(resized_frame)

    def _img_resize(self, image):
        # 不再做缩放，交由 update_video_display 一次性完成
        return image

    def load_video(self, video_path):
        logger.info('video_load_start: %s', video_path)
        self.video_path = video_path
        with self._video_cap_lock:
            if self.video_cap:
                self.video_cap.release()
                self.video_cap = None
            self.video_cap = create_video_capture(get_readable_path(self.video_path))
            if not self.video_cap.isOpened():
                self.video_cap = None
                logger.warning('video_open_failed: %s', video_path)
                return False
            ret, frame = self.video_cap.read()
            if not ret:
                self.video_cap.release()
                self.video_cap = None
                logger.warning('video_first_frame_failed: %s', video_path)
                return False
            self.frame_count = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.frame_height = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.frame_width = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.fps = self.video_cap.get(cv2.CAP_PROP_FPS)
            logger.info('video_loaded: size=%dx%d, fps=%.2f, frames=%d',
                         self.frame_width, self.frame_height, self.fps, self.frame_count)

        self._stop_preview_decoder()
        self._preview_decoder = PreviewDecoder(
            get_readable_path(self.video_path), self._preview_queue)
        self._preview_poll_timer.start()

        self.reset_processed_state()
        self.current_mode = self.MODE_SINGLE
        self._set_disp_buttons_enabled(False)
        self._set_playback_buttons_enabled(True)

        self.update_preview(frame)
        self.video_slider.setMaximum(self.frame_count)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        self._update_time_label(1, self.fps, self.frame_count)
        self.timeline.set_data(self.video_display_component.get_tracks(), self.frame_count, self.fps)
        self.timeline.set_current_frame(1)
        return True
        self.frame_height = frame.shape[0]
        self.frame_width = frame.shape[1]
        self.fps = 1
        self.reset_processed_state()
        self.current_mode = self.MODE_SINGLE
        self._set_disp_buttons_enabled(False)
        self._set_playback_buttons_enabled(True)
        self.update_preview(frame)
        self.video_slider.setMaximum(self.frame_count)
        self.video_slider.setValue(1)
        self.video_display_component.set_dragger_enabled(True)
        self._update_time_label(1, self.fps, self.frame_count)
        self.timeline.set_data(self.video_display_component.get_tracks(), self.frame_count, self.fps)
        self.timeline.set_current_frame(1)
        return True

    # ==================== 设置弹窗 ====================

    # ==================== 轨道 / 时间轴回调 ====================

    def _on_tracks_changed(self, _ignored=None):
        self.timeline.set_data(self.video_display_component.get_tracks(), self.frame_count or 1, self.fps or 30)
        self.timeline.set_current_frame(self.video_slider.value())

    def _on_timeline_frame_selected(self, frame):
        frame = max(1, min(frame, self.frame_count or 1))
        if self._worker_thread and self._worker_thread.is_alive():
            return
        if frame != self.video_slider.value():
            self.video_slider.setValue(frame)

    def _stop_preview_decoder(self):
        self._preview_poll_timer.stop()
        if self._preview_decoder:
            self._preview_decoder.stop()
            self._preview_decoder = None
        while not self._preview_queue.empty():
            try:
                self._preview_queue.get_nowait()
            except queue.Empty:
                break

    def _on_preview_seek(self, frame):
        if not self._preview_decoder:
            return
        fi = max(0, min(frame - 1, (self.frame_count or 1) - 1))
        self._preview_decoder.seek(fi)
        if not self._preview_poll_timer.isActive():
            self._preview_poll_timer.start()
        # 立即刷新选框（不等待解码器线程），滤镜使用当前滑块值+共享的轨道字典
        self.video_display_component.update_preview_with_rect()

    def _poll_preview_frame(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        try:
            fi, frame = self._preview_queue.get_nowait()
        except queue.Empty:
            return
        self._last_ori_frame = frame
        self.video_slider.blockSignals(True)
        self.video_slider.setValue(fi + 1)
        self.video_slider.blockSignals(False)
        resized = self._img_resize(frame)
        self.video_display_component.update_video_display(resized, draw_selection=True)
        if self.fps and self.frame_count:
            self._update_time_label(fi + 1, self.fps, self.frame_count)

    # ==================== 运行 / 停止 ====================

    def run_button_clicked(self):
        logger.info('run_button_clicked')
        if not self.video_path:
            self.append_output(tr['SubtitleExtractorGUI']['OpenVideoFirst'])
            return

        if not os.path.exists(self.video_path):
            logger.warning('file_not_found: %s', self.video_path)
            self.append_output(f"File not found: {self.video_path}")
            return

        logger.info('processing_start: path=%s, tracks=%s, ab_sections=%s',
                     self.video_path,
                     [t.get("id") for t in self.video_display_component.get_tracks()],
                     self.video_display_component.get_ab_sections())

        self._preview_poll_timer.stop()
        self._playback.stop()
        self.video_display_component.set_dragger_enabled(False)
        self.timeline.setEnabled(False)
        self._set_playback_buttons_enabled(False)
        self.video_display_component.show_status(tr['Main']['WaitRendering'])

        # STTN 模式固定使用框选区域遮罩，双窗口直接对比
        self.current_mode = self.MODE_COMPARE

        # 坐标转换必须视图模式确定之后执行，
        # 避免 compare 模式拼接帧后覆盖 scaled_width/scaled_height 导致坐标映射错误
        tracks = self.video_display_component.get_tracks()
        track_data_px = []
        for t in tracks:
            rects = self.video_display_component.preview_coordinates_to_video_coordinates(
                [(t["ymin"], t["ymax"], t["xmin"], t["xmax"])])
            if rects:
                ymin_px, ymax_px, xmin_px, xmax_px = rects[0]
                track_data_px.append({
                    "id": t["id"],
                    "ymin": ymin_px, "ymax": ymax_px,
                    "xmin": xmin_px, "xmax": xmax_px,
                    "start": t["start"], "end": t["end"],
                    "enabled": t.get("enabled", True),
                })
        options = {
            'track_data': track_data_px,
            'sub_areas': self.video_display_component.preview_coordinates_to_video_coordinates(
                [(t["ymin"], t["ymax"], t["xmin"], t["xmax"]) for t in tracks if t.get("enabled", True)]
            ),
            'ab_sections': self.video_display_component.get_ab_sections(),
        }

        self.video_display_component.show_status(tr['Main']['WaitRendering'])

        try:
            self._stop_event.clear()
            self._processing_finished_called = False
            self.toggle_buttons_signal.emit(False)

            # 计算输出路径
            save_dir = config.saveDirectory.value if config.saveDirectory.value else os.path.dirname(self.video_path)
            stem = Path(self.video_path).stem
            ext = Path(self.video_path).suffix
            self.output_path = os.path.abspath(os.path.join(save_dir, f'{stem}_no_sub{ext}'))

            def task():
                try:
                    with self._video_cap_lock:
                        if self.video_cap:
                            self.video_cap.release()
                            self.video_cap = None

                    process = self.run_subtitle_remover_process(self.video_path, self.output_path, options)

                    if self._stop_event.is_set():
                        return

                    # 处理完成
                    if process.exitcode == 0:
                        self.progress_signal.emit(100, True)
                    else:
                        self.append_log_signal.emit([tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(f"exit code: {process.exitcode}")])
                except Exception as e:
                    logger.error('processing_task_error: %s', traceback.format_exc())
                    self.append_log_signal.emit([f"Error: {e}"])
                finally:
                    self.toggle_buttons_signal.emit(True)

            self._worker_thread = threading.Thread(target=task, daemon=True)
            self._worker_thread.start()
        except Exception as e:
            logger.error('thread_start_error: %s', traceback.format_exc())
            self.append_log_signal.emit([f"Error: {e}"])
            self.toggle_buttons_signal.emit(True)

    def stop_button_clicked(self):
        try:
            self._stop_event.set()
            self.append_output(tr['Main']['StopProcessing'])
            logger.info('user_stop_processing')
            # Directly kill child process by PID
            if self._proc_pid is not None:
                try:
                    import subprocess
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(self._proc_pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                except Exception:
                    pass
            ProcessManager.instance().terminate_all()
            if self._pipeline:
                self._pipeline.stop()
        finally:
            self._proc_pid = None
            self._worker_thread = None
            self.video_display_component.hide_status()
            self.video_display_component.set_dragger_enabled(True)
            self.timeline.setEnabled(True)
            self._set_playback_buttons_enabled(True)
            self.run_button.setVisible(True)
            self.stop_button.setVisible(False)

    @pyqtSlot(bool)
    def _toggle_buttons(self, show_run):
        self.run_button.setVisible(show_run)
        self.stop_button.setVisible(not show_run)
        if show_run:
            self.video_display_component.hide_status()
            self.video_display_component.set_dragger_enabled(True)
            self.timeline.setEnabled(True)
            self._set_playback_buttons_enabled(True)

    # ==================== 子进程处理 ====================

    def run_subtitle_remover_process(self, video_path, output_path, options):
        from dataclasses import dataclass
        @dataclass
        class Callbacks:
            on_progress = self.progress_signal.emit
            on_log = self.append_log_signal.emit
            on_preview = self.update_preview_with_comp_signal.emit
            on_error = self.task_error_signal.emit
        result = self._pipeline.start(video_path, output_path, options, Callbacks())
        if result and hasattr(result, 'pid'):
            self._proc_pid = result.pid
        return result

    # ==================== 进度 / 日志 / 错误 ====================

    @pyqtSlot(int, bool)
    def update_progress(self, progress_total, isFinished):
        try:
            if self.frame_count:
                pos = int(progress_total / 100 * self.frame_count)
                pos = max(1, min(pos, self.frame_count))
                if pos != self.video_slider.value():
                    self.video_slider.blockSignals(True)
                    self.video_slider.setValue(pos)
                    self.video_slider.blockSignals(False)
                self.timeline.set_current_frame(pos)
                self._update_time_label(pos, self.fps, self.frame_count)

            if isFinished:
                self.processing_finished()
        except Exception as e:
            logger.warning('progress_update_error: %s', e)

    @pyqtSlot()
    def processing_finished(self):
        if self._processing_finished_called:
            return
        # worker 线程未退出时 slider_changed 会早返回，延迟到线程结束后再处理
        if self._worker_thread and self._worker_thread.is_alive():
            QTimer.singleShot(200, self.processing_finished)
            return
        self._processing_finished_called = True
        logger.info('processing_finished_callback')
        logger.info('  video_path=%s', self.video_path)
        logger.info('  output_path=%s', self.output_path)
        self.run_button.setVisible(True)
        self.stop_button.setVisible(False)
        self.video_display_component.hide_status()
        self._set_playback_buttons_enabled(True)
        self.se = None

        if self.output_path and os.path.exists(self.output_path):
            logger.info('output_video_open_start')
            self._has_processed = True
            if self.output_cap:
                self.output_cap.release()
            self.output_cap = create_video_capture(get_readable_path(self.output_path))
            if self.output_cap.isOpened():
                logger.info('output_video_opened')
            else:
                logger.warning('output_video_open_failed')
                self.output_cap = None
                self._has_processed = False

        logger.info('source_video_reopen')
        with self._video_cap_lock:
            if self.video_cap:
                self.video_cap.release()
            self.video_cap = create_video_capture(get_readable_path(self.video_path))
            if self.video_cap.isOpened():
                logger.info('source_video_opened')
            else:
                logger.warning('source_video_reopen_failed')

        # 处理完成，切换到单窗口预览输出结果
        self.current_mode = self.MODE_SINGLE

        # 重置时间指针到开头
        self.video_slider.setValue(1)
        if self.output_cap and self.output_cap.isOpened():
            out_frames = int(self.output_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if out_frames > 0:
                self.video_slider.setMaximum(out_frames)
        self.timeline.set_current_frame(1)
        self._update_time_label(1, self.fps, self.frame_count)

        # 刷新当前视图
        if self._has_processed and hasattr(self, '_result_frame') and self._result_frame is not None:
            self.update_preview(self._result_frame)
        else:
            self.slider_changed(self.video_slider.value())
        # 处理完成后不再需要预览解码器（源视频），使用输出视频直接播放
        self._stop_preview_decoder()

    @pyqtSlot(list)
    def append_log(self, log):
        self.append_output(*log)

    def append_output(self, *args):
        text = ' '.join(str(arg) for arg in args).rstrip()
        if not text:
            return
        timestamp = datetime.now().strftime('%H:%M:%S')
        escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if '错误' in text or 'Error' in text or '失败' in text or 'Failed' in text:
            color = '#e74c3c'
        elif '成功' in text or '完成' in text or 'Success' in text or 'Finished' in text:
            color = '#27ae60'
        elif '警告' in text or 'Warning' in text:
            color = '#f39c12'
        else:
            color = '#2980b9'
        html = f'<span style="color:#888;">[{timestamp}]</span> <span style="color:{color};">{escaped}</span><br>'
        self.output_text.append(html)
        print(*args)
        if self.auto_scroll:
            sb = self.output_text.verticalScrollBar()
            sb.setValue(sb.maximum())

        # 开始移除字幕 → 自动切换到双窗口对比显示
        if '开始移除字幕' in text:
            self.current_mode = self.MODE_COMPARE

    def _on_scroll_change(self, value):
        sb = self.output_text.verticalScrollBar()
        if value == sb.maximum():
            self.auto_scroll = True
        elif self.auto_scroll and value < sb.maximum():
            self.auto_scroll = False

    @pyqtSlot(list)
    def update_preview_with_comp(self, args):
        """处理中实时预览"""
        frame_ori, frame_comp = args
        self._last_ori_frame = frame_ori
        self._last_comp_frame = frame_comp

        self.video_display_component.set_dragger_enabled(False)
        self._set_disp_buttons_enabled(True)
        if self.current_mode == self.MODE_SINGLE:
            resized = self._img_resize(frame_comp)
            self.video_display_component.update_video_display(resized, draw_selection=False)
        elif self.current_mode == self.MODE_COMPARE:
            if frame_ori.shape[0] != frame_comp.shape[0]:
                h = min(frame_ori.shape[0], frame_comp.shape[0])
                frame_ori = cv2.resize(frame_ori, (int(frame_ori.shape[1] * h / frame_ori.shape[0]), h))
                frame_comp = cv2.resize(frame_comp, (int(frame_comp.shape[1] * h / frame_comp.shape[0]), h))
            combined = cv2.hconcat([frame_ori, frame_comp])
            resized = self._img_resize(combined)
            self.video_display_component.update_video_display(resized, draw_selection=False)
        self.video_display_component.hide_status()

    @pyqtSlot(object)
    def on_task_error(self, e):
        self.append_output(tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(str(e)))

    # ==================== 打开文件 ====================

    def open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            tr['SubtitleExtractorGUI']['Open'],
            "",
            "All Files (*.*);;Video Files (*.mp4 *.flv *.wmv *.avi *.mkv *.mov);;Image Files (*.jpg *.jpeg *.png *.bmp *.webp *.tiff)"
        )
        if path:
            if self.load_video(path):
                self.append_output(f"{tr['SubtitleExtractorGUI']['OpenVideoSuccess']}: {path}")
            else:
                self.append_output(f"{tr['SubtitleExtractorGUI']['OpenVideoFailed']}: {path}")

    # ==================== 关闭清理 ====================

    def closeEvent(self, event):
        try:
            self._stop_event.set()
            ProcessManager.instance().terminate_all()
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=5)

            self.progress_signal.disconnect(self.update_progress)
            self.append_log_signal.disconnect(self.append_log)
            self.update_preview_with_comp_signal.disconnect(self.update_preview_with_comp)
            self.task_error_signal.disconnect(self.on_task_error)
            self.toggle_buttons_signal.disconnect(self._toggle_buttons)
            self.video_display_component.video_slider.valueChanged.disconnect(self.slider_changed)
            self.video_display_component.tracks_changed.disconnect(self._on_tracks_changed)
            self.timeline.frame_selected.disconnect(self._on_timeline_frame_selected)
            self.timeline.preview_seek.disconnect(self._on_preview_seek)
            self._stop_preview_decoder()

            with self._video_cap_lock:
                if self.video_cap:
                    self.video_cap.release()
                    self.video_cap = None
            if self.output_cap:
                self.output_cap.release()
                self.output_cap = None
        except Exception as e:
            logger.warning('close_event_error: %s', e)
        super().closeEvent(event)
