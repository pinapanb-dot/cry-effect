# Cry Effect(泣き声化ツール)

**日本語** | [English](README.en.md)

ボーカル WAV の指定区間に「泣きの身振り」(不規則な震え・語尾の崩れ・息・暗い響き など)を付加します。
出力は Suno の「カバー」入力向け。音程補正と全体のフォルマントは Melodyne で行い、このツールは
Melodyne でできない「新しい揺れや息の付加」を担当します。

## ダウンロード(Windows 10 / 11、64bit)

**[Releases](../../releases/latest)** から `CryEffect-<版数>-win64.zip` をダウンロードし、
展開した中の `CryEffect.exe` をダブルクリックします。Python などのインストールは不要です。

- 署名していないため、初回は「Windows によって PC が保護されました」と出ます。
  「詳細情報」→「実行」で起動できます。
- 使い方は zip に入っている `README_ja.txt`(English: `README_en.txt`)を見てください。
- 画面は日本語 / English を切り替えられます。

## ライセンス

[MIT License](LICENSE)。同梱しているライブラリのライセンスは、配布 zip の `THIRD_PARTY_LICENSES.txt` にあります
(pyworld / WORLD、numpy、scipy、soundfile / libsndfile、FastAPI、wavesurfer.js など)。

---

以下は開発者向けの説明です。

## セットアップ(Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

pyworld 0.3.5 は import 時に `pkg_resources` を使うため、`setuptools<81` を入れています。

## 配布版を作る

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe packaging\build.py
```

`dist\CryEffect\`(exe と同梱ファイル、約 150MB)と `dist\CryEffect-<版数>-win64.zip`(約 65MB)ができます。
利用者は zip を展開して `CryEffect.exe` をダブルクリックするだけで使えます(Python は不要)。

- ウィンドウ: Edge(なければ Chrome)のアプリモードで開きます。どちらもなければ既定のブラウザ。
  ウィンドウを閉じるとツールも終わります(画面が `/api/ping` を送り、閉じるときに `/api/bye` を送る)。
- 配布物には利用者向けの説明 `README_ja.txt` / `README_en.txt`(`packaging/` にある原稿)と、
  同梱ソフトのライセンス `THIRD_PARTY_LICENSES.txt`(ビルド時に自動で集める)が入ります。
- 置き場所: 素材は exe と同じフォルダの `materials\`、設定・キャッシュ・ログは `%LOCALAPPDATA%\CryEffect\`。
  (`CRY_DATA_DIR` / `CRY_CACHE_DIR` / `CRY_MATERIALS_DIR` で変更可)
- 版数は `cry_effect/__init__.py` の `__version__`。アイコンは `packaging/make_icon.py` で作り直せます。
- 署名していないので、初回起動時に Windows SmartScreen の確認が出ます(README_ja.txt に回避手順あり)。
- librosa は使っていないので配布物には入れていません(numba などで大きくなるため)。

## 使い方(画面版)

```powershell
.\.venv\Scripts\python.exe app.py              # アプリのウィンドウが開く(閉じると終わる)
.\.venv\Scripts\python.exe app.py vocal.wav    # 開いた状態で起動(プロジェクト JSON も可)
.\.venv\Scripts\python.exe app.py --browser    # 既定のブラウザで開く
.\.venv\Scripts\python.exe app.py --no-browser # サーバーだけ(開発用。閉じても終わらない)
```

右上のメニューで **日本語 / English** を切り替えられます(`%LOCALAPPDATA%\CryEffect\settings.json` に保存。
初回は Windows の表示言語に合わせます)。コマンドライン版の表示は日本語のみです。

`start.bat` をダブルクリック(または WAV をドラッグ&ドロップ)でも起動できます。

- **開く**: WAV かプロジェクト JSON。WAV と同じ場所に `<名前>.cry.json` があれば自動で読み込みます。
  初回は WORLD 分析(3 分で 15 秒前後)、2 回目以降はキャッシュから即座に開きます。
- **画面**: 上が波形 + F0 曲線(左に音名)。暗い部分が無音、下端の緑の帯が有声区間。その下に処理ごとのレーン。
- **区間**: レーンの上をドラッグで作成。ドラッグで移動、端で伸縮、クリックで選択して右側で強さ・パラメータを調整。
  パラメータは安全上限を超えて入力できません。
- **試聴**: 区間を変えると、その前後だけを再処理して(1 か所 0.1 秒程度)すぐに反映されます。
  `B` で処理あり / なしを同じ位置のまま切り替え、`L` で選択区間をループ。
- **保存**: `Ctrl+S` でプロジェクト(既定は `<名前>.cry.json`)。**WAV 書き出し** で処理済み WAV(元ファイルへは書き込めません)。

| キー | 動作 |
|---|---|
| Space | 再生 / 停止 |
| B | 処理あり / なしの切り替え |
| L | 選択区間のループ(区間の前後 0.15 秒を含む) |
| Delete | 選択区間を削除 |
| Esc | 選択解除 |
| Ctrl+ホイール / Shift+ホイール | 拡大・縮小 / 横スクロール |
| Ctrl+S | プロジェクト保存 |

## 使い方(コマンドライン版)

```powershell
.\.venv\Scripts\python.exe cli.py render examples\example_project.json
.\.venv\Scripts\python.exe cli.py render project.json -i vocal.wav -o vocal_cry.wav --cry 60
.\.venv\Scripts\python.exe cli.py analyze vocal.wav   # 分析だけ先に済ませる
.\.venv\Scripts\python.exe cli.py params              # 処理とパラメータの範囲
```

- 出力は 32bit float WAV。入力とサンプルレート・チャンネル数・サンプル数が同じです(DAW で伴奏とずれません)。
- 出力先を省略すると `<入力名>_cry.wav`。元ファイルへの上書きは拒否します。
- 区間の外は元の音声のサンプルそのまま。区間とその前後(クロスフェード 40ms + 余裕 20ms)だけを WORLD で再合成します。

## プロジェクト JSON

```json
{
  "source": "vocal.wav",
  "global_cry_amount": 60,
  "regions": [
    {"type": "tremor", "start": 12.34, "end": 14.10, "strength": 70,
     "params": {"depth_cents": 30, "rate_hz": 5, "irregularity": 0.6}},
    {"type": "sob_formant", "start": 30.00, "end": 45.50, "strength": 50,
     "params": {"warp": 0.94}}
  ]
}
```

- `source` は JSON からの相対パスでも可。
- `strength`(0〜100)× `global_cry_amount`(0〜100)が実際の強さ。
- 任意: `ramp_ms`(区間の端の立ち上げ/戻し、既定 50ms)、`id` / `seed`(乱数の種。無い場合は種類と開始時刻から決まる)、`enabled: false`。
- 上限を超える値は読み込み時に上限へ丸め、注意を表示します。

## 処理(フェーズ1 実装済み)

| type | 内容 | パラメータ(既定 / 範囲) |
|---|---|---|
| `tremor` | F0 に帯域制限ノイズの不規則な揺れ(正弦波は使わない) | depth_cents 30 / 0〜120、rate_hz 5 / 3〜10、irregularity 0.5 / 0〜1 |
| `phrase_end` | 区間の終わりへ向けて息を増やし音量を下げる。区間の後ろ ramp_ms で解放 | onset 0.5、breath 0.7 / 〜0.95、level_drop_db 15 / 〜40、f0_drop_cents -50 / -250〜0、rasp 0 / 0〜1 |
| `breathiness` | 非周期成分を増やす(高域ほど多く) | amount 0.3 / 0〜0.9 |
| `sob_formant` | 包絡を周波数方向に縮めてフォルマントを下げる。F0 は不変 | warp 0.94 / 0.78〜1.00 |

## 処理(フェーズ2 実装済み)

| type | 内容 | パラメータ(既定 / 範囲) |
|---|---|---|
| `sob_pulse` | 母音の途中に、短い音量の落ち込み + 息の増加を不規則に入れる | rate_hz 3.5 / 2〜8、depth_db 8 / 0〜24、irregularity 0.6 / 0〜1 |
| `voice_break`(マーカー) | マーカーの位置から上へ跳ねて戻る。その間は高域を弱め息を増やす | semitones 5 / 3〜10、duration_ms 100 / 60〜220、harmonic_cut 0.5 / 0〜1 |
| `articulation_blur` | 包絡を時間方向にならして子音の切れ目をぼかす。「あ」に寄せることもできる | smooth_ms 50 / 30〜120、toward_a 0 / 0〜0.8 |
| `sob_inhale` | 無音に吸気音を重ねる(区間の終わりで吸い終わる。時間はずらさない) | sample(素材名、空なら合成)、level_db -14 / -30〜0(声の代表音量に対して)、duration_ms 280 / 150〜600、rise 0.7 / 0〜1 |

- `voice_break` はマーカーで、JSON では `"time"` で位置を指定します。画面ではレーンをダブルクリックで追加します。無声の所に置くと効かないので注意を出します。
- `articulation_blur` は無声の子音にもかけます(子音の切れ目をぼかすため)。無音にはかけません。
- `sob_inhale` の素材は `materials/inhale/` に WAV を置くと画面で選べます(場所は環境変数 `CRY_MATERIALS_DIR` で変更可)。
  鳴っている声に 30ms 以上重なると、警告を出し区間を赤い破線で囲みます。
- 声の裏返りの「跳ねる幅」は強さ 100 のときの値です(強さ・泣き度に比例して小さくなります)。

## 処理(フェーズ3 実装済み)

| type | 内容 | パラメータ(既定 / 範囲) |
|---|---|---|
| `glottal_catch`(マーカー) | マーカーの位置から声を一瞬ほぼ無音まで落とす(手前 3ms で落ち、長さの後 10ms で戻る) | duration_ms 40 / 20〜100、depth_db 40 / 12〜80 |
| `f2_raise` | 900〜2500Hz の帯域だけ包絡を上へ伸ばす(1700Hz 付近を 1+amount 倍へ。帯域の外は動かさない) | amount 0.15 / 0〜0.45 |
| `nasal` | 1.2kHz から上を 3.5kHz にかけて下げ、300〜500Hz(中心 400Hz)を持ち上げる | high_cut_db 6 / 0〜18、low_boost_db 3 / 0〜9 |
| `sfx` | 無音に素材を重ねる(区間の始まりから鳴らし、終わりで切る) | sample(素材名、空なら合成の「鼻をすする音」)、level_db -16 / -36〜0、duration_ms 220 / 100〜600(合成音) |

- `glottal_catch` は音量だけを変える処理なので再合成しません。元の音(他の処理と重なる所は再合成した音)にサンプル単位でかけます。
  無声の所に置くとほとんど聞こえないので注意を出します。
- `sfx` の素材は `materials/sfx/` に WAV を置くと画面で選べます。`sob_inhale` と同じく、鳴っている声に 30ms 以上重なると警告します。
- 素材の音量は、素材の RMS を声の代表音量にそろえてから `level_db` をかけます(素材の前後に長い無音があると大きめになります)。

## 分析キャッシュ

開発時は `.cache/analysis/<音声のハッシュ>/`、配布版は `%LOCALAPPDATA%\CryEffect\cache\` に保存します
(3 分・48kHz で約 140MB)。場所は環境変数 `CRY_CACHE_DIR` で変更可。不要になったらフォルダごと消して構いません。
長い音源は 10 秒ごとに分けて並列に分析します(3 分で 15 秒前後)。

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

出力の長さ、NaN なし、ピーク上限、区間外が元のまま、境界にクリックがないこと(各処理の上限値で)、
パラメータの上限、キャッシュ、元ファイルを上書きしないこと、日本語 / 英語の表示、ウィンドウを閉じたときの終了、
などを確認します。

## 構成

```
cli.py                    コマンドライン
cry_effect/audio_io.py    WAV 入出力(元ファイル保護)
cry_effect/analysis.py    WORLD 分析・並列化・キャッシュ・有声/無音判定
cry_effect/dsp.py         立ち上げカーブ、帯域ノイズ、包絡の周波数伸縮(F2 上昇でも使う)、リミッタ
cry_effect/effects/       処理(base.py が共通部分、phase1〜3.py が各フェーズ)
cry_effect/materials.py   素材 WAV(materials/inhale, materials/sfx)の一覧と読み込み
cry_effect/render.py      区間ごとの再合成とクロスフェード
cry_effect/live.py        画面用の差分レンダリング(変わったスパンだけ再処理)
cry_effect/server.py      FastAPI サーバー(API・ファイルダイアログ)
cry_effect/project.py     JSON の読み書きと検証
cry_effect/i18n.py        表示言語(L("日本語", "English") / Msg)と設定ファイル
cry_effect/paths.py       置き場所(開発時 / 配布版)
app.py / start.bat        画面版の起動(アプリのウィンドウ・ログ・終了の見張り)
web/                      画面(app.js, player.js, i18n.js, style.css)
packaging/                配布版の作成(build.py, CryEffect.spec, アイコン, 利用者向け README)
web/vendor/               wavesurfer.js 7.12.12 + Regions プラグイン(同梱・オフライン動作)
```

## 今後

1. 区間候補の自動提案
