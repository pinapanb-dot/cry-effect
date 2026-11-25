"""処理の一覧。適用はこの順(包絡 → 非周期 → F0/音量)で行う。"""

from __future__ import annotations

from .base import Effect, ParamSpec, RenderContext, SegmentState  # noqa: F401
from .phase1 import Breathiness, PhraseEnd, SobFormant, Tremor
from .phase2 import ArticulationBlur, SobInhale, SobPulse, VoiceBreak
from .phase3 import F2Raise, GlottalCatch, Nasal, Sfx


class _Planned(Effect):
    """仕様にあるが未実装の処理(読み込みは受け付け、処理は飛ばす)。"""

    implemented = False

    def __init__(self, name, label, kind, phase):
        self.name, self.label, self.kind, self.phase = name, label, kind, phase

    def apply(self, state, region, ctx):  # pragma: no cover
        return None


# 包絡(時間平滑化 → 周波数伸縮 → 周波数ごとの増減)→ 非周期成分・音量 → F0 の順にかける。
# 再合成しない処理(声の詰まり=音量だけ、無音に重ねる音)は再合成の後にかける。
_IMPLEMENTED: list[Effect] = [
    ArticulationBlur(), SobFormant(), F2Raise(), Nasal(), Breathiness(), SobPulse(), Tremor(), VoiceBreak(),
    PhraseEnd(), GlottalCatch(), SobInhale(), Sfx(),
]

# 仕様にあって未実装の処理(今はなし)
_PLANNED: list[Effect] = []

REGISTRY: dict[str, Effect] = {e.name: e for e in _IMPLEMENTED + _PLANNED}
ORDER: list[str] = [e.name for e in _IMPLEMENTED + _PLANNED]


def get(name: str) -> Effect | None:
    return REGISTRY.get(name)


def specs_json() -> list[dict]:
    return [REGISTRY[n].to_json() for n in ORDER]
