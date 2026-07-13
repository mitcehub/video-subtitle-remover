"""坐标转换工具：归一化坐标 ↔ 视频像素坐标。"""

import numpy as np


class CoordinateMapper:
    def __init__(self):
        self.frame_width = None
        self.frame_height = None
        self.scaled_width = None
        self.scaled_height = None
        self.border_left = 0
        self.border_top = 0
        self.fill_mode = False

    def set_video_params(self, frame_width, frame_height, scaled_width, scaled_height,
                         border_left, border_top, fill_mode):
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.scaled_width = scaled_width
        self.scaled_height = scaled_height
        self.border_left = border_left
        self.border_top = border_top
        self.fill_mode = fill_mode

    def preview_to_video(self, preview_rects):
        """将归一化坐标 (0~1) 转换为原始视频像素坐标"""
        if self.frame_width is None or self.frame_height is None or self.scaled_width is None:
            return []
        sw, sh = self.scaled_width, self.scaled_height
        if sw <= 0 or sh <= 0:
            return []
        fw, fh = self.frame_width, self.frame_height

        if self.fill_mode:
            pad_left = pad_top = 0.0
            content_w = sw
            content_h = sh
        else:
            target_ratio = sw / sh
            image_ratio = fw / fh
            if image_ratio > target_ratio:
                content_h = sw / image_ratio
                pad_top = (sh - content_h) / 2
                pad_left = 0.0
                content_w = sw
            else:
                content_w = sh * image_ratio
                pad_left = (sw - content_w) / 2
                pad_top = 0.0
                content_h = sh

        result = []
        for rect in preview_rects:
            ymin, ymax, xmin, xmax = rect
            x1 = int((xmin * sw - pad_left) / content_w * fw)
            x2 = int((xmax * sw - pad_left) / content_w * fw)
            y1 = int((ymin * sh - pad_top) / content_h * fh)
            y2 = int((ymax * sh - pad_top) / content_h * fh)
            x1, x2 = max(0, min(x1, fw)), max(0, min(x2, fw))
            y1, y2 = max(0, min(y1, fh)), max(0, min(y2, fh))
            if x1 > x2: x1, x2 = x2, x1
            if y1 > y2: y1, y2 = y2, y1
            if x2 <= x1 or y2 <= y1:
                continue
            result.append((y1, y2, x1, x2))
        return result

    def pos_to_norm(self, pos):
        """将鼠标位置转换为归一化坐标 (0~1)"""
        sw, sh = self.scaled_width or 1, self.scaled_height or 1
        bl, bt = self.border_left or 0, self.border_top or 0
        fx = max(0, min(sw - 1, pos.x() - bl))
        fy = max(0, min(sh - 1, pos.y() - bt))
        return (fy / sh if sh > 0 else 0, fx / sw if sw > 0 else 0)

    def norm_to_rect(self, xmin, ymin, xmax, ymax):
        """将归一化坐标转换为显示区域的 QRect"""
        from PyQt6.QtCore import QRect
        sw, sh = self.scaled_width or 1, self.scaled_height or 1
        bl, bt = self.border_left or 0, self.border_top or 0
        return QRect(
            int(xmin * sw) + bl,
            int(ymin * sh) + bt,
            int((xmax - xmin) * sw),
            int((ymax - ymin) * sh)
        )
