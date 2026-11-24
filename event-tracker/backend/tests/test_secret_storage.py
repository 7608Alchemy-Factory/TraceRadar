import sys
from pathlib import Path

from dotenv import dotenv_values
import pytest

from app import secret_storage


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DPAPI')
def test_migration_encrypts_legacy_keys_before_replacing_the_file(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    keys = {'QWEN_API_KEY': 'fake-model-value', 'BOCHA_API_KEY': 'fake-search-value'}
    original = '# settings\nQWEN_MODEL=team-model\nTAVILY_API_KEY=old-value\nTAVILY_API_KEY=\n'
    original += ''.join(f'{name}={value}\n' for name, value in keys.items())
    path.write_text(original, encoding='utf-8')

    def interrupted_replace(temporary, target):
        assert target == path
        candidate = Path(temporary).read_text(encoding='utf-8')
        assert all(value not in candidate for value in keys.values())
        assert 'old-value' not in candidate
        raise OSError('simulated replace failure')

    with monkeypatch.context() as patch:
        patch.setattr(secret_storage.os, 'replace', interrupted_replace)
        with pytest.raises(OSError, match='simulated replace failure'):
            secret_storage.save_env(path)
    assert path.read_text(encoding='utf-8') == original
    assert list(tmp_path.iterdir()) == [path]

    secret_storage.save_env(path)
    values = dotenv_values(path)
    assert values['QWEN_MODEL'] == 'team-model'
    assert values['TAVILY_API_KEY'] == ''
    for name, value in keys.items():
        assert values[name].startswith('dpapi:')
        monkeypatch.setenv(name, values[name])
        assert secret_storage.read_api_key(name) == value
    encrypted = path.read_bytes()
    secret_storage.save_env(path)
    assert path.read_bytes() == encrypted
