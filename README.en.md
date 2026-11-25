# Cry Effect

[日本語](README.md) | **English**

Cry Effect adds crying gestures to chosen regions of a vocal WAV: irregular tremor, broken phrase endings, breath, a darker tone and more.
The output is meant as input for Suno's "Cover" feature. Pitch correction and overall formants are left to Melodyne;
this tool adds the new wobbles and breaths that Melodyne cannot create. The original file is never overwritten.

## Download (Windows 10 / 11, 64-bit)

Download `CryEffect-<version>-win64.zip` from **[Releases](../../releases/latest)**, extract it,
and double-click `CryEffect.exe` inside. You don't need to install Python or anything else.

- The app is not code-signed, so on first launch Windows shows "Windows protected your PC".
  Click "More info" → "Run anyway" to start it.
- For usage, see `README_en.txt` in the zip (Japanese: `README_ja.txt`).
- Switch the UI between Japanese and English from the top-right menu.

## License

[MIT License](LICENSE). The zip's `THIRD_PARTY_LICENSES.txt` lists the licenses of the bundled libraries
(pyworld / WORLD, numpy, scipy, soundfile / libsndfile, FastAPI, wavesurfer.js and others).

---

The rest of this page is for developers.

## Setup (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

pyworld 0.3.5 uses `pkg_resources` at import time, so `setuptools<81` is pinned.

## Building the distributable

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe packaging\build.py
```

This creates `dist\CryEffect\` (the exe plus bundled files, about 150 MB) and `dist\CryEffect-<version>-win64.zip` (about 65 MB).
Users only need to extract the zip and double-click `CryEffect.exe`. Python is not required.

- **Window:** the UI opens in Edge's app mode, or Chrome's if Edge is missing. If neither is installed, it opens in the default browser.
  Closing the window also quits the tool: the page sends `/api/ping` while it is open and `/api/bye` when it closes.
- **Bundled documents:** the user guides `README_ja.txt` and `README_en.txt` (sources in `packaging/`) and
  `THIRD_PARTY_LICENSES.txt`, which is collected automatically at build time.
- **Locations:** materials live in `materials\` next to the exe. Settings, cache and logs go to `%LOCALAPPDATA%\CryEffect\`.
  You can override them with `CRY_DATA_DIR`, `CRY_CACHE_DIR` and `CRY_MATERIALS_DIR`.
- **Version and icon:** the version is `__version__` in `cry_effect/__init__.py`. Rebuild the icon with `packaging/make_icon.py`.
- **SmartScreen:** the build is unsigned, so Windows SmartScreen asks for confirmation on first launch. The workaround is described in README_en.txt.
- **librosa:** it is not used, so it is left out of the build. With it, numba and its other dependencies would make the build much larger.

## Usage (GUI)

```powershell
.\.venv\Scripts\python.exe app.py              # opens the app window (quits when closed)
.\.venv\Scripts\python.exe app.py vocal.wav    # starts with a file open (a project JSON also works)
.\.venv\Scripts\python.exe app.py --browser    # opens in the default browser
.\.venv\Scripts\python.exe app.py --no-browser # server only (for development; does not quit on close)
```

Switch between **日本語 / English** from the top-right menu. The choice is saved in `%LOCALAPPDATA%\CryEffect\settings.json`.
On first launch the language follows the Windows display language. The command-line version is Japanese only.

You can also start the GUI by double-clicking `start.bat`, or by dragging a WAV onto it.

- **Open:** choose a WAV or a project JSON. If `<name>.cry.json` sits next to the WAV, it is loaded automatically.
  The first time, a file goes through WORLD analysis, which takes about 15 s for 3 minutes of audio. After that it opens instantly from the cache.
- **Screen:** the top shows the waveform and the F0 curve, with note names on the left. Dark areas are silence, and the green strip at the bottom marks voiced parts.
  Below that is one lane per effect.
- **Regions:**
  - Drag on a lane to create a region.
  - Drag a region to move it, or drag its edges to resize it.
  - Click a region to select it, then adjust its strength and parameters on the right.
  - Parameters cannot be set beyond their safety limits.
- **Preview:** when you change a region, only the audio around it is re-processed (about 0.1 s per region) and the change is heard right away.
  `B` toggles between processed and original at the same position. `L` loops the selected region.
- **Save:** `Ctrl+S` saves the project (default `<name>.cry.json`). **Export WAV** writes the processed WAV. The tool refuses to write over the original file.

| Key | Action |
|---|---|
| Space | Play / stop |
| B | Toggle processed / original |
| L | Loop the selected region (including 0.15 s before and after) |
| Delete | Delete the selected region |
| Esc | Clear the selection |
| Ctrl+wheel / Shift+wheel | Zoom / scroll horizontally |
| Ctrl+S | Save the project |

## Usage (command line)

```powershell
.\.venv\Scripts\python.exe cli.py render examples\example_project.json
.\.venv\Scripts\python.exe cli.py render project.json -i vocal.wav -o vocal_cry.wav --cry 60
.\.venv\Scripts\python.exe cli.py analyze vocal.wav   # run only the analysis, ahead of time
.\.venv\Scripts\python.exe cli.py params              # list effects and parameter ranges
```

- **Format:** the output is a 32-bit float WAV with the same sample rate, channel count and sample count as the input, so it stays in sync with the backing track in a DAW.
- **Output name:** if you omit the output path, it is `<input name>_cry.wav`. The tool refuses to overwrite the original file.
- **What changes:** audio outside the regions is the original samples, untouched. Only the regions and their surroundings (40 ms crossfade plus 20 ms margin) are resynthesized with WORLD.

## Project JSON

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

- `source` may be a path relative to the JSON file.
- The effective strength is `strength` (0–100) × `global_cry_amount` (0–100).
- Optional fields:
  - `ramp_ms`: fade-in / fade-out at the region edges (default 50 ms).
  - `id` / `seed`: the random seed. If omitted, it is derived from the type and the start time.
  - `enabled: false`: turns the region off.
- Values above a limit are clamped to the limit on load, with a notice.

## Effects (phase 1)

| type | What it does | Parameters (default / range) |
|---|---|---|
| `tremor` | Irregular F0 wobble from band-limited noise (no sine vibrato) | depth_cents 30 / 0–120, rate_hz 5 / 3–10, irregularity 0.5 / 0–1 |
| `phrase_end` | Adds breath and lowers the level toward the region end, released over ramp_ms after it | onset 0.5, breath 0.7 / –0.95, level_drop_db 15 / –40, f0_drop_cents -50 / -250–0, rasp 0 / 0–1 |
| `breathiness` | Adds aperiodic (breath) components, more in the highs | amount 0.3 / 0–0.9 |
| `sob_formant` | Compresses the envelope in frequency to lower the formants. F0 is unchanged | warp 0.94 / 0.78–1.00 |

## Effects (phase 2)

| type | What it does | Parameters (default / range) |
|---|---|---|
| `sob_pulse` | Irregular short dips in level, with extra breath, inside vowels | rate_hz 3.5 / 2–8, depth_db 8 / 0–24, irregularity 0.6 / 0–1 |
| `voice_break` (marker) | Jumps up from the marker and back. Highs are reduced and breath is added meanwhile | semitones 5 / 3–10, duration_ms 100 / 60–220, harmonic_cut 0.5 / 0–1 |
| `articulation_blur` | Smooths the envelope over time to blur consonant boundaries. Can also pull vowels toward "a" | smooth_ms 50 / 30–120, toward_a 0 / 0–0.8 |
| `sob_inhale` | Adds an inhale in silence, ending at the region end (no time shift) | sample (material name, empty = synthesized), level_db -14 / -30–0 (relative to the voice level), duration_ms 280 / 150–600, rise 0.7 / 0–1 |

- **`voice_break`:** it is a marker, placed with `"time"` in JSON. In the GUI, double-click a lane to add one.
  Placing it on an unvoiced part has no effect, so the tool shows a notice.
- **`articulation_blur`:** it also applies to unvoiced consonants, to blur their boundaries, but not to silence.
- **`sob_inhale`:**
  - Put WAV files in `materials/inhale/` to choose them in the GUI. Override the location with `CRY_MATERIALS_DIR`.
  - If the inhale overlaps the voice by 30 ms or more, the tool warns and outlines the region with a red dashed line.
- **Voice-break jump:** `semitones` is the jump at strength 100. It shrinks in proportion to strength and cry amount.

## Effects (phase 3)

| type | What it does | Parameters (default / range) |
|---|---|---|
| `glottal_catch` (marker) | Drops the voice to near silence at the marker (falls 3 ms before it and recovers 10 ms after the duration) | duration_ms 40 / 20–100, depth_db 40 / 12–80 |
| `f2_raise` | Stretches the envelope upward in the 900–2500 Hz band only (about 1700 Hz moves to 1+amount times; nothing outside the band moves) | amount 0.15 / 0–0.45 |
| `nasal` | Cuts from 1.2 kHz, reaching the full cut by 3.5 kHz, and lifts 300–500 Hz (centered on 400 Hz) | high_cut_db 6 / 0–18, low_boost_db 3 / 0–9 |
| `sfx` | Adds a material sound in silence (starts at the region start, cut at its end) | sample (material name, empty = synthesized sniff), level_db -16 / -36–0, duration_ms 220 / 100–600 (synthesized sound) |

- **`glottal_catch`:**
  - It only changes the level, so it does no resynthesis of its own.
  - It is applied sample by sample to the original audio, or to the resynthesized audio where it overlaps other effects.
  - On an unvoiced part it is barely audible, so the tool shows a notice.
- **`sfx`:** put WAV files in `materials/sfx/` to choose them in the GUI. As with `sob_inhale`, the tool warns if the sound overlaps the voice by 30 ms or more.
- **Material level:** a material's RMS is matched to the voice level first, then `level_db` is applied. Long silence at the start or end of a material makes it louder.

## Analysis cache

During development the analysis is stored in `.cache/analysis/<audio hash>/`. The distributable stores it in `%LOCALAPPDATA%\CryEffect\cache\`.
It takes about 140 MB for 3 minutes at 48 kHz. Override the location with `CRY_CACHE_DIR`. The folder can be deleted at any time.
Long audio is split into 10 s chunks that are analyzed in parallel, taking about 15 s for 3 minutes.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The tests check:

- output length, no NaN, the peak limit, and untouched audio outside the regions;
- no clicks at the boundaries, with each effect at its maximum settings;
- parameter limits and the cache;
- that the original file is never overwritten;
- the Japanese / English UI text;
- that the tool quits when the window closes.

## Layout

```
cli.py                    command line
cry_effect/audio_io.py    WAV I/O (protects the original file)
cry_effect/analysis.py    WORLD analysis, parallelism, cache, voiced/silence detection
cry_effect/dsp.py         ramp curves, band-limited noise, envelope frequency warping (also used by F2 raise), limiter
cry_effect/effects/       effects (base.py holds the shared parts, phase1–3.py the phases)
cry_effect/materials.py   listing and loading material WAVs (materials/inhale, materials/sfx)
cry_effect/render.py      per-region resynthesis and crossfades
cry_effect/live.py        incremental rendering for the GUI (re-processes only changed spans)
cry_effect/server.py      FastAPI server (API, file dialogs)
cry_effect/project.py     JSON reading, writing and validation
cry_effect/i18n.py        UI language (L("日本語", "English") / Msg) and the settings file
cry_effect/paths.py       file locations (development / distributable)
app.py / start.bat        GUI launcher (app window, logging, quit watchdog)
web/                      UI (app.js, player.js, i18n.js, style.css)
packaging/                building the distributable (build.py, CryEffect.spec, icon, user READMEs)
web/vendor/               wavesurfer.js 7.12.12 + Regions plugin (bundled, works offline)
```

## Roadmap

1. Automatic suggestions for candidate regions
