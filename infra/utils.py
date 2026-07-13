"""通用工具函数：文件类型检测、路径处理。"""

import os
import sys
import ctypes

VIDEO_EXTENSIONS = {
    '.mp4', '.m4a', '.m4v', '.f4v', '.f4a', '.m4b', '.m4r', '.f4b', '.mov',
    '.3gp', '.3gp2', '.3g2', '.3gpp', '.3gpp2', '.ogg', '.oga', '.ogv', '.ogx',
    '.wmv', '.wma', '.asf', '.webm', '.flv', '.avi', '.gifv', '.mkv', '.rm',
    '.rmvb', '.vob', '.dvd', '.mpg', '.mpeg', '.mp2', '.mpe', '.mpv',
    '.m2v', '.svi', '.mxf', '.roq', '.nsv', '.f4p'
}


def get_readable_path(path):
    if sys.platform != 'win32':
        return path
    buf = ctypes.create_unicode_buffer(4096)
    ret = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 4096)
    return buf.value if ret != 0 else path


def is_frame_in_sections(frame_no, sections):
    if sections is None or len(sections) <= 0:
        return True
    for section in sections:
        if frame_no in section:
            return True
    return False
