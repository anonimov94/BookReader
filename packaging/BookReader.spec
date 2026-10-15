# PyInstaller spec — folder build (onedir): fast start, then compressed by build.ps1.
# Build:  packaging\build.ps1
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = []
datas += collect_data_files("piper")                 # espeak-ng-data for Piper voices
datas += collect_data_files("imageio_ffmpeg")        # bundled ffmpeg.exe for export
datas += collect_data_files("edge_tts")
datas += [(os.path.join(SPECPATH, "BookReader.png"), ".")]
# Run by the separate XTTS runtime Python, so it must ship as a .py source file.
datas += [(os.path.join(ROOT, "bookreader", "addons", "xtts_worker.py"), os.path.join("bookreader", "addons"))]

hiddenimports = []
hiddenimports += collect_submodules("num2words")      # language modules are imported dynamically
hiddenimports += collect_submodules("piper")
hiddenimports += collect_submodules("pedalboard")
hiddenimports += collect_submodules("bookreader")
hiddenimports += ["_miniaudio", "lxml.etree", "lxml._elementpath"]

excludes = [
    "torch", "torchaudio", "TTS", "transformers",      # XTTS is an optional, separate install
    "tkinter", "matplotlib", "pytest", "PyInstaller", "IPython",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtNetwork", "PySide6.QtPdf", "PySide6.QtSvg",
    "PySide6.QtOpenGL", "PySide6.QtDBus", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml",
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BookReader",
    icon=os.path.join(SPECPATH, "BookReader.ico"),
    console=False,
    upx=False,                 # UPX breaks Qt/onnxruntime DLLs; 7-Zip compresses the package instead
)
coll = COLLECT(exe, a.binaries, a.datas, name="BookReader", upx=False)
