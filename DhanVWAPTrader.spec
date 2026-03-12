# DhanVWAPTrader.spec
# PyInstaller spec for building the Windows EXE.
# Run from repo root: pyinstaller DhanVWAPTrader.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        *collect_data_files('customtkinter'),
    ],
    hiddenimports=[
        # GUI
        'customtkinter',
        'tkinter',
        'tkinter.ttk',
        'PIL',
        'PIL._tkinter_finder',
        # Strategy modules
        'candle_engine',
        'config',
        'dhan_api',
        'dhan_token_manager',
        'executors',
        'instrument_resolver',
        'logger_setup',
        'main',
        'market_feed',
        'state_store',
        'strategy_engine',
        'time_utils',
        # Third-party
        'websocket',
        'websocket._core',
        'websocket._abnf',
        'websocket._handshake',
        'websocket._http',
        'websocket._logging',
        'websocket._socket',
        'websocket._ssl_compat',
        'websocket._utils',
        'dotenv',
        'pyotp',
        'schedule',
        'pandas',
        'requests',
        'zoneinfo',
        'zoneinfo._tzpath',
    ],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DhanVWAPTrader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # no black console window behind the GUI
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets/icon.ico',  # uncomment if you add an icon
)
