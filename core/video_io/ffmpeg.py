"""FFmpeg CLI 封装：路径管理与音频合并。"""

import os
import re
import shutil
import stat
import platform
import threading
import logging
import subprocess
import tempfile
import traceback

from infra.utils import get_readable_path
from infra.config import BASE_DIR

logger = logging.getLogger(__name__)

# 超时常量（秒）
FFMPEG_VERSION_TIMEOUT = 5
FFMPEG_PROBE_TIMEOUT = 60
FFMPEG_LONG_TIMEOUT = 600

# 拒绝 shell 注入字符：; | & ` $ ( ) \n \r ! \0
_PATH_DANGER_CHARS = re.compile(r'[;|&`$()\n\r!\x00]')


def _validate_path(path):
    """校验文件路径，防止命令注入。拒绝包含 shell 元字符的路径。"""
    if not path or not isinstance(path, str):
        raise ValueError(f"Invalid path: {path!r}")
    if _PATH_DANGER_CHARS.search(path):
        raise ValueError(f"Path contains potentially dangerous characters: {path!r}")
    return path


class FFmpegCLI:
    """
    FFmpeg CLI 单例，管理 ffmpeg 路径与版本信息。
    """
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls):
        """单例模式获取实例"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = FFmpegCLI()
        return cls._instance

    def __init__(self):
        path = self.ffmpeg_path
        try:
            os.chmod(path, stat.S_IRWXU + stat.S_IRWXG + stat.S_IRWXO)
        except (OSError, FileNotFoundError):
            pass
        version_info = 'unknown'
        try:
            result = subprocess.run([path, '-version'], capture_output=True, text=True, timeout=FFMPEG_VERSION_TIMEOUT)
            version_info = result.stdout.split('\n')[0] if result.stdout else 'unknown'
        except Exception as e:
            logger.warning('ffmpeg_version_check_failed: %s', e)
        logger.info('ffmpeg_init: path=%s, version=%s', path, version_info)

    @property
    def ffmpeg_path(self):
        system = platform.system()
        if system == "Windows":
            bundled = os.path.join(BASE_DIR, 'core', 'video_io', 'ffmpeg', 'win_x64', 'ffmpeg.exe')
        elif system == "Linux":
            bundled = os.path.join(BASE_DIR, 'core', 'video_io', 'ffmpeg', 'linux_x64', 'ffmpeg')
        else:
            bundled = os.path.join(BASE_DIR, 'core', 'video_io', 'ffmpeg', 'macos', 'ffmpeg')

        if os.path.exists(bundled):
            return bundled

        system_ffmpeg = shutil.which('ffmpeg')
        if system_ffmpeg:
            return system_ffmpeg

        return bundled

    def get_frame_count(self, video_path):
        """
        使用 ffmpeg 快速扫描获取视频的实际帧数（比 OpenCV 的 CAP_PROP_FRAME_COUNT 准确）。
        返回实际帧数，失败时返回 None。
        """
        try:
            safe_path = get_readable_path(video_path) or video_path
            _validate_path(safe_path)
            result = subprocess.run(
                [self.ffmpeg_path, '-i', safe_path, '-map', '0:v:0', '-c', 'copy', '-f', 'null', '-'],
                capture_output=True, timeout=FFMPEG_LONG_TIMEOUT
            )
            stderr_text = result.stderr.decode('utf-8', errors='replace')
            last_frame = None
            for line in stderr_text.split('\n'):
                line = line.strip()
                if line.startswith('frame='):
                    frame_str = line.split('=')[1].strip().split()[0]
                    last_frame = int(frame_str)
            logger.info('ffmpeg_frame_count: path=%s, count=%s', video_path, last_frame)
            if last_frame is not None:
                return last_frame
        except Exception as e:
            logger.warning('ffmpeg_frame_count_failed: path=%s, error=%s', video_path, e)
        return None


def _video_has_audio_stream(ffmpeg_path, video_path):
    """探测视频是否包含音频流。返回 True/False，探测失败时返回 None。"""
    try:
        _validate_path(video_path)
        # 以默认级别扫描，解析 stderr 中的 "Audio:" 行判断是否含音频流
        result = subprocess.run(
            [ffmpeg_path, "-i", video_path],
            capture_output=True, timeout=FFMPEG_PROBE_TIMEOUT
        )
        text = result.stderr.decode('utf-8', errors='replace')
        return 'Audio:' in text
    except Exception as e:
        logger.warning('audio_probe_failed: path=%s, error=%s', video_path, e)
        return None


def _copy_video_without_audio(video_out_path, output_path):
    """无音频时直接复制去字幕视频到输出路径。"""
    _validate_path(video_out_path)
    _validate_path(output_path)
    shutil.copy2(video_out_path, output_path)
    logger.info('video_copied_no_audio: %s', output_path)
    return True


def merge_audio_to_video(video_path, video_out_path, output_path, ffmpeg_path):
    """
    Extract audio from original video and merge with subtitle-free video.
    源视频无音频流时，直接复制去字幕视频作为输出，不再视为错误。
    """
    _validate_path(video_path)
    # 先探测源视频是否包含音频流，避免对无音频视频报错
    has_audio = _video_has_audio_stream(ffmpeg_path, video_path)
    if has_audio is False:
        if os.path.exists(video_out_path):
            return _copy_video_without_audio(video_out_path, output_path)
        logger.warning('audio_merge_skipped: temp not found %s', video_out_path)
        return False

    temp = tempfile.NamedTemporaryFile(suffix='.aac', delete=False)
    audio_extract_command = [ffmpeg_path,
                             "-y", "-i", video_path,
                             "-acodec", "copy",
                             "-vn", "-loglevel", "error", temp.name]
    use_shell = False
    logger.info('audio_extract_start: %s', video_path)
    try:
        subprocess.check_output(audio_extract_command, stdin=subprocess.DEVNULL, shell=use_shell, timeout=FFMPEG_LONG_TIMEOUT)
        logger.info('audio_extract_success')
    except subprocess.CalledProcessError as e:
        raw = e.stderr or b''
        stderr_text = raw.decode('utf-8', errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw)
        # 探测失败时，若提取阶段的错误明确表明无音频流：视为正常情况，直接生成无音频视频
        if has_audio is None and ('does not contain any stream' in stderr_text or 'no audio streams' in stderr_text):
            logger.info('audio_extract_skipped_no_audio_stream')
            if os.path.exists(video_out_path):
                return _copy_video_without_audio(video_out_path, output_path)
            return False
        logger.error('audio_extract_failed: %s, stderr=%s', e, stderr_text)
        traceback.print_exc()
        return False
    except Exception as e:
        logger.error('audio_extract_failed: %s', e)
        traceback.print_exc()
        return False
    else:
        if os.path.exists(video_out_path):
            logger.info('audio_merge_start: audio=%s, output=%s', temp.name, output_path)
            _validate_path(video_out_path)
            _validate_path(output_path)
            audio_merge_command = [ffmpeg_path,
                                   "-y", "-i", video_out_path,
                                   "-i", temp.name,
                                   "-vcodec", "copy",
                                   "-acodec", "copy",
                                   "-loglevel", "error", output_path]
            try:
                subprocess.check_output(audio_merge_command, stdin=subprocess.DEVNULL, shell=use_shell, timeout=FFMPEG_LONG_TIMEOUT)
                logger.info('audio_merge_success')
                return True
            except Exception as e:
                logger.error('audio_merge_failed: %s', e)
                traceback.print_exc()
                return False
        logger.warning('audio_merge_skipped: temp not found %s', video_out_path)
        return False
    finally:
        # 必须先关闭句柄再删除文件，否则 Windows 上会 PermissionError
        try:
            temp.close()
        except Exception:
            pass
        if os.path.exists(temp.name):
            try:
                os.remove(temp.name)
            except Exception:
                pass
