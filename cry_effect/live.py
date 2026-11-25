"""画面用の差分レンダリング。

区間を編集するたびに全体を処理し直すのではなく、変わったスパン(再合成範囲)だけを処理する。
スパンは「範囲 + 中に含まれる区間の内容 + 泣き度 + 乱数の種」で識別し、
前回と同じスパンはそのまま、消えたスパンは元音声に戻し、新しいスパンだけを再合成する。
"""

from __future__ import annotations

import json
import threading

import numpy as np

from .analysis import Analysis
from .render import active_regions, make_context, plan_spans, render_span


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


class LiveRenderer:
    def __init__(self, data: np.ndarray, analysis: Analysis, mono: np.ndarray | None = None):
        self.data = np.asarray(data, dtype=np.float64)
        if self.data.ndim == 1:
            self.data = self.data[:, None]
        self.mono = self.data.mean(axis=1) if mono is None else mono
        self.analysis = analysis
        self.out = self.data.copy()
        self.spans: dict[str, tuple[int, int]] = {}
        self.version = 0
        self.lock = threading.RLock()

    @staticmethod
    def _signature(span, project: dict) -> str:
        regs = sorted(
            (json.dumps(r, sort_keys=True, ensure_ascii=False) for r in span.regions),
        )
        return json.dumps({
            "t": [round(span.t0, 5), round(span.t1, 5)],
            "g": project.get("global_cry_amount", 100.0),
            "seed": project.get("seed", 0),
            "r": regs,
        }, sort_keys=True)

    def update(self, project: dict) -> dict:
        """project(正規化済み)に合わせて出力を更新し、変わったサンプル範囲を返す。"""
        with self.lock:
            fs = self.analysis.fs
            ctx = make_context(self.analysis, project)
            regions = active_regions(project, ctx)
            plan = plan_spans(regions, ctx, self.data.shape[0] / fs)
            new = {self._signature(sp, project): sp for sp in plan}

            removed = [rng for sig, rng in self.spans.items() if sig not in new]
            added = [(sig, sp) for sig, sp in new.items() if sig not in self.spans]

            for s0, s1 in removed:
                self.out[s0:s1] = self.data[s0:s1]
            notes: list = []
            new_spans = {sig: rng for sig, rng in self.spans.items() if sig in new}
            changed = list(removed)
            for sig, sp in added:
                s0, s1, block = render_span(self.data, self.mono, self.analysis, ctx, sp, notes)
                self.out[s0:s1] = block
                new_spans[sig] = (s0, s1)
                changed.append((s0, s1))
            self.spans = new_spans
            self.version += 1
            return {
                "version": self.version,
                "changed": _merge_ranges(changed),
                "rendered": len(added),
                "kept": len(new) - len(added),
                "notes": notes,
            }

    def patch(self, s0: int, s1: int) -> np.ndarray:
        """out[s0:s1] をチャンネルごとに並べた float32(planar)で返す。"""
        with self.lock:
            s0 = max(0, int(s0))
            s1 = min(self.out.shape[0], int(s1))
            return np.ascontiguousarray(self.out[s0:s1].T, dtype=np.float32)
