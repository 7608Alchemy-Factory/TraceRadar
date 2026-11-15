import hashlib
import json
from pathlib import Path
from typing import Protocol
from .domain import Evidence, uid, now

SOURCES = [
    dict(source_id='vendor', name='ExampleSoft advisory (simulated)', type='fixture', endpoint='fixture://vendor', config={}),
    dict(source_id='research', name='Security research (simulated)', type='fixture', endpoint='fixture://research', config={}),
]


class Connector(Protocol):
    def fetch(self, source: dict, batch: str) -> list[dict]: ...


class FixtureConnector:
    def __init__(self, path: Path):
        self.path = path

    def fetch(self, source, batch):
        # File path comes exclusively from the registry and validated batch enum.
        if source['source_id'] not in {s['source_id'] for s in SOURCES} or batch not in {'1', '2', '3', 'empty'}:
            raise ValueError('Unregistered fixture input')
        data = json.loads((self.path / f'{batch}.json').read_text(encoding='utf-8'))
        result = []
        for item in data:
            if item['source_id'] != source['source_id']:
                continue
            item['content'] = ' '.join(item['content'].split())
            identity = uid()
            item.update(evidence_id=identity, canonical_evidence_id=identity, fetched_at=now(),
                        content_hash=hashlib.sha256(item['content'].encode()).hexdigest())
            result.append(Evidence.model_validate(item).model_dump(mode='json'))
        return sorted(result, key=lambda e: (e['published_at'], e['external_id']))
