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
        atexit.register(self.terminate_all)

    def add_process(self, process, name=None):
        if process is None:
            return
        process_id = name or f"Process:{id(process)}"
        self.processes[process_id] = process
        logger.info('process_add: id=%s, pid=%s', process_id, getattr(process, 'pid', None))
        return process_id

    def remove_process(self, process_id):
        if process_id in self.processes:
            del self.processes[process_id]
            logger.info('process_remove: id=%s', process_id)
            return True
        return False

    def terminate_all(self):
        logger.info('process_terminate_all: count=%d', len(self.processes))
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = []
            for _, process in list(self.processes.items()):
                futures.append(executor.submit(self.terminate_by_process, process))
            concurrent.futures.wait(futures)
        self.processes.clear()

    def terminate_by_process(self, process):
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

    def terminate_by_pid(self, pid):
        try:
            if platform.system() == 'Windows':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            else:
                subprocess.run(['kill', '-9', str(pid)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        except Exception as e:
            logger.warning('terminate_pid_error: pid=%s, %s', pid, e)
