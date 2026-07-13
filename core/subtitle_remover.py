"""字幕去除核心编排器。"""

import shutil
import traceback
import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Optional
import threading
import cv2
import sys
import tempfile
import time
from tqdm import tqdm

from infra.config import config, tr, MODEL_PATH
from infra.hardware import HardwareAccelerator
from infra.utils import get_readable_path
from core.inpaint.inpaint import STTNAutoInpaint
from core.inpaint.mask import create_mask
from core.video_io.ffmpeg import FFmpegCLI, merge_audio_to_video
from core.video_io.video_reader import create_video_capture
from core.video_io.video_writer import FFmpegVideoWriter

logger = logging.getLogger(__name__)


@dataclass
class ProcessingCallbacks:
    on_log: Callable[[str], None] = field(default_factory=lambda: lambda msg: print(msg))
    on_progress: Callable[[int, bool], None] = field(default_factory=lambda: lambda pct, done: None)
    on_preview: Callable[[object, object], None] = field(default_factory=lambda: lambda ori, comp: None)


class SubtitleRemover:
    def __init__(self, video_path, gui_mode=False, callbacks: Optional[ProcessingCallbacks] = None):
        self.lock = threading.RLock()
        self.sub_areas = []
        self.gui_mode = gui_mode
        self.callbacks = callbacks or ProcessingCallbacks()
        self.hardware_accelerator = HardwareAccelerator.instance()
        self.hardware_accelerator.set_enabled(config.hardwareAcceleration.value)

        self.video_path = video_path
        self.video_cap = create_video_capture(get_readable_path(video_path))
        self.vd_name = Path(video_path).stem
        self.frame_count = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT) + 0.5)
        self.fps = self.video_cap.get(cv2.CAP_PROP_FPS)
        self.frame_width = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.size = (self.frame_width, self.frame_height)
        self.mask_size = (self.frame_height, self.frame_width)

        fourcc = int(self.video_cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = ''.join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)) if fourcc else 'unknown'
        logger.info('video_opened: path=%s, %dx%d, fps=%.2f, frames=%d, codec=%s',
                 video_path, self.frame_width, self.frame_height, self.fps, self.frame_count, fourcc_str)

        self.video_temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        self.video_writer = FFmpegVideoWriter(get_readable_path(self.video_temp_file.name), self.fps, self.size)
        self.video_out_path = os.path.abspath(os.path.join(os.path.dirname(video_path), f'{self.vd_name}_no_sub.mp4'))

        self.progress_total = 0
        self.isFinished = False
        self.is_successful_merged = False
        self.ab_sections = None
        self.track_data = None

    def _emit_log(self, *args):
        msg = ' '.join(str(a) for a in args)
        logger.info('[output] %s', msg)
        self.callbacks.on_log(msg)

    def _emit_progress(self):
        self.callbacks.on_progress(self.progress_total, self.isFinished)

    def _emit_preview(self, frame_ori, frame_comp):
        self.callbacks.on_preview(frame_ori, frame_comp)

    def update_progress(self, tbar, increment):
        tbar.update(increment)
        self.progress_total = int((tbar.n / tbar.total) * 100)
        self._emit_progress()

    def run(self):
        start_time = time.time()
        logger.info('processing_start: frames=%d, fps=%s', self.frame_count, self.fps)

        if len(self.sub_areas) == 0:
            self._emit_log(tr['Main']['FullScreenProcessingNote'])
            self.sub_areas.append((0, self.frame_height, 0, self.frame_width))

        self._emit_log(tr['Main']['SubtitleArea'].format(self.sub_areas))
        ab_str = str(self.ab_sections).replace("range", "") if self.ab_sections else tr['Main']['ABSectionAll']
        self._emit_log(tr['Main']['ABSection'].format(ab_str))
        os.makedirs(os.path.dirname(self.video_out_path), exist_ok=True)

        self.progress_total = 0
        tbar = tqdm(total=self.frame_count, unit='frame', position=0, file=sys.__stdout__, desc='Subtitle Removing')
        try:
            self._log_model()
            self._process_video(tbar)

            self.video_cap.release()
            self.video_writer.pad_to(self.frame_count)
            self.video_writer.release()
            self._merge_audio()

            elapsed = round(time.time() - start_time, 1)
            self._emit_log(tr['Main']['FinishedProcessing'].format(elapsed))
            self._emit_log(tr['Main']['SavedTo'].format(self.video_out_path))
            logger.info('processing_complete: output=%s, time=%.1fs', self.video_out_path, elapsed)
        except Exception:
            logger.exception('processing_error')
            self._emit_log(tr['SubtitleExtractorGUI']['ErrorDuringProcessing'].format(traceback.format_exc()))
            raise
        finally:
            self._cleanup()

    def _process_video(self, tbar):
        self._emit_log(tr['Main']['ProcessingStartRemovingSubtitles'])
        mask = create_mask(self.mask_size, list(self.sub_areas))
        sttn = STTNAutoInpaint(self.hardware_accelerator.device, MODEL_PATH, self.video_path)
        sttn(input_mask=mask, input_sub_remover=self, tbar=tbar, gui_mode=self.gui_mode)

    def _log_model(self):
        device = 'CPU'
        if self.hardware_accelerator.has_accelerator():
            name = self.hardware_accelerator.accelerator_name
            if self.hardware_accelerator.has_cuda() or self.hardware_accelerator.has_mps():
                device = name
            elif name == 'DirectML':
                device = 'DirectML'
        self._emit_log(tr['Main']['SubtitleRemoverModel'].format(f"STTN Auto ({device})"))

    def _merge_audio(self):
        self.is_successful_merged = merge_audio_to_video(
            self.video_path, self.video_temp_file.name, self.video_out_path,
            FFmpegCLI.instance().ffmpeg_path
        )
        if not self.is_successful_merged:
            try:
                shutil.copy2(self.video_temp_file.name, self.video_out_path)
            except IOError as e:
                self._emit_log(tr['Main']['CopyFileFailed'].format(self.video_temp_file.name, self.video_out_path, str(e)))
        self.video_temp_file.close()

    def _cleanup(self):
        try:
            self.video_cap.release()
        except Exception:
            pass
        try:
            self.video_writer.release()
        except Exception:
            pass
        self.isFinished = True
        self.progress_total = 100
        self._emit_progress()
        try:
            self.video_temp_file.close()
        except Exception:
            pass
        if os.path.exists(self.video_temp_file.name):
            try:
                os.remove(self.video_temp_file.name)
            except Exception:
                pass
