"""Replay frozen cases through the local API and export observable results.

Mock is a pipeline smoke check only: its rules support the old ExampleSoft fixture,
not these benchmark topics. Ground truth is loaded only after all runs finish.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi.testclient import TestClient  # noqa: E402
from app.core import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(response, label):
    if response.status_code >= 400:
        raise RuntimeError(f'{label}: HTTP {response.status_code}: {response.text[:600]}')
    return response.json()


def check_case(case_id):
    case = load(ROOT / 'evaluation/cases' / case_id / 'case.json')
    fixture = load(ROOT / case['fixture'])
    ids = [item['external_id'] for item in fixture]
    assert case['case_id'] == case_id
    assert len(ids) == len(set(ids))
    assert all(datetime.fromisoformat(item['published_at'].replace('Z', '+00:00')) <=
               datetime.fromisoformat(case['stages'][-1]['as_of'].replace('Z', '+00:00')) for item in fixture)
    return case


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_gold(case, gold):
    fixture = load(ROOT / case['fixture'])
    assert case['case_id'] == gold['case_id']
    assert {item['external_id'] for item in fixture} == {x['item_id'] for x in gold['evidence_labels']}
    assert [s['stage_id'] for s in case['stages']] == [s['stage_id'] for s in gold['stage_expectations']]


def wait_run(client, run_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = require(client.get(f'/api/runs/{run_id}'), 'poll run')
        if detail['run']['status'] not in ('pending', 'running'):
            return detail
        time.sleep(.2)
    raise TimeoutError(f'Run {run_id} did not finish in {timeout}s')


def run_case(client, case, provider, timeout):
    source = require(client.post('/api/sources', json=case['runtime_source']), 'register source')
    topic = require(client.post('/api/topics', json=case['topic']), 'create topic')
    topic_id = topic['topic_id']
    require(client.put(f'/api/topics/{topic_id}/sources',
                       json={'approved_source_ids': [source['source_id']]}), 'approve source')
    stages = []
    for stage in case['stages']:
        body = {'fixture_batch_id': 'all', 'provider': provider,
                'as_of': stage['as_of'], 'source_ids': [source['source_id']]}
        queued = require(client.post(f'/api/topics/{topic_id}/runs', json=body), 'start run')
        detail = wait_run(client, queued['run_id'], timeout)
        dashboard = require(client.get(f'/api/topics/{topic_id}/dashboard'), 'dashboard')
        stages.append({'stage_id': stage['stage_id'], 'as_of': stage['as_of'],
                       'run': detail, 'dashboard': dashboard,
                       'evidence': require(client.get(f'/api/topics/{topic_id}/evidence'), 'evidence'),
                       'events': [require(client.get(f"/api/events/{e['event_id']}"), 'event')
                                  for e in dashboard['events']],
                       'notifications': require(client.get(f'/api/topics/{topic_id}/notifications'), 'notifications')})
    return {'case_id': case['case_id'], 'topic_id': topic_id, 'provider': provider,
            'stages': stages}


def diagnostic(result, gold):
    expected = {x['item_id']: x for x in gold['evidence_labels']}
    final = result['stages'][-1]
    observations = []
    for row in final['evidence']:
        evidence = row['evidence']
        item_id = evidence['external_id'].rsplit(':', 1)[-1]
        label = expected.get(item_id)
        assessment = row.get('assessment')
        observations.append({'item_id': item_id, 'expected_relevance': label['relevance'] if label else None,
                             'actual_relevance': assessment.get('is_relevant') if assessment else None,
                             'processing_status': row.get('status'), 'error': row.get('error')})
    return {'evaluation_valid': result['provider'] != 'mock' and gold['annotation_status'] == 'frozen',
            'reason': 'Mock is ExampleSoft-specific; draft labels are not formal scores.'
                      if result['provider'] == 'mock' or gold['annotation_status'] != 'frozen' else None,
            'observations': observations,
            'stage_summary': [{'stage_id': s['stage_id'], 'run_status': s['run']['run']['status'],
                               'events': len(s['events']), 'notifications': len(s['notifications']),
                               'evidence_rows': len(s['evidence'])} for s in result['stages']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', nargs='+', default=['cedar_patch_v1', 'mcd_hk_campaigns_v1', 'cve_2024_3400_v1'])
    parser.add_argument('--provider', choices=['mock', 'qwen'], default='mock')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/reports/benchmark-local')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for case_id in args.cases:
        case = check_case(case_id)
        run_dir = args.output / f'{case_id}-{uuid4().hex[:8]}'
        run_dir.mkdir()
        settings = Settings(database_path=str(run_dir / 'tracker.db'))
        with TestClient(create_app(settings), base_url='http://127.0.0.1',
                        raise_server_exceptions=False) as client:
            result = run_case(client, case, args.provider, args.timeout)
        # Gold is deliberately read after the runtime and worker have stopped.
        gold_path = ROOT / 'evaluation/cases' / case_id / 'ground_truth.json'
        gold = load(gold_path)
        validate_gold(case, gold)
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT.parent,
                                capture_output=True, text=True, check=True).stdout.strip()
        report = {'experiment': {'commit': commit, 'provider': args.provider,
                                 'case_sha256': digest(ROOT / 'evaluation/cases' / case_id / 'case.json'),
                                 'fixture_sha256': digest(ROOT / case['fixture']),
                                 'gold_sha256': digest(gold_path),
                                 'replay_options': {'fixture_batch_id': 'all', 'prefilter_enabled': False,
                                                    'near_duplicate_enabled': True}},
                  'result': result, 'diagnostic': diagnostic(result, gold)}
        (run_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'case_id': case_id, 'report': str(run_dir / 'report.json'),
                          'summary': report['diagnostic']['stage_summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
