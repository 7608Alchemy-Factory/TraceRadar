"""Explicit paid-call opt-in. Run from backend with --allow-paid-call."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core import Settings
from app.llm import get_provider
from app.connectors import FixtureConnector, SOURCES

if '--allow-paid-call' not in sys.argv:
    raise SystemExit('No call made. Add --allow-paid-call to explicitly invoke configured Qwen.')
settings=Settings()
provider=get_provider('qwen',settings)
evidence=FixtureConnector(settings.fixture_path).fetch(SOURCES[0],'1')[0]
result=provider.assess({'title':'ExampleSoft security','description':'Track vulnerabilities'},[],evidence,SOURCES)
print(result.model_dump_json(indent=2))
