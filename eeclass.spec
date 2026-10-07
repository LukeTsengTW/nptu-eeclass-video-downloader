# Windows x64 single-file build. Keep console pipes for internal worker modes;
# the bootloader hides its console when launched by double-clicking.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas, binaries, hiddenimports = collect_all('yt_dlp')
se_data, se_bins, se_hidden = collect_all('selenium')
def windows_manager_only(items):
    return [(source, destination) for source, destination in items
            if 'selenium-manager' not in Path(source).name or 'windows' in Path(source).parts]
datas += windows_manager_only(se_data)
binaries += windows_manager_only(se_bins)
hiddenimports += se_hidden
if not any(Path(source).name == 'selenium-manager.exe' for source, _ in datas + binaries):
    raise RuntimeError('Selenium Manager Windows executable was not collected')
datas += [(str(root / 'build' / 'third_party_licenses'), 'third_party_licenses')]
a = Analysis([str(root / 'eeclass_entry.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='eeClass-Downloader',
          debug=False, strip=False, upx=False, console=True, hide_console='hide-early',
          disable_windowed_traceback=False, uac_admin=False)
