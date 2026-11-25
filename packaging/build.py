"""配布用の CryEffect フォルダと zip を作る。

    .venv\\Scripts\\python.exe packaging\\build.py

できるもの:
    dist\\CryEffect\\CryEffect.exe          起動する exe(ダブルクリック)
    dist\\CryEffect\\materials\\              素材フォルダ(利用者が WAV を置く)
    dist\\CryEffect\\README_ja.txt / README_en.txt / LICENSE.txt / THIRD_PARTY_LICENSES.txt
    dist\\CryEffect-<版数>-win64.zip        上のフォルダをまとめたもの
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from cry_effect import __version__  # noqa: E402

DIST = os.path.join(ROOT, "dist")
APP = os.path.join(DIST, "CryEffect")

# 配布物に入るパッケージ(ライセンス表記を集める対象)
BUNDLED = [
    "numpy", "scipy", "pyworld", "soundfile", "cffi", "pycparser", "fastapi", "starlette", "uvicorn",
    "pydantic", "pydantic_core", "annotated_types", "typing_extensions", "typing_inspection", "anyio",
    "sniffio", "idna", "click", "h11", "colorama", "setuptools",
]


def version_info() -> None:
    parts = [int(x) for x in __version__.split(".")] + [0] * 4
    v = tuple(parts[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Cry Effect (泣き声化ツール)'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'CryEffect'),
      StringStruct('OriginalFilename', 'CryEffect.exe'),
      StringStruct('ProductName', 'Cry Effect'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(os.path.join(HERE, "version_info.txt"), "w", encoding="utf-8") as f:
        f.write(text)


def third_party_licenses() -> str:
    import importlib.metadata as md

    out = ["Cry Effect bundles the following third-party software.\n"
           "Cry Effect には次のソフトウェアが含まれています。\n"]
    for name in BUNDLED:
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        meta = dist.metadata
        lic = meta.get("License-Expression") or meta.get("License") or ""
        if len(lic) > 200:
            lic = lic.splitlines()[0]
        out.append("=" * 78)
        out.append(f"{meta['Name']} {dist.version}  —  {lic}".rstrip(" —"))
        if meta.get("Home-page"):
            out.append(meta["Home-page"])
        texts = [f for f in (dist.files or []) if os.path.basename(str(f)).upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
        for f in texts:
            try:
                out.append("")
                with open(f.locate(), encoding="utf-8", errors="replace") as fh:
                    out.append(fh.read().strip())
            except (OSError, AttributeError):
                pass
        out.append("")
    # 同梱の JavaScript / ライブラリ
    out.append("=" * 78)
    out.append("wavesurfer.js 7.12.12 (+ Regions plugin)  —  BSD-3-Clause")
    with open(os.path.join(ROOT, "web", "vendor", "LICENSE-wavesurfer.txt"), encoding="utf-8") as f:
        out.append(f.read().strip())
    out.append("")
    out.append("=" * 78)
    out.append("WORLD vocoder (inside pyworld)  —  Modified BSD license, Copyright (c) Masanori Morise")
    out.append("https://github.com/mmorise/World")
    out.append("")
    out.append("=" * 78)
    out.append("libsndfile (inside soundfile)  —  LGPL-2.1-or-later, https://libsndfile.github.io/libsndfile/")
    out.append("It is loaded as a separate DLL (_internal/_soundfile_data) and can be replaced.")
    out.append("")
    out.append("=" * 78)
    out.append(f"Python {sys.version.split()[0]}  —  PSF License Agreement, https://docs.python.org/3/license.html")
    out.append("Tcl/Tk (tkinter)  —  BSD-style license, https://www.tcl.tk/software/tcltk/license.html")
    return "\n".join(out) + "\n"


def main() -> int:
    if not os.path.exists(os.path.join(HERE, "icon.ico")):
        subprocess.check_call([sys.executable, os.path.join(HERE, "make_icon.py")])
    version_info()
    shutil.rmtree(APP, ignore_errors=True)
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", os.path.join(ROOT, "build"),
        os.path.join(HERE, "CryEffect.spec"),
    ])

    # 素材フォルダ(説明だけ入れる。利用者が WAV を足す)
    for kind in ("inhale", "sfx"):
        d = os.path.join(APP, "materials", kind)
        os.makedirs(d, exist_ok=True)
        for f in glob.glob(os.path.join(ROOT, "materials", kind, "README*.txt")):
            shutil.copy2(f, d)
    for name in ("README_ja.txt", "README_en.txt"):
        shutil.copy2(os.path.join(HERE, name), APP)
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(APP, "LICENSE.txt"))
    with open(os.path.join(APP, "THIRD_PARTY_LICENSES.txt"), "w", encoding="utf-8") as f:
        f.write(third_party_licenses())

    zpath = os.path.join(DIST, f"CryEffect-{__version__}-win64.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _dirs, files in os.walk(APP):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, DIST))
    size = sum(os.path.getsize(os.path.join(b, f)) for b, _, fs in os.walk(APP) for f in fs)
    print(f"\nbuilt: {APP}  ({size / 1e6:.0f} MB)")
    print(f"zip:   {zpath}  ({os.path.getsize(zpath) / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
