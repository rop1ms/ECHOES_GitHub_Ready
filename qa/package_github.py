"""Create a new public source folder from an explicit release allowlist."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import argparse

ROOT = Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ROOT.parent/'ECHOES_GitHub_Ready')
DEST=parser.parse_args().output.resolve()
if DEST.exists():
    raise SystemExit('Destination already exists; choose a new destination instead of overwriting it.')
assert DEST != ROOT and DEST.parent.is_dir()

entries = []
root_names = ['README.md','README.ru.md','LICENSE','THIRD_PARTY_NOTICES.md','.gitignore','.gitattributes',
              'requirements.txt','requirements-dev.txt','Install ECHOES.bat','Update ECHOES.bat',
              'УСТАНОВИТЬ ECHOES.bat','ОБНОВИТЬ ECHOES.bat','ECHOES Launcher.exe']
entries.extend(ROOT / name for name in root_names)
entries.extend(p for p in (ROOT/'app').iterdir() if p.is_file() and
               (p.suffix.lower() in ('.py','.dll','.ico','.png') or p.name in ('requirements.txt','version.txt')))
for folder in ('app/assets','app/dsl_themes','app/sounds','app/discord_assets','app/montage_native',
               'setup','launcher','docs','tools/native','tools/montage'):
    entries.extend(p for p in (ROOT/folder).rglob('*') if p.is_file())
entries.append(ROOT/'tools/build_launcher.ps1')
entries.extend((ROOT/'qa').glob('test_*.py'))
entries.extend(ROOT/'qa'/name for name in ('ui_smoke.py','make_readme_screenshots.py','build_ui_symbols.py','package_github.py','benchmark_row_memory.py','profile_queue_navigation.py','probe_first_page_geometry.py','profile_net_first_open.py','probe_device_responsiveness.py','check_duel_performance_ui.py','check_radio_fire_ui.py','check_radio_studio_ui.py'))
entries=sorted(set(p for p in entries if not any(part in ('__pycache__','(null)','launcher-build') for part in p.parts)
                   and p.suffix.lower() not in ('.pyc','.pyo','.bak','.log','.db','.sqlite','.key','.pem')
                   and '.old' not in p.name))
secrets = re.compile(r'(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-proj-[A-Za-z0-9_-]{25,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,})')
personal_home = str(Path.home()).replace('\\', '/').casefold()
for p in entries:
    assert p.is_file() and not p.is_symlink() and not p.is_junction(), p.name
    assert p.stat().st_size < 100*1024*1024, p.name
    if p.suffix.lower() in ('.py','.ps1','.cpp','.h','.md','.json','.bat','.txt','.sh'):
        text=p.read_text('utf-8-sig')
        assert not secrets.search(text), 'Credential-like literal in '+str(p.relative_to(ROOT))
        assert personal_home not in re.sub(r'[/\\]+', '/', text).casefold(), 'Personal path in '+str(p.relative_to(ROOT))

DEST.mkdir()
manifest=[]
for source in entries:
    relative=source.relative_to(ROOT)
    target=DEST/relative;target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    assert digest==hashlib.sha256(source.read_bytes()).hexdigest()
    item={'path':relative.as_posix(),'bytes':target.stat().st_size,'sha256':digest}
    if target.suffix.lower() not in ('.png','.ico','.ttf','.dll','.exe'):
        item['sha256_lf']=hashlib.sha256(target.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    manifest.append(item)
report={'version':(ROOT/'app/version.txt').read_text().strip(),'files':len(manifest),
        'total_bytes':sum(x['bytes'] for x in manifest),'largest_file_bytes':max(x['bytes'] for x in manifest),
        'private_profile_included':False,'text_checksum':'sha256_lf normalizes CRLF to LF for Git checkouts','files_manifest':manifest}
(DEST/'docs/RELEASE_MANIFEST.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='files_manifest'}))
print(str(DEST))
