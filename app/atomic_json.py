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
