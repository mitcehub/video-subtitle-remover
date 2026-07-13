import multiprocessing
import threading
import logging
import cv2
from enum import Enum

logger = logging.getLogger(__name__)

class Command(Enum):
    FINISH = 0
    PROGRESS = 1
    LOG = 2
    ERROR = 3
    UPDATE_PREVIEW_WITH_COMP = 4

class SubtitleRemoverRemoteCall:
    """
    远程回调函数类，用于在多进程环境中传递回调函数
    """
    def __init__(self):
        self.queue = multiprocessing.Queue()
        self.callbacks = {}
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            while self.running:
                try:
                    cmd, args = self.queue.get(block=True)
                except (EOFError, BrokenPipeError, ConnectionResetError, OSError, AssertionError):
                    break
                if cmd == Command.FINISH:
                    break
                callback = self.callbacks.get(cmd)
                if callback:
                    # 单个回调异常不应终止整个消息循环，否则后续 PROGRESS/LOG/FINISH/ERROR 全部丢失
                    try:
                        callback(*args)
                    except Exception:
                        logger.exception('RemoteCall callback failed for cmd=%s', cmd)
        except Exception:
            pass
        finally:
            self.running = False

    def stop(self):
        self.running = False
        self.queue.put((Command.FINISH, (None,)))

    def register_update_progress_callback(self, callback):
        self.callbacks[Command.PROGRESS] = callback

    def register_log_callback(self, callback):
        self.callbacks[Command.LOG] = callback

    def register_update_preview_with_comp_callback(self, callback):
        self.callbacks[Command.UPDATE_PREVIEW_WITH_COMP] = callback

    def register_error_callback(self, callback):
        self.callbacks[Command.ERROR] = callback

    @staticmethod
    def remote_call_update_progress(queue, progress, isFinished):
        queue.put((Command.PROGRESS, (progress, isFinished,)))

    @staticmethod
    def remote_call_append_log(queue, *args):
        queue.put((Command.LOG, (*args,)))

    @staticmethod
    def remote_call_finish(queue, *args):
        queue.put((Command.FINISH, (None,)))

    @staticmethod
    def remote_call_catch_error(queue, e):
        queue.put((Command.ERROR, (e,)))

    @staticmethod
    def _downscale_for_preview(frame, max_width=640):
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if w <= max_width:
            return frame
        scale = max_width / w
        new_h = int(h * scale)
        return cv2.resize(frame, (max_width, new_h), interpolation=cv2.INTER_AREA)

    @staticmethod
    def remote_call_update_preview_with_comp(queue, *args):
        downscaled_args = []
        for arg in args:
            if isinstance(arg, cv2.UMat):
                arg = arg.get()
            if hasattr(arg, 'shape'):
                downscaled_args.append(SubtitleRemoverRemoteCall._downscale_for_preview(arg))
            else:
                downscaled_args.append(arg)
        queue.put((Command.UPDATE_PREVIEW_WITH_COMP, (*downscaled_args,)))
