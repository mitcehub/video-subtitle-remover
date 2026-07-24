"""硬件加速检测：CUDA / DirectML / MPS。"""

import logging
import threading
import importlib.util

import torch

logger = logging.getLogger(__name__)


class HardwareAccelerator:
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = HardwareAccelerator()
                cls._instance.initialize()
        return cls._instance

    def __init__(self):
        self.__cuda = False
        self.__dml = False
        self.__mps = False
        self.__enabled = True
        self.__device_cached = None

    def initialize(self):
        self._check_directml()
        self._check_cuda()
        self._check_mps()
        logger.info('hardware_init: name=%s, cuda=%s, dml=%s, mps=%s',
                 self.accelerator_name, self.__cuda, self.__dml, self.__mps)

    def _check_directml(self):
        self.__dml = bool(importlib.util.find_spec("torch_directml"))
        logger.info('directml: available=%s', self.__dml)

    def _check_cuda(self):
        self.__cuda = torch.cuda.is_available()
        if self.__cuda:
            logger.info('cuda: available=True, device=%s', torch.cuda.get_device_name(0))
        else:
            logger.info('cuda: available=False')

    def _check_mps(self):
        self.__mps = torch.backends.mps.is_available() and torch.backends.mps.is_built()
        logger.info('mps: available=%s', self.__mps)

    def has_accelerator(self):
        if not self.__enabled:
            return False
        return self.__cuda or self.__dml or self.__mps

    @property
    def accelerator_name(self):
        if not self.__enabled:
            return "CPU"
        if self.__cuda:
            return "GPU"
        if self.__dml:
            return "DirectML"
        if self.__mps:
            return "MPS"
        return "CPU"

    @property
    def device(self):
        if self.__device_cached is not None:
            return self.__device_cached
        if self.__enabled:
            if self.__cuda:
                self.__device_cached = torch.device("cuda:0")
                return torch.device("cuda:0")
            if self.__dml:
                try:
                    import torch_directml
                    dev = torch_directml.device(torch_directml.default_device())
                    self.__device_cached = dev
                    return dev
                except Exception:
                    logger.exception('directml_device_failed')
            if self.__mps:
                self.__device_cached = torch.device("mps")
                return torch.device("mps")
        self.__device_cached = torch.device("cpu")
        return torch.device("cpu")

    def has_cuda(self):
        return self.__enabled and self.__cuda

    def has_mps(self):
        return self.__enabled and self.__mps

    def set_enabled(self, enable):
        old = self.__enabled
        self.__enabled = enable
        self.__device_cached = None
        if old != enable:
            logger.info('hardware_toggle: %s -> %s', old, enable)

    def get_available_vram_mb(self):
        if not self.__enabled:
            return 0
        if self.__cuda:
            try:
                return torch.cuda.mem_get_info()[0] / (1024 * 1024)
            except Exception:
                return 0
        if self.__mps:
            try:
                import subprocess
                result = subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True, timeout=5)
                return int(result.stdout.strip()) / (1024 * 1024) * 0.5
            except Exception:
                return 0
        return 0
