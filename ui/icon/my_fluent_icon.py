import os
from enum import Enum

from qfluentwidgets import getIconColor, Theme, FluentIconBase


class MyFluentIcon(FluentIconBase, Enum):
    Stop = "stop"
    PrevFrame = "prev_frame"
    NextFrame = "next_frame"
    SkipStart = "skip_start"
    SkipEnd = "skip_end"
    Speed1x = "speed_1x"
    Speed2x = "speed_2x"
    Speed4x = "speed_4x"

    def path(self, theme=Theme.AUTO):
        return os.path.join(os.path.abspath(os.path.dirname(__file__)), f'{self.value}_{getIconColor(theme)}.svg')
