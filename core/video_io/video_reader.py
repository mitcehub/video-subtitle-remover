"""视频读取：创建 VideoCapture 与帧预读取。"""

import os
import queue
import threading
import logging

import cv2

logger = logging.getLogger(__name__)


def create_video_capture(path, api_preference=None):
    """创建 VideoCapture 并尝试启用硬件加速解码（自动检测 GPU DirectX 加速）。"""
    if api_preference is not None:
        cap = cv2.VideoCapture(path, api_preference)
    else:
        cap = cv2.VideoCapture(path)
    opened = cap.isOpened()
    hw_accel = None
    if opened and hasattr(cv2, 'CAP_PROP_HW_ACCELERATION'):
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

    def __init__(self, video_cap, buffer_size=10):
        self.cap = video_cap
        self._buffer = queue.Queue(maxsize=buffer_size)
        self._stopped = False
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        logger.info('frame_prefetcher_started: buffer_size=%d', buffer_size)

    def _read_loop(self):
        frames_read = 0
        try:
            while not self._stopped:
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
        """读取下一帧，接口与 cv2.VideoCapture.read() 一致。"""
        result = self._buffer.get()
        ret, frame = result
        return ret, frame

    def get(self, propId):
        return self.cap.get(propId)

    def stop(self):
        """停止预读取，不释放底层 video_cap。"""
        self._stopped = True
        qsize = self._buffer.qsize()
        try:
            while not self._buffer.empty():
                self._buffer.get_nowait()
        except queue.Empty:
            pass
        self._thread.join(timeout=5)
        logger.info('frame_prefetcher_stopped: buffer_drained=%d', qsize)

    def release(self):
        self.stop()
        self.cap.release()
