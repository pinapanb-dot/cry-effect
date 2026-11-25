# Cry Effect 0.1.0

ボーカル WAV の指定区間に「泣き」の表現(震え・語尾の崩れ・息・しゃくり上げ・声の詰まり など)を付け足すツールです。
Suno のカバー入力向けの下ごしらえに使えます。元のファイルは上書きしません。

## ダウンロードと起動

1. 下の **Assets** から `CryEffect-0.1.0-win64.zip` をダウンロードします。
2. zip を右クリック →「すべて展開」します(zip の中から直接は起動できません)。
3. 展開したフォルダの `CryEffect.exe` をダブルクリックします。

- 対応: Windows 10 / 11(64bit)。Python などのインストールは不要です。
- 署名していないため、初回は「Windows によって PC が保護されました」と出ます。「詳細情報」→「実行」で起動できます。
- 使い方は同梱の `README_ja.txt` を見てください。画面右上で日本語 / English を切り替えられます。

## 主な機能

- 波形の上で区間を選び、12 種類の処理を重ねられます(不規則な震え、語尾の崩れ、息っぽさ、むせび泣き、嗚咽の脈動、
  声の裏返り、発音の崩れ、しゃくり上げ、声の詰まり、F2 の上昇、鼻にかかる声、効果音の重ね)。
- 編集内容はプロジェクト(JSON)として保存でき、書き出しは元と同じ長さの WAV です。
- 吸気音・効果音は `materials\` フォルダに自分の WAV を足して使えます。

---

## English

A tool that adds crying gestures (irregular tremor, broken phrase endings, breath, sob inhales, glottal catches, …)
to selected regions of a vocal WAV. The original file is never overwritten.

1. Download `CryEffect-0.1.0-win64.zip` from **Assets** below.
2. Right-click the zip → "Extract All".
3. Run `CryEffect.exe` in the extracted folder.

Windows 10 / 11 (64-bit). No Python required. Because the app is not code-signed, Windows SmartScreen may show
"Windows protected your PC" on first launch — click "More info" → "Run anyway".
See `README_en.txt` for usage. Switch the UI language from the top-right menu.

License: MIT. Bundled third-party licenses: `THIRD_PARTY_LICENSES.txt`.
