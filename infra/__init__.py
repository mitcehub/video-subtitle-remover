"""基础设施层：配置、硬件、进程管理、工具函数。"""

from infra.config import config, tr, VERSION, BASE_DIR, MODEL_PATH
from infra.hardware import HardwareAccelerator
from infra.process_manager import ProcessManager
from infra.utils import VIDEO_EXTENSIONS, get_readable_path, is_frame_in_sections
