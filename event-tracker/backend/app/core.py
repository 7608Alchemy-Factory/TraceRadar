import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
if (ROOT.parent / '.env').exists():
    load_dotenv(ROOT.parent / '.env', override=False)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ('0', 'false', 'no', 'off')


@dataclass
class Settings:
    env_path: Path = ROOT / '.env'
    database_path: str = os.getenv('DATABASE_PATH', str(ROOT / 'data/tracker.db'))
    provider: str = os.getenv('MODEL_PROVIDER', 'mock')
    api_key: str = field(default=os.getenv('QWEN_API_KEY', ''),repr=False)
    base_url: str = os.getenv('QWEN_BASE_URL', '')
    model: str = os.getenv('QWEN_MODEL', '')
    timeout: float = float(os.getenv('MODEL_TIMEOUT_SECONDS', '30'))
    max_attempts: int = min(2, max(1, int(os.getenv('MODEL_MAX_ATTEMPTS', '2'))))
    fixture_path: Path = ROOT / 'fixtures/cybersecurity'
    corpus_root: Path = ROOT / 'fixtures'
    search_provider: str = field(default_factory=lambda: os.getenv('SEARCH_PROVIDER', 'auto'))
    bocha_api_key: str = field(default_factory=lambda: os.getenv('BOCHA_API_KEY', ''), repr=False)
    bocha_base_url: str = field(default_factory=lambda: os.getenv('BOCHA_BASE_URL', 'https://api.bochaai.com/v1'))
    tavily_api_key: str = field(default_factory=lambda: os.getenv('TAVILY_API_KEY', ''), repr=False)
    tavily_base_url: str = field(default_factory=lambda: os.getenv('TAVILY_BASE_URL', 'https://api.tavily.com'))
    jina_api_key: str = field(default_factory=lambda: os.getenv('JINA_API_KEY', ''), repr=False)
    web_fetch_jina_fallback: bool = field(default_factory=lambda: _env_bool('WEB_FETCH_JINA_FALLBACK', True))

    def __post_init__(self):
        path = Path(self.database_path)
        self.database_path = str(path if path.is_absolute() else ROOT / path)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
