"""视频读取：创建 VideoCapture 与帧预读取。"""

import os
import queue
import threading
import logging

import cv2

from infra.utils import get_readable_path

logger = logging.getLogger(__name__)


def create_video_capture(path, api_preference=None):
    """创建 VideoCapture 并尝试启用硬件加速解码（自动检测 GPU DirectX 加速）。

    统一在此处对路径做 get_readable_path 转换（Windows 短路径），
    消除各调用方重复包装的不一致问题。打开失败时抛出 FileNotFoundError。
    """
    # 统一做短路径转换（仅 Windows 生效），避免含中文/Unicode 路径打开失败
    safe_path = get_readable_path(path) or path
    if api_preference is not None:
        cap = cv2.VideoCapture(safe_path, api_preference)
    else:
        cap = cv2.VideoCapture(safe_path)
    opened = cap.isOpened()
    if not opened:
        cap.release()
        raise FileNotFoundError(f'cannot open video: {path}')
    hw_accel = None
    if hasattr(cv2, 'CAP_PROP_HW_ACCELERATION'):
        try:
            mode = (cv2.VIDEO_ACCELERATION_ANY if hasattr(cv2, 'VIDEO_ACCELERATION_ANY')
                    else cv2.VIDEO_ACCELERATION_D3D11 if hasattr(cv2, 'VIDEO_ACCELERATION_D3D11')
                    else 0)
            cur = cap.get(cv2.CAP_PROP_HW_ACCELERATION)
            hw_accel = int(cur)
            if cur < 0.5:
                cap.set(cv2.CAP_PROP_HW_ACCELERATION, mode)
                hw_accel = int(mode)
        except Exception:
            pass
    logger.info('video_capture_created: path=%s, opened=%s, hw_accel=%s', path, opened, hw_accel)
    return cap


class FramePrefetcher:
    """
    后台线程预解码视频帧，使 I/O 与模型推理重叠。
    接口兼容 cv2.VideoCapture（read/release）。
    """

    _SENTINEL = (False, None)
    # read() 阻塞超时（秒），避免损坏视频导致永久挂起
    _READ_TIMEOUT = 30

    def __init__(self, video_cap, buffer_size=10):
        self.cap = video_cap
        self._buffer = queue.Queue(maxsize=buffer_size)
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        logger.info('frame_prefetcher_started: buffer_size=%d', buffer_size)

    def _read_loop(self):
        frames_read = 0
        try:
            while not self._stopped.is_set():
                ret, frame = self.cap.read()
                self._buffer.put((ret, frame))
                frames_read += 1
                if not ret:
                    break
            logger.info('frame_prefetcher_finished: frames_read=%d', frames_read)
        except Exception:
            logger.exception('frame_prefetcher_thread_error')
            self._buffer.put((False, None))
            logger.info('frame_prefetcher_error: frames_before_error=%d', frames_read)

    def read(self):
        """读取下一帧，接口与 cv2.VideoCapture.read() 一致。

        带超时，避免生产者线程卡死时消费者永久阻塞。
        超时后返回 (False, None)。
        """
        try:
            result = self._buffer.get(timeout=self._READ_TIMEOUT)
        except queue.Empty:
            logger.warning('frame_prefetcher_read_timeout: %ds', self._READ_TIMEOUT)
            return False, None
        ret, frame = result
        return ret, frame

    def get(self, prop_id):
        return self.cap.get(prop_id)

    def stop(self):
        """停止预读取，不释放底层 video_cap。"""
        self._stopped.set()
        qsize = self._buffer.qsize()
        try:
            while not self._buffer.empty():
                self._buffer.get_nowait()
        except queue.Empty:
            pass
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            logger.warning('frame_prefetcher_thread_still_alive_after_join')
        logger.info('frame_prefetcher_stopped: buffer_drained=%d', qsize)

    def release(self):
        """停止预读取并释放底层 video_cap。"""
        self.stop()
        self.cap.release()
