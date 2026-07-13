"""进程管理器：管理和终止子进程。"""

import os
import platform
import threading
import logging
import atexit
import subprocess
import concurrent.futures

logger = logging.getLogger(__name__)


class ProcessManager:
    """单例进程管理器，线程安全地管理子进程生命周期。"""

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = ProcessManager()
        return cls._instance

    def __init__(self):
        self.processes = {}
        self._lock = threading.RLock()
        atexit.register(self.terminate_all)

    def add_process(self, process, name=None):
        """注册子进程到管理器。"""
        if process is None:
            return
        process_id = name or f"Process:{id(process)}"
        with self._lock:
            self.processes[process_id] = process
        logger.info('process_add: id=%s, pid=%s', process_id, getattr(process, 'pid', None))
        return process_id

    def remove_process(self, process_id):
        """从管理器移除指定进程记录。"""
        with self._lock:
            if process_id in self.processes:
                del self.processes[process_id]
                logger.info('process_remove: id=%s', process_id)
                return True
        return False

    def terminate_all(self):
        """终止所有已注册的子进程。"""
        with self._lock:
            items = list(self.processes.items())
            self.processes.clear()
        logger.info('process_terminate_all: count=%d', len(items))
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(self.terminate_by_process, proc) for _, proc in items]
            concurrent.futures.wait(futures)

    def terminate_by_process(self, process):
        """终止指定进程并从管理器移除。"""
        if process is None:
            return
        try:
            if hasattr(process, 'poll') and process.poll() is not None:
                return
            process.terminate()
            if hasattr(process, 'join'):
                try:
                    process.join(timeout=3)
                except Exception:
                    pass
            if hasattr(process, 'kill'):
                process.kill()
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
        except Exception as e:
            logger.warning('terminate_error: %s', e)
        if process.pid is not None:
            self.terminate_by_pid(process.pid)
        # 从管理器移除
        process_id = f"Process:{id(process)}"
        self.remove_process(process_id)

    def terminate_by_pid(self, pid):
        """通过 PID 终止进程。"""
        try:
            if platform.system() == 'Windows':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            else:
                subprocess.run(['kill', '-9', str(pid)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        except Exception as e:
            logger.warning('terminate_pid_error: pid=%s, %s', pid, e)
