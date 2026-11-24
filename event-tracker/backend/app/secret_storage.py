import base64
import ctypes
from ctypes import wintypes
from io import StringIO
import logging
import os
from pathlib import Path
import tempfile

from dotenv.main import resolve_variables
from dotenv.parser import parse_stream


_KEY_NAMES = {'QWEN_API_KEY', 'BOCHA_API_KEY', 'TAVILY_API_KEY', 'JINA_API_KEY'}
_PREFIX = 'dpapi:'
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        raise OSError('Saving encrypted API keys requires Windows DPAPI. Supply keys through the process environment on other systems.')

    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    transform = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    blob_pointer = ctypes.POINTER(_DataBlob)
    transform.argtypes = (
        blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, blob_pointer,
    )
    transform.restype = wintypes.BOOL
    local_free = ctypes.WinDLL('kernel32').LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.addressof(buffer))
    result = _DataBlob()
    if not transform(ctypes.byref(source), None, None, None, None,
                     _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        local_free(result.data)


def read_api_key(name: str) -> str:
    value = os.getenv(name, '')
    if not value.startswith(_PREFIX):
        return value
    try:
        encrypted = base64.b64decode(value.removeprefix(_PREFIX), validate=True)
        return _dpapi(encrypted, decrypt=True).decode('utf-8')
    except (OSError, ValueError):
        logging.getLogger(__name__).warning('Could not decrypt %s. Re-enter the key under the current Windows account.', name)
        return ''


def _encrypt(value: str) -> str:
    return _PREFIX + base64.b64encode(_dpapi(value.encode('utf-8'))).decode('ascii')


def _env_line(name: str, value: str | None) -> str:
    if value is None:
        return f'{name}\n'
    value = value.replace('\\', '\\\\').replace("'", "\\'")
    return f"{name}='{value}'\n"


def save_env(path: Path, updates: dict[str, str] | None = None) -> None:
    """Atomically update settings and migrate old keys before writing any bytes."""
    target = Path(path)
    try:
        contents = target.read_text(encoding='utf-8')
    except FileNotFoundError:
        contents = ''

    bindings = list(parse_stream(StringIO(contents)))
    pending = {
        name: _env_line(name, _encrypt(value) if name in _KEY_NAMES and value else value)
        for name, value in (updates or {}).items()
    }
    legacy_keys = {
        binding.key for binding in bindings
        if binding.key in _KEY_NAMES and binding.value and not binding.value.startswith(_PREFIX)
    }
    if legacy_keys:
        values = resolve_variables(((binding.key, binding.value) for binding in bindings if binding.key), override=False)
        for name in legacy_keys - pending.keys():
            value = values[name]
            pending[name] = _env_line(name, _encrypt(value) if value and not value.startswith(_PREFIX) else value)
    if not pending:
        return

    remaining = pending.copy()
    lines = []
    for binding in bindings:
        if binding.key in pending:
            lines.append(pending[binding.key])
            remaining.pop(binding.key, None)
        else:
            lines.append(binding.original.string)
    if remaining and lines and not lines[-1].endswith('\n'):
        lines.append('\n')
    lines.extend(remaining.values())

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.env-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.writelines(lines)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
