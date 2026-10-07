"""Build and stage a redistributable ZIP on Windows, without administrator rights."""
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows EXE on Windows (or use GitHub Actions).')
    notices = ROOT / 'build' / 'third_party_licenses'
    if notices.exists():
        shutil.rmtree(notices)
    notices.mkdir(parents=True)
    inventory = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        inventory.append({'name': name, 'version': dist.version,
                          'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                          'home_page': dist.metadata.get('Home-page')})
        folder = notices / re.sub(r'[^A-Za-z0-9_.-]', '_', name)
        for relative in dist.files or []:
            if Path(relative).name.lower().startswith(('license', 'licence', 'copying', 'notice')):
                source = Path(dist.locate_file(relative))
                if source.is_file():
                    folder.mkdir(exist_ok=True)
                    target = folder / str(relative).replace('/', '__').replace('\\', '__')
                    shutil.copyfile(source, target)
    for name in ('LICENSE.txt', 'LICENSE'):
        source = Path(sys.base_prefix) / name
        if source.is_file():
            shutil.copyfile(source, notices / 'Python-LICENSE.txt')
            break
    else:
        raise RuntimeError('Python distribution license is missing')
    (notices / 'build-packages.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', 'eeclass.spec'], cwd=ROOT, check=True)
    package = ROOT / 'dist' / 'eeClass-Windows-x64'
    package.mkdir(parents=True, exist_ok=True)
    exe = ROOT / 'dist' / 'eeClass-Downloader.exe'
    shutil.copyfile(exe, package / exe.name)
    shutil.copyfile(ROOT / 'LICENSE', package / 'LICENSE.txt')
    shutil.copyfile(ROOT / 'Windows快速開始.txt', package / 'Windows快速開始.txt')
    shutil.copyfile(ROOT / '使用說明.txt', package / '使用說明.txt')
    shutil.copytree(notices, package / 'third_party_licenses', dirs_exist_ok=True)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (package / 'SHA256SUMS.txt').write_text(f'{digest}  {exe.name}\n', encoding='ascii')
    (package / 'BUILD.json').write_text(json.dumps({'python': sys.version, 'packages': inventory}, indent=2), encoding='utf-8')
    print('Staged', package)


if __name__ == '__main__':
    build()
