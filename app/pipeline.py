"""处理流水线：子进程启动与管理。"""

import logging
import multiprocessing
import traceback

from core.subtitle_remover import SubtitleRemover, ProcessingCallbacks
from ui.subtitle_remover_remote_call import SubtitleRemoverRemoteCall
from infra.process_manager import ProcessManager

logger = logging.getLogger(__name__)


def remover_process(queue, video_path, output_path, options):
    """子进程入口：运行字幕去除"""
    sr = None
    try:
        callbacks = ProcessingCallbacks(
            on_log=lambda msg: SubtitleRemoverRemoteCall.remote_call_append_log(queue, (msg,)),
            on_progress=lambda pct, done: SubtitleRemoverRemoteCall.remote_call_update_progress(queue, pct, done),
            on_preview=lambda ori, comp: SubtitleRemoverRemoteCall.remote_call_update_preview_with_comp(queue, (ori, comp)),
        )
        sr = SubtitleRemover(video_path, True, callbacks=callbacks)
        sr.video_out_path = output_path
        for key, value in options.items():
            setattr(sr, key, value)
        sr.run()
    except Exception as e:
        traceback.print_exc()
        SubtitleRemoverRemoteCall.remote_call_catch_error(queue, e)
    finally:
        if sr:
            sr.isFinished = True
        SubtitleRemoverRemoteCall.remote_call_finish(queue)


class ProcessingPipeline:
    """处理流水线管理器"""

    def __init__(self):
        self.running_process = None

    def start(self, video_path, output_path, options, callbacks):
        """启动处理子进程"""
        caller = SubtitleRemoverRemoteCall()
        caller.register_update_progress_callback(callbacks.on_progress)
        caller.register_log_callback(callbacks.on_log)
        caller.register_update_preview_with_comp_callback(callbacks.on_preview)
        caller.register_error_callback(callbacks.on_error)

        process = multiprocessing.Process(
            target=remover_process,
            args=(caller.queue, video_path, output_path, options)
        )
        try:
            self.running_process = process
            process.start()
            ProcessManager.instance().add_process(process)
            while process.is_alive():
                process.join(timeout=0.5)
            logger.info('subprocess_exit: exitcode=%d', process.exitcode)
        finally:
            caller.stop()
        return process

    def stop(self):
        """停止当前处理"""
        if self.running_process:
            ProcessManager.instance().terminate_by_process(self.running_process)
            self.running_process = None
