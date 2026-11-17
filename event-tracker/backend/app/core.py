import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')


@dataclass
class Settings:
    database_path: str = os.getenv('DATABASE_PATH', str(ROOT / 'data/tracker.db'))
    provider: str = os.getenv('MODEL_PROVIDER', 'mock')
    api_key: str = os.getenv('QWEN_API_KEY', '')
    base_url: str = os.getenv('QWEN_BASE_URL', '')
    model: str = os.getenv('QWEN_MODEL', '')
    timeout: float = float(os.getenv('MODEL_TIMEOUT_SECONDS', '30'))
    max_attempts: int = min(2, max(1, int(os.getenv('MODEL_MAX_ATTEMPTS', '2'))))
    fixture_path: Path = ROOT / 'fixtures/cybersecurity'
    corpus_root: Path = ROOT / 'fixtures'

    def __post_init__(self):
        path = Path(self.database_path)
        self.database_path = str(path if path.is_absolute() else ROOT / path)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
