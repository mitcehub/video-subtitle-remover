"""视频预览解码器：后台线程按需解码帧并缓存。"""

import cv2
import queue
import threading
import logging

from core.video_io.video_reader import create_video_capture

logger = logging.getLogger(__name__)


class PreviewDecoder:
    def __init__(self, video_path, result_queue, max_cache=120):
        self.video_path = video_path
        self.queue = result_queue
        self.max_cache = max_cache
        self._target = -1
        self._running = True
        self._cache = {}
        self._order = []
        self._event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def seek(self, frame_idx):
        self._target = frame_idx
        self._event.set()

    def stop(self):
        self._running = False
        self._event.set()
        self._thread.join(timeout=2)

    def _run(self):
        cap = create_video_capture(self.video_path)
        cap_pos = -1
        while self._running:
            self._event.wait(timeout=0.5)
            self._event.clear()
            if not self._running:
                break
            fi = self._target
            if fi < 0:
                continue
            if fi in self._cache:
                self._put_result(fi, self._cache[fi])
                continue
            pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
            if 0 <= fi - pos <= 60 and cap_pos >= 0:
                for _ in range(int(fi - pos)):
                    cap.read()
            else:
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ret, frame = cap.read()
            if ret:
                cap_pos = fi
                if len(self._order) >= self.max_cache:
                    old = self._order.pop(0)
                    self._cache.pop(old, None)
                self._cache[fi] = frame
                self._order.append(fi)
                self._put_result(fi, frame)
        cap.release()

    def _put_result(self, fi, frame):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        self.queue.put((fi, frame.copy()))
