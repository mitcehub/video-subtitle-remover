"""视频播放控制器：管理播放/暂停/速度/帧步进。"""

import logging
from PyQt6.QtCore import QTimer, Qt

from infra.config import tr

logger = logging.getLogger(__name__)


class PlaybackController:
    def __init__(self, parent):
        self.parent = parent
        self._is_playing = False
        self._playback_speed = 1
        self._playback_timer = QTimer(parent)
        self._playback_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._playback_timer.timeout.connect(self._tick)

    @property
    def is_playing(self):
        return self._is_playing

    def toggle(self):
        if self._is_playing:
            self.stop()
        else:
            self.play()

    def play(self):
        fps = self.parent.fps or 30
        interval = max(8, int(1000 / fps // self._playback_speed))
        self._playback_timer.setInterval(interval)
        self._playback_timer.start()
        self._is_playing = True
        self.parent.play_btn.setIcon(self.parent._pause_icon)
        self.parent.play_btn.setToolTip(tr['Main']['Pause'])

    def stop(self):
        self._playback_timer.stop()
        self._is_playing = False
        self.parent.play_btn.setIcon(self.parent._play_icon)
        self.parent.play_btn.setToolTip(tr['Main']['Play'])

    def cycle_speed(self):
        speeds = [1, 2, 4]
        icons = [self.parent._speed1x_icon, self.parent._speed2x_icon, self.parent._speed4x_icon]
        idx = speeds.index(self._playback_speed)
        next_idx = (idx + 1) % len(speeds)
        self._playback_speed = speeds[next_idx]
        self.parent.speed_btn.setIcon(icons[next_idx])
        self.parent.speed_btn.setToolTip(tr['Main']['PlaySpeedCurrent'].format(self._playback_speed))
        if self._is_playing:
            fps = self.parent.fps or 30
            interval = max(8, int(1000 / fps // self._playback_speed))
            self._playback_timer.setInterval(interval)

    def step_forward(self):
        v = self.parent.video_slider.value()
        self.parent.video_slider.setValue(min(self.parent.video_slider.maximum(), v + 1))

    def step_backward(self):
        v = self.parent.video_slider.value()
        self.parent.video_slider.setValue(max(1, v - 1))

    def jump_to_start(self):
        self.parent.video_slider.setValue(1)

    def jump_to_end(self):
        self.parent.video_slider.setValue(self.parent.video_slider.maximum())

    def _tick(self):
        self.parent._playback_tick()

    def set_buttons_enabled(self, enabled):
        self.parent.jump_start_btn.setEnabled(enabled)
        self.parent.prev_btn.setEnabled(enabled)
        self.parent.play_btn.setEnabled(enabled)
        self.parent.speed_btn.setEnabled(enabled)
        self.parent.next_btn.setEnabled(enabled)
        self.parent.jump_end_btn.setEnabled(enabled)
        if not enabled and self._is_playing:
            self.stop()
