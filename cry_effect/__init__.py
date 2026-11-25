"""泣き声化ツール(試作)。

ボーカル WAV の指定区間だけを WORLD で再合成し、泣きの特徴を付加する。
"""

import warnings as _warnings

# pyworld 0.3.5 が import 時に出す pkg_resources の非推奨警告を抑える
_warnings.filterwarnings("ignore", message=".*pkg_resources.*")

__version__ = "0.1.0"
