"""泣き声化ツール コマンドライン版。

使い方:
    python cli.py render project.json [-i vocal.wav] [-o out.wav] [--cry 60]
    python cli.py analyze vocal.wav          # 分析だけ行ってキャッシュする
    python cli.py params                     # 処理とパラメータの範囲を表示
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _progress(label):
    last = [-1]

    def f(x):
        p = int(x * 100)
        if p // 10 != last[0] // 10:
            print(f"  {label}: {p}%", flush=True)
            last[0] = p
    return f


def cmd_analyze(args) -> int:
    from cry_effect.analysis import analyze
    from cry_effect.audio_io import load_wav

    audio = load_wav(args.input)
    t = time.time()
    a = analyze(audio.mono(), audio.fs, use_cache=not args.no_cache, progress=_progress("分析"))
    src = "キャッシュ" if a.meta.get("from_cache") else "新規"
    print(f"分析完了({src}, {time.time() - t:.1f} 秒): {a.n_frames} フレーム, キー {a.key}")
    voiced = a.segments(a.voiced)
    print(f"  有声区間 {len(voiced)} 個, 無音しきい値 {a.silence_threshold_db:.1f} dB")
    return 0


def cmd_render(args) -> int:
    from cry_effect.analysis import analyze
    from cry_effect.audio_io import default_output_path, load_wav, save_wav
    from cry_effect.project import load_project
    from cry_effect.render import render

    msgs = []
    project = load_project(args.project, warn=msgs.append)
    for m in msgs:
        print(f"[注意] {m}")

    src = args.input or project.get("source_path")
    if not src:
        print("入力 WAV がありません(-i で指定するか、JSON に source を書いてください)", file=sys.stderr)
        return 2
    if not os.path.exists(src):
        print(f"入力 WAV が見つかりません: {src}", file=sys.stderr)
        return 2
    if args.cry is not None:
        project["global_cry_amount"] = min(100.0, max(0.0, args.cry))

    out = args.output or default_output_path(src)

    audio = load_wav(src)
    print(f"入力: {src}({audio.fs} Hz, {audio.n_channels} ch, {audio.duration:.2f} 秒)")
    t = time.time()
    a = analyze(audio.mono(), audio.fs, use_cache=not args.no_cache, progress=_progress("分析"))
    print(f"分析: {'キャッシュ' if a.meta.get('from_cache') else '新規'}({time.time() - t:.1f} 秒)")

    t = time.time()
    res = render(audio.data, a, project, progress=_progress("処理"))
    print(f"処理: 区間 {len(project['regions'])} 個 → 再合成 {len(res.spans)} か所({time.time() - t:.1f} 秒)")
    for n in res.notes:
        print(f"[注意] {n}")

    save_wav(out, res.data if audio.n_channels > 1 else res.data, audio.fs, protect=[src])
    print(f"出力: {out}")
    return 0


def cmd_params(args) -> int:
    from cry_effect import effects

    if args.json:
        print(json.dumps(effects.specs_json(), ensure_ascii=False, indent=2))
        return 0
    for e in effects.specs_json():
        mark = "" if e["implemented"] else "(未実装)"
        print(f"{e['name']:18s} {e['label']} [{e['kind']}, フェーズ{e['phase']}]{mark}")
        for p in e["params"]:
            if p["kind"] == "choice":
                folder = f"materials/{p['choices_source']}" if p["choices_source"] else ""
                print(f"    {p['name']:16s} {p['label']}: {folder} の WAV ファイル名(空なら合成音)")
                continue
            u = f" {p['unit']}" if p["unit"] else ""
            print(f"    {p['name']:16s} {p['label']}: 既定 {p['default']}{u}, 範囲 {p['min']}〜{p['max']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="泣き声化ツール(コマンドライン版)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="プロジェクト JSON に従って処理済み WAV を書き出す")
    r.add_argument("project")
    r.add_argument("-i", "--input", help="入力 WAV(省略時は JSON の source)")
    r.add_argument("-o", "--output", help="出力 WAV(省略時は <入力名>_cry.wav)")
    r.add_argument("--cry", type=float, help="泣き度 0〜100(JSON の値を上書き)")
    r.add_argument("--no-cache", action="store_true", help="分析キャッシュを使わない")
    r.set_defaults(func=cmd_render)

    a = sub.add_parser("analyze", help="WORLD 分析だけ行ってキャッシュする")
    a.add_argument("input")
    a.add_argument("--no-cache", action="store_true")
    a.set_defaults(func=cmd_analyze)

    p = sub.add_parser("params", help="処理とパラメータの一覧")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_params)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
