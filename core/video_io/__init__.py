"""视频 I/O 模块：视频读取、写入与 FFmpeg 封装。"""

from .video_reader import create_video_capture, FramePrefetcher
from .video_writer import FFmpegVideoWriter
from .ffmpeg import FFmpegCLI, merge_audio_to_video

__all__ = [
    'create_video_capture',
    'FramePrefetcher',
    'FFmpegVideoWriter',
    'FFmpegCLI',
    'merge_audio_to_video',
]
