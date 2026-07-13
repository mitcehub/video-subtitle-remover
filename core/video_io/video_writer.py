"""视频写入：通过 FFmpeg 管道写入帧。"""

import subprocess
import threading
import logging

import numpy as np

from .ffmpeg import FFmpegCLI

logger = logging.getLogger(__name__)


class FFmpegVideoWriter:
    """
    通过 FFmpeg 管道写入帧，libx264 CRF 1 无损输出。
    接口兼容 cv2.VideoWriter（write/release）。
    """

    def __init__(self, output_path, fps, size):
        w, h = size
        cmd = [
            FFmpegCLI.instance().ffmpeg_path,
            '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{w}x{h}',
            '-pix_fmt', 'bgr24',
            '-r', str(fps),
            '-i', '-',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-crf', '1',
            '-preset', 'medium',
            '-loglevel', 'error',
            output_path
        ]
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._returncode = None
        self._frames_written = 0
        self._last_frame = None
        self._size = (w, h)
        self._broken = False
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        logger.info('video_writer_initialized: path=%s, fps=%s, %dx%d', output_path, fps, w, h)

    def _drain_stderr(self):
        """读取 FFmpeg stderr 到日志，避免 pipe buffer 满阻塞，同时保留诊断信息。"""
        try:
            for line in self._process.stderr:
                if line.strip():
                    logger.warning('[ffmpeg] %s', line.rstrip())
        except Exception:
            pass

    def write(self, frame):
        """写入一帧（numpy BGR 数组）。"""
        if self._broken:
            raise RuntimeError('FFmpegVideoWriter: pipe broken, write aborted')
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        try:
            self._process.stdin.write(frame.tobytes())
        except BrokenPipeError:
            logger.warning('video_writer_broken_pipe: frames_written=%d', self._frames_written)
            self._broken = True
            return
        self._frames_written += 1
        self._last_frame = frame

    @property
    def frames_written(self):
        return self._frames_written

    def pad_to(self, target_count):
        """用最后一帧补齐到 target_count 帧。"""
        if self._last_frame is None:
            logger.warning('video_writer_pad_no_frames')
            return
        missing = target_count - self._frames_written
        if missing <= 0:
            return
        logger.info('video_writer_padding: missing=%d, before=%d, target=%d', missing, self._frames_written, target_count)
        for _ in range(missing):
            try:
                self._process.stdin.write(self._last_frame.tobytes())
            except BrokenPipeError:
                logger.warning('video_writer_pad_error: padded=%d', self._frames_written - (target_count - missing))
                break
            self._frames_written += 1

    def release(self):
        """关闭管道并等待编码完成。"""
        if self._returncode is not None:
            return
        try:
            self._process.stdin.close()
        except BrokenPipeError:
            logger.warning('video_writer_broken_pipe_on_close')
        try:
            self._process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            logger.warning('video_writer_timeout: terminating')
            self._process.terminate()
            self._process.wait(timeout=5)
        self._returncode = self._process.returncode
        logger.info('video_writer_released: frames=%d, rc=%s, size=%s', self._frames_written, self._returncode, self._size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False
