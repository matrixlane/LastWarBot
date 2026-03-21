from pathlib import Path

from PyInstaller.utils.hooks import collect_all

project_root = Path.cwd()

datas = []
binaries = []
hiddenimports = []

for package_name in [
    "paddle",
    "paddleocr",
    "cv2",
    "numpy",
    "PIL",
    "mss",
    "pyautogui",
    "keyboard",
    "psutil",
    "yaml",
    "pyclipper",
    "shapely",
    "Cython",
]:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

block_cipher = None


a = Analysis(
    [str(project_root / "run_lastwar_bot.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LastWarBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LastWarBot",
)
