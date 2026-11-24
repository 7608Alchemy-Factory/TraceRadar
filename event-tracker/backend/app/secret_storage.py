import base64
import ctypes
from ctypes import wintypes
from io import StringIO
import logging
import os
from pathlib import Path
import tempfile
from threading import Lock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv.main import resolve_variables
from dotenv.parser import parse_stream


_KEY_NAMES = {'QWEN_API_KEY', 'BOCHA_API_KEY', 'TAVILY_API_KEY', 'JINA_API_KEY'}
_PREFIX = 'aes256gcm:'
_LEGACY_PREFIX = 'dpapi:'
_MASTER_KEY_ENV = 'TRACERADAR_SECRETS_KEY'
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_WRITE_LOCK = Lock()


class SecretStorageError(ValueError):
    """A local key could not be saved securely."""


class _DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        raise OSError('Windows DPAPI is unavailable on this system.')

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


def _decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode('ascii')


def _master_key() -> bytes:
    try:
        key = _decode(os.getenv(_MASTER_KEY_ENV, ''))
        if len(key) == 32:
            return key
    except ValueError:
        pass
    raise SecretStorageError(f'Set {_MASTER_KEY_ENV} to a base64-encoded 32-byte key in the backend environment to save API keys.')


def _decrypt(name: str, value: str) -> str:
    if value.startswith(_LEGACY_PREFIX):
        return _dpapi(_decode(value.removeprefix(_LEGACY_PREFIX)), decrypt=True).decode('utf-8')
    if not value.startswith(_PREFIX):
        return value
    scheme, parts = value.removeprefix(_PREFIX).split(':', 1)
    if scheme == 'dpapi':
        envelope, parts = parts.split(':', 1)
        key = _dpapi(_decode(envelope), decrypt=True)
    elif scheme == 'env':
        key = _master_key()
    else:
        raise ValueError('Unsupported key protection scheme.')
    payload = _decode(parts)
    return AESGCM(key).decrypt(payload[:12], payload[12:], name.encode('ascii')).decode('utf-8')


def read_api_key(name: str) -> str:
    try:
        return _decrypt(name, os.getenv(name, ''))
    except (OSError, ValueError, InvalidTag):
        logging.getLogger(__name__).warning('Could not decrypt %s. Check local key storage or re-enter the API key.', name)
        return ''


def _encrypt(name: str, value: str) -> str:
    if os.name == 'nt':
        key = AESGCM.generate_key(bit_length=256)
        try:
            scheme = 'dpapi:' + _encode(_dpapi(key))
        except OSError as exc:
            raise SecretStorageError('Windows could not protect the local API key. Use your normal Windows account or provide the API key in the backend environment.') from exc
    else:
        key = _master_key()
        scheme = 'env'
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode('utf-8'), name.encode('ascii'))
    return f'{_PREFIX}{scheme}:{_encode(nonce + ciphertext)}'


def _env_line(name: str, value: str | None) -> str:
    if value is None:
        return f'{name}\n'
    value = value.replace('\\', '\\\\').replace("'", "\\'")
    return f"{name}='{value}'\n"


def save_env(path: Path, updates: dict[str, str] | None = None) -> None:
    """Atomically update settings and migrate old keys before writing any bytes."""
    with _WRITE_LOCK:
        _save_env(path, updates or {})


def _save_env(path: Path, updates: dict[str, str]) -> None:
    target = Path(path)
    try:
        contents = target.read_text(encoding='utf-8')
    except FileNotFoundError:
        contents = ''

    bindings = list(parse_stream(StringIO(contents)))
    pending = {
        name: _env_line(name, _encrypt(name, value) if name in _KEY_NAMES and value else value)
        for name, value in updates.items()
    }
    legacy_keys = {
        binding.key for binding in bindings
        if binding.key in _KEY_NAMES and binding.value and not binding.value.startswith((_PREFIX, _LEGACY_PREFIX))
    }
    if legacy_keys and (os.name == 'nt' or os.getenv(_MASTER_KEY_ENV)):
        values = resolve_variables(((binding.key, binding.value) for binding in bindings if binding.key), override=False)
        for name in legacy_keys - pending.keys():
            value = values[name]
            pending[name] = _env_line(name, _encrypt(name, value) if value and not value.startswith((_PREFIX, _LEGACY_PREFIX)) else value)
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
