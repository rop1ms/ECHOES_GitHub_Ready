"""Local ECHOES updates. Never touches the user's music/settings directory."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import uuid
import psutil

def installation(path):
    p=Path(path).resolve()
    return (p/'app/main.py').is_file() and (p/'python/pythonw.exe').is_file()

def discover():
    candidates=[]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\ECHOES') as k:
            candidates.append(Path(winreg.QueryValueEx(k,'InstallLocation')[0]))
    except OSError:pass
    candidates.append(Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Programs/ECHOES')
    return next((p for p in candidates if installation(p)),None)

def version(folder):
    try:return (Path(folder)/'app/version.txt').read_text('utf-8-sig').strip()[:80]
    except OSError:return 'не определена'

def running(root):
    root=Path(root).resolve()
    for p in psutil.process_iter(['exe','cmdline']):
        try:
            exe=p.info['exe']
            if exe and Path(exe).resolve().is_relative_to(root/'python'):return True
            if exe and Path(exe).resolve().is_relative_to(root/'app'/'montage_native'):return True
            if any(Path(a).resolve()==root/'app/main.py' for a in (p.info['cmdline'] or []) if a.endswith('main.py')):return True
        except (OSError,ValueError,psutil.Error):continue
    return False

def launch(root):
    root=Path(root).resolve()
    if not installation(root):raise ValueError('Выберите папку установленного ECHOES с app и python.')
    if running(root):raise ValueError('ECHOES уже запущен. Откройте его окно или значок в трее.')
    return subprocess.Popen([str(root/'python/pythonw.exe'),str(root/'app/main.py')],cwd=root/'app')

def files(folder):
    result=[]
    for p in sorted(Path(folder).rglob('*')):
        if p.is_symlink() or p.is_junction():raise ValueError('Сборка содержит ссылку на другую папку или файл.')
        if '__pycache__' in p.parts or p.suffix in ('.pyc','.pyo') or '.dll.old' in p.name:continue
        if p.is_file():result.append(p)
    return result

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').digest()

def update(source,target,progress=lambda n,t:None):
    source=Path(source).resolve();target=Path(target).resolve()
    if not installation(target):raise ValueError('Установленный плеер не найден.')
    src=source/'app';dst=target/'app'
    if src.is_symlink() or src.is_junction():raise ValueError('Папка сборки не должна быть ссылкой.')
    if not (src/'main.py').is_file():raise ValueError('В сборке отсутствует app/main.py. Выберите распакованную папку ECHOES_Setup.')
    if source==target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Сборка и установленный плеер должны находиться в отдельных папках.')
    if dst.is_symlink() or dst.is_junction():raise ValueError('Папка приложения не должна быть ссылкой.')
    if running(target):raise ValueError('Закройте ECHOES, включая значок в трее, и повторите обновление.')
    entries=files(src)
    total=sum(p.stat().st_size for p in entries)
    if shutil.disk_usage(target).free<total+32*1024*1024:raise ValueError('Недостаточно свободного места для безопасного обновления.')
    token=uuid.uuid4().hex
    stage=target/('.update-'+token);backup=target/('app-backup-'+token)
    stage.mkdir()
    committed=False
    try:
        for i,p in enumerate(entries):
            out=stage/p.relative_to(src);out.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,out)
            if digest(p)!=digest(out):raise OSError('Не прошла проверка копии: '+p.name)
            progress(int((i+1)*90/max(1,len(entries))),p.name)
        if running(target):raise ValueError('Плеер был запущен во время подготовки. Закройте его и повторите.')
        progress(94,'Сохраняю предыдущую версию')
        dst.rename(backup)
        try:stage.rename(dst)
        except BaseException:
            backup.rename(dst)
            raise
        committed=True
        progress(100,'Обновление установлено')
        return backup
    finally:
        # Only remove the unique staging directory created by this call.
        if not committed and stage.exists() and stage.parent==target and stage.name=='.update-'+token:
            shutil.rmtree(stage)
