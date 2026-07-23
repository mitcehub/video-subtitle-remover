"""配置管理：常量、枚举、Config 类、模型路径、翻译加载。"""

import os
import logging
import configparser
from enum import Enum, unique
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================================
# 常量
# ============================================================================

VERSION = "1.5"
BASE_DIR = str(Path(os.path.abspath(__file__)).parent.parent)
_STTN_DIR = os.path.join(BASE_DIR, 'core', 'inpaint', 'models', 'sttn')
_MODEL_PATH_FIXED = os.path.join(_STTN_DIR, 'sttn.pt')
if os.path.isfile(_MODEL_PATH_FIXED):
    MODEL_PATH = _MODEL_PATH_FIXED
else:
    _MODEL_EXTS = ('.pt', '.pth', '.ckpt', '.bin')
    MODEL_PATH = next(
        (os.path.join(_STTN_DIR, f) for f in os.listdir(_STTN_DIR)
         if os.path.splitext(f)[1].lower() in _MODEL_EXTS and not f.endswith('.bak')),
        _MODEL_PATH_FIXED  # fallback — will fail with clear error at load time
    )
    logger.warning('sttn_model_path_fallback: sttn.pt not found, using %s', MODEL_PATH)

os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

# ============================================================================
# 枚举
# ============================================================================

@unique
class InpaintMode(Enum):
    STTN_AUTO = "sttn-auto"

# ============================================================================
# Config 类
# ============================================================================

from qfluentwidgets import (qconfig, ConfigItem, QConfig, OptionsValidator, BoolValidator,
                            OptionsConfigItem, EnumSerializer, RangeValidator,
                            RangeConfigItem, ConfigValidator)


class Config(QConfig):
    interfaceTexts = {
        '简体中文': 'ch',
        '繁體中文': 'chinese_cht',
        'English': 'en',
        '한국어': 'ko',
        '日本語': 'japan',
        'Tiếng Việt': 'vi',
        'Español': 'es'
    }
    interface = OptionsConfigItem("Window", "Interface", "ChineseSimplified",
                                  OptionsValidator(interfaceTexts.values()), restart=True)

    windowX = ConfigItem("Window", "X", None)
    windowY = ConfigItem("Window", "Y", None)
    windowW = ConfigItem("Window", "Width", 1200)
    windowH = ConfigItem("Window", "Height", 900)

    subtitleSelectionAreas = ConfigItem("Main", "SubtitleSelectionAreas", "0.88,0.99,0.15,0.85")

    inpaintMode = OptionsConfigItem("Main", "InpaintMode", InpaintMode.STTN_AUTO,
                                    OptionsValidator(InpaintMode), EnumSerializer(InpaintMode))

    sttnNeighborStride = RangeConfigItem("Sttn", "NeighborStride", 5, RangeValidator(1, 100))
    sttnReferenceLength = RangeConfigItem("Sttn", "ReferenceLength", 10, RangeValidator(1, 100))
    sttnMaxLoadNum = RangeConfigItem("Sttn", "MaxLoadNum", 50, RangeValidator(1, 300))

    def get_sttn_max_load_num(self):
        return max(self.sttnMaxLoadNum.value, self.sttnNeighborStride.value * self.sttnReferenceLength.value)

    hardwareAcceleration = ConfigItem("Main", "HardwareAcceleration", True, BoolValidator())

    checkUpdateOnStartup = ConfigItem("Main", "CheckUpdateOnStartup", True, BoolValidator())

    saveDirectory = ConfigItem("Main", "SaveDirectory", "", ConfigValidator())


# ============================================================================
# 初始化
# ============================================================================

CONFIG_FILE = os.path.join(BASE_DIR, 'config', 'config.json')
config = Config()
qconfig.load(CONFIG_FILE, config)

tr = configparser.ConfigParser()
TRANSLATION_FILE = os.path.join(BASE_DIR, 'config', 'translations', f"{config.interface.value}.ini")

# Try to load the requested translation file
try:
    tr.read(TRANSLATION_FILE, encoding='utf-8')
    if not tr.sections():
        logger.warning('translation_file_empty_or_missing: %s', TRANSLATION_FILE)
        # Try to load ch.ini as fallback
        fallback_file = os.path.join(BASE_DIR, 'config', 'translations', 'ch.ini')
        tr_fallback = configparser.ConfigParser()
        tr_fallback.read(fallback_file, encoding='utf-8')
        if tr_fallback.sections():
            tr = tr_fallback
            logger.info('Loaded fallback translation from ch.ini')
        else:
            logger.warning('Fallback translation file ch.ini also empty or missing')
except Exception as e:
    logger.warning('Failed to load translation file %s: %s', TRANSLATION_FILE, e)
    # Try to load ch.ini as fallback
    fallback_file = os.path.join(BASE_DIR, 'config', 'translations', 'ch.ini')
    tr_fallback = configparser.ConfigParser()
    try:
        tr_fallback.read(fallback_file, encoding='utf-8')
        if tr_fallback.sections():
            tr = tr_fallback
            logger.info('Loaded fallback translation from ch.ini')
        else:
            logger.warning('Fallback translation file ch.ini also empty or missing')
    except Exception as e2:
        logger.warning('Failed to load fallback translation file ch.ini: %s', e2)
