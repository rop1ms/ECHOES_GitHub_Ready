"""Atomic profile JSON with one last-good copy; reads never modify forensic data.

This protects against interrupted application writes, not a guarantee against
filesystem or device failure. It deliberately avoids synchronous fsync on each
UI settings change; closing the temporary file flushes Python's buffers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

_IO_LOCK=threading.RLock()


def backup_path(path):
    path=Path(path)
    return path.with_name(path.name+'.bak')


def _read(path,default,validator=None):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    # A wrong-shaped profile container is not a usable library/settings file.
    # Scalars retain the original generic helper behavior.
    expected=list if isinstance(default,list) else dict if isinstance(default,dict) else None
    if expected is not None and not isinstance(value,expected):
        raise ValueError('unexpected JSON container')
    if validator is not None and not validator(value):
        raise ValueError('unusable JSON records')
    return value


def load_json(path,default,validator=None):
    try:path=Path(path)
    except (TypeError,ValueError):return default
    # Windows readers opened through normal Python IO do not share DELETE.
    # Avoid replacing a file while another local profile reader holds it open.
    queued=pending(path)                                  # сохранено, но ещё пишется в фоне
    if queued is not None:
        try:
            value=json.loads(queued.decode('utf-8'))
            expected=list if isinstance(default,list) else dict if isinstance(default,dict) else None
            if expected is None or isinstance(value,expected):return value
        except ValueError:pass
    with _IO_LOCK:
        try:return _read(path,default,validator)
        except (OSError,ValueError,UnicodeError):pass
        try:
            value=_read(backup_path(path),default,validator)
            print('[data] recovered',path.name,'from last-good copy')
            return value
        except (OSError,ValueError,UnicodeError):return default


def _replace(path,payload):
    temporary=None
    try:
        fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
        temporary=Path(name)
        with os.fdopen(fd,'wb') as stream:stream.write(payload)
        os.replace(temporary,path)
        temporary=None
    finally:
        if temporary is not None:
            try:temporary.unlink()
            except FileNotFoundError:pass


def save_json(path,obj):
    path=Path(path)
    # Serialize and encode before touching either existing file. In particular,
    # an invalid object or unicode value must not truncate a healthy library.
    payload=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    with _IO_LOCK:
        _replace(path,payload)
        # Refresh the backup from the newly committed, known-valid payload, never
        # by copying a potentially corrupt preexisting target over the good copy.
        try:_replace(backup_path(path),payload)
        except OSError as error:print('[data] last-good copy could not be updated:',path.name,str(error))


# ── запись в фоне: поток интерфейса только сериализует (мс), диск — отдельный поток ──
# На медленном диске и с антивирусом, который проверяет каждый новый файл, синхронная запись настроек
# (0,5 МБ + копия) останавливала интерфейс на десятки и сотни мс — на каждом клике, ползунке, треке.
_PENDING={}
_COND=threading.Condition()
_WRITER=[None]
_BUSY=[0]


def _writer():
    while True:
        with _COND:
            while not _PENDING:
                _COND.wait()
            items=list(_PENDING.items());_PENDING.clear();_BUSY[0]+=1
        try:
            for key,payload in items:
                path=Path(key)
                try:
                    with _IO_LOCK:
                        _replace(path,payload)
                        try:_replace(backup_path(path),payload)
                        except OSError as error:print('[data] last-good copy could not be updated:',path.name,str(error))
                except OSError as error:
                    print('[data] write failed:',path.name,str(error))
        finally:
            with _COND:
                _BUSY[0]-=1;_COND.notify_all()


def save_json_async(path,obj):
    """Как save_json, но запись — в фоне; одна и та же запись подряд склеивается (пишется последняя)."""
    payload=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    with _COND:
        _PENDING[str(Path(path))]=payload
        if _WRITER[0] is None or not _WRITER[0].is_alive():
            _WRITER[0]=threading.Thread(target=_writer,daemon=True,name='json-writer');_WRITER[0].start()
        _COND.notify_all()


def pending(path):
    """Ещё не записанное содержимое файла (чтобы чтение сразу после сохранения видело новое)."""
    with _COND:
        return _PENDING.get(str(Path(path)))


def flush(timeout=10.0):
    """Дождаться записи всего, что в очереди (при выходе)."""
    import time
    end=time.monotonic()+timeout
    with _COND:
        while (_PENDING or _BUSY[0]) and time.monotonic()<end:
            _COND.wait(0.05)


import atexit as _atexit
_atexit.register(flush)
