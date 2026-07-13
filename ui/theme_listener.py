from PyQt6 import QtCore

from qfluentwidgets import setTheme, qconfig, Theme
import darkdetect


class SystemThemeListener(QtCore.QThread):
    """ System theme listener """

    systemThemeChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)

    def run(self):
        darkdetect.listener(self._on_theme_changed)

    def _on_theme_changed(self, theme: str):
        theme = Theme.DARK if theme.lower() == "dark" else Theme.LIGHT
        if qconfig.themeMode.value != Theme.AUTO or theme == qconfig.theme:
            return
        # 通过信号将主题变更传递到主线程，避免跨线程操作 Qt 对象
        self.systemThemeChanged.emit()
        QtCore.QMetaObject.invokeMethod(
            self, "_apply_theme_on_main_thread",
            QtCore.Qt.ConnectionType.QueuedConnection,
            QtCore.Q_ARG(str, theme.value)
        )

    @QtCore.pyqtSlot(str)
    def _apply_theme_on_main_thread(self, theme_name: str):
        """在主线程中应用主题变更"""
        theme = Theme.DARK if theme_name == "dark" else Theme.LIGHT
        qconfig.theme = Theme.AUTO
        setTheme(theme)
        qconfig.themeChanged.emit(Theme.AUTO)
