# -*- coding: utf-8 -*-
"""
@Author  : Fang Yao（原作者） / 改写：Jason Eric
@Time    : 2023/4/1 6:07 下午（原始时间）
@FileName: gui.py
@desc: 字幕去除器图形化界面（由 PySimpleGUI 改写为 PySide6）
"""

import sys
import os
import logging
import multiprocessing
from PyQt6.QtCore import Qt
from PyQt6 import QtCore, QtWidgets, QtGui
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from qfluentwidgets import (FluentWindow,
                          setTheme, Theme, setThemeColor, InfoBar, ToolButton, FluentIcon)

from qframelesswindow.utils import getSystemAccentColor
from infra.config import config, tr, VERSION
from ui.theme_listener import SystemThemeListener
from infra.process_manager import ProcessManager
from ui.advanced_setting_interface import AdvancedSettingInterface
from ui.home_interface import HomeInterface

# ============ 日志配置 ============
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'asr.log')

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
_fh.setLevel(logging.DEBUG)
_fmt = logging.Formatter('%(asctime)s [%(levelname)-5s] %(name)s: %(message)s', datefmt='%H:%M:%S')
_fh.setFormatter(_fmt)
_root_logger.addHandler(_fh)

_ch = logging.StreamHandler()
_ch.setLevel(logging.WARNING)
_ch.setFormatter(_fmt)
_root_logger.addHandler(_ch)

# 配置 scenedetect 日志（默认被静默清空，重定向到文件）
try:
    import pyscenedetect
    _scenedetect_logger = logging.getLogger('pyscenedetect')
    _scenedetect_logger.setLevel(logging.DEBUG)
    _scenedetect_logger.handlers.clear()
    _scenedetect_logger.addHandler(_fh)
except ImportError:
    pass

logging.getLogger('PIL').setLevel(logging.WARNING)
logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger('torch').setLevel(logging.WARNING)

logger = logging.getLogger('gui')

# 清理上次运行残留的临时文件（仅清理应用专属临时子目录，避免误删系统 TEMP）
APP_TEMP_DIR = os.path.join(LOG_DIR, 'temp')
if os.path.isdir(APP_TEMP_DIR):
    import shutil
    for fname in os.listdir(APP_TEMP_DIR):
        fpath = os.path.join(APP_TEMP_DIR, fname)
        try:
            if os.path.isfile(fpath):
                os.remove(fpath)
            elif os.path.isdir(fpath):
                shutil.rmtree(fpath, ignore_errors=True)
        except Exception:
            pass

logger.info('=' * 60)
logger.info('app_start: video subtitle remover v%s', '1.5')
logger.info('=' * 60)


class SubtitleExtractorGUI(FluentWindow): 
    def __init__(self):
        super().__init__()
        # 开启云母效果
        self.setMicaEffectEnabled(True)
        # 设置深色主题并跟随系统主题色
        setTheme(Theme.AUTO)
        setThemeColor(getSystemAccentColor(), save=True)

        # 初始化系统主题监听器并连接信号
        self.themeListener = SystemThemeListener(self)
        self.themeListener.start()

        # 设置窗口图标
        self.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "icon", "asr.ico")))
        # 窗口标题（自动同步到 titleLabel）
        self.setWindowTitle("Video Subtitle Remover")
        # 标题栏：紧凑高度 + logo 留边距
        self.titleBar.setFixedHeight(36)
        self.titleBar.hBoxLayout.setContentsMargins(12, 0, 0, 0)
        # 内容区顶部随标题栏高度
        self.widgetLayout.setContentsMargins(0, 36, 0, 0)
        # 创建界面布局
        self._create_layout()
        self._connectSignalToSlot()

    def _connectSignalToSlot(self):
        config.appRestartSig.connect(self._showRestartTooltip)

    def _showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            'Updated successfully',
            'Configuration takes effect after restart',
            duration=5000,
            parent=self
        )

    def _create_layout(self):
        # 移除 FluentWindow 默认的导航栏
        if self.navigationInterface:
            self.hBoxLayout.removeWidget(self.navigationInterface)
            self.navigationInterface.deleteLater()
            self.navigationInterface = None

        # 从旧的 widgetLayout 中移除 stackedWidget
        self.widgetLayout.removeWidget(self.stackedWidget)

        # ============ 窗口级别左侧导航面板（仅图标按钮） ============
        left_panel = QWidget()
        left_panel.setMinimumWidth(48)
        left_panel.setMaximumWidth(48)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 8, 0, 0)
        left_layout.setSpacing(8)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.home_btn = ToolButton(FluentIcon.HOME, self)
        self.settings_btn = ToolButton(FluentIcon.SETTING, self)
        left_layout.addWidget(self.home_btn)
        left_layout.addWidget(self.settings_btn)

        # 内容区：左面板 + stackedWidget
        self.widgetLayout.addWidget(left_panel)
        self.widgetLayout.addWidget(self.stackedWidget, 1)

        # 创建主页面和高级设置页面
        self.homeInterface = HomeInterface(self)
        self.homeInterface.setObjectName("HomeInterface")
        self.advancedSettingInterface = AdvancedSettingInterface(self)
        self.advancedSettingInterface.setObjectName("AdvancedSettingInterface")

        # 直接加入 stackedWidget
        self.stackedWidget.addWidget(self.homeInterface)
        self.stackedWidget.addWidget(self.advancedSettingInterface)
        self.stackedWidget.setCurrentWidget(self.homeInterface)

        # 连接按钮切换页面
        self.home_btn.clicked.connect(lambda: self.stackedWidget.setCurrentWidget(self.homeInterface))
        self.settings_btn.clicked.connect(lambda: self.stackedWidget.setCurrentWidget(self.advancedSettingInterface))

    def closeEvent(self, event):
        """程序关闭时保存窗口位置并清理资源"""
        self.save_window_position()
        # 停止主题监听线程
        try:
            if self.themeListener and self.themeListener.isRunning():
                self.themeListener.terminate()
                self.themeListener.wait(2000)
        except Exception:
            pass
        ProcessManager.instance().terminate_all()
        super().closeEvent(event)

    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()

    def save_window_position(self):
        """保存窗口位置到配置文件"""
        # 保存窗口位置和大小
        config.set(config.windowX, self.x())
        config.set(config.windowY, self.y())
        config.set(config.windowW, self.width())
        config.set(config.windowH, self.height())

    def load_window_position(self):
        # 尝试读取窗口位置
        try:
            x = config.windowX.value
            y = config.windowY.value
            width = config.windowW.value
            height = config.windowH.value

            if x is None or y is None:
                self.center_window()
                return

            # 确保窗口在屏幕内
            screen_rect = QtWidgets.QApplication.primaryScreen().availableGeometry()
            if (x >= 0 and y >= 0 and 
                x + width <= screen_rect.width() and 
                y + height <= screen_rect.height()):
                self.setGeometry(x, y, width, height)
            else:
                self.center_window()
        except Exception as e:
            logger.warning('window_position_load_failed: %s', e)
            self.center_window()
    
    def center_window(self):
        """将窗口居中显示"""
        screen_rect = QtWidgets.QApplication.primaryScreen().availableGeometry()
        window_rect = self.frameGeometry()
        center_point = screen_rect.center()
        window_rect.moveCenter(center_point)
        self.move(window_rect.topLeft())

    def keyPressEvent(self, event):
        """处理键盘事件"""
        # 检测Ctrl+C组合键
        if event.key() == QtCore.Qt.Key.Key_C and event.modifiers() == QtCore.Qt.KeyboardModifier.ControlModifier:
            logger.warning('user_interrupt: Ctrl+C')
            self.close()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 导航栏已移除，标题栏贴到窗口最左侧
        self.titleBar.move(0, 0)
        self.titleBar.resize(self.width(), self.titleBar.height())


if __name__ == '__main__':
    logger.info('qt_app_init')
    multiprocessing.set_start_method("spawn")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # 兼容 PyQt6：无 AA_EnableHighDpiScaling（PyQt6 默认启用高 DPI），仅设置不报错
    # 设置 Windows 任务栏 AppUserModelID，确保任务栏图标正确显示
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('com.asr.app')
    app = QtWidgets.QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    # 设置应用级图标，确保 Windows 任务栏显示正确
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "icon", "asr.ico")
    app.setWindowIcon(QtGui.QIcon(_icon_path))
    window = SubtitleExtractorGUI()
    # 先设置透明, 再显示, 否则会有闪烁的效果
    window.setWindowOpacity(0.0)
    window.show()
    window.load_window_position()
    # 使用动画效果逐渐显示窗口
    animation = QtCore.QPropertyAnimation(window, b"windowOpacity")
    animation.setDuration(300)  # 300毫秒的动画
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.start()
    app.exec()