# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ------------------------------------------------------------------------------
# 1. CRITICAL PATH FIX
# Ensure current working directory is in sys.path for local package imports
# ------------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath("."))

try:
    from aegis.redaction import MODEL_PREFS
except ImportError:
    MODEL_PREFS = {}

block_cipher = None

# ------------------------------------------------------------------------------
# 2. DATA FILES & HIDDEN IMPORTS COLLECTION
# ------------------------------------------------------------------------------
datas = [
    ('aegis', 'aegis'),
]

# Collect Presidio data files without invalid keyword arguments
datas += collect_data_files('presidio_analyzer')
datas += collect_data_files('presidio_anonymizer')

hiddenimports = [
    'aegis',
    'aegis.beta_guard',
    'aegis.redaction',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'presidio_analyzer',
    'presidio_anonymizer',
    'spacy',
]
hiddenimports += collect_submodules('aegis')

# ------------------------------------------------------------------------------
# 3. PYINSTALLER ANALYSIS
# ------------------------------------------------------------------------------
a = Analysis(
    ['app.py'],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'notebook'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)

# ------------------------------------------------------------------------------
# 4. EXE CONFIGURATION
# target_arch set explicitly to 'x86_64' to prevent invoking Apple's lipo tool
# ------------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Aegis Privacy Airlock',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Aegis Privacy Airlock',
)

app = BUNDLE(
    coll,
    name='Aegis Privacy Airlock.app',
    icon=None,
    bundle_identifier='com.aegis.privacyairlock',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'LSBackgroundOnly': 'False',
        'CFBundleShortVersionString': '0.13.3',
        'CFBundleVersion': '0.13.3',
    },
)