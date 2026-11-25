# PyInstaller の設定。packaging\build.py から使う(直接なら: pyinstaller packaging\CryEffect.spec)
# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "web"), "web")]
datas += copy_metadata("pyworld")  # pyworld は import 時に自分の版数を pkg_resources で読む

hiddenimports = ["pkg_resources"]
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("cry_effect")

excludes = [
    "librosa", "numba", "llvmlite", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "_pytest",
    "pandas", "sklearn", "PIL", "pygments", "sphinx", "docutils", "httpx", "setuptools._vendor.wheel",
]

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CryEffect",
    console=False,
    icon=os.path.join(SPECPATH, "icon.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CryEffect", upx=False)
