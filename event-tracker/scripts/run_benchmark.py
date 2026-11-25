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
from app.llm import PROMPT_VERSION  # noqa: E402
from app.main import create_app  # noqa: E402
from build_review_sheet import build as build_review_sheet  # noqa: E402
from score_replay_state import (score as score_replay_state, markdown as state_score_markdown,
                                review_template as state_review_template)  # noqa: E402


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
    # Git may check out text with CRLF on Windows; hash canonical LF bytes.
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def validate_gold(case, gold):
    fixture = load(ROOT / case['fixture'])
    assert case['case_id'] == gold['case_id']
    assert {item['external_id'] for item in fixture} == {x['item_id'] for x in gold['evidence_labels']}
    assert [s['stage_id'] for s in case['stages']] == [s['stage_id'] for s in gold['stage_expectations']]


def verify_freeze(case, gold, gold_path):
    if gold['annotation_status'] != 'frozen':
        return
    manifest_path = gold_path.with_name('freeze_manifest.json')
    if not manifest_path.exists():
        raise ValueError(f'Frozen case needs a freeze manifest: {manifest_path}')
    manifest = load(manifest_path)
    expected = {'case_sha256': ROOT / 'evaluation/cases' / case['case_id'] / 'case.json',
                'fixture_sha256': ROOT / case['fixture'],
                'ground_truth_sha256': gold_path}
    if manifest.get('case_id') != case['case_id'] or manifest.get('status') != 'frozen':
        raise ValueError(f'Freeze manifest identity/status mismatch: {manifest_path}')
    for field, path in expected.items():
        if manifest.get(field) != digest(path):
            raise ValueError(f'Frozen input changed ({field}): {path}')


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


def diagnostic(result, gold, fixture, case):
    expected = {x['item_id']: x for x in gold['evidence_labels']}
    final = result['stages'][-1]
    stored = {row['evidence']['external_id'].rsplit(':', 1)[-1]: row for row in final['evidence']}
    stage_summary = []
    for stage in result['stages']:
        run = stage['run']['run']
        by_item = {row['external_id'].rsplit(':', 1)[-1]: row for row in stage['run']['evidence']}
        cutoff = datetime.fromisoformat(stage['as_of'].replace('Z', '+00:00'))
        items = []
        for item in fixture:
            if datetime.fromisoformat(item['published_at'].replace('Z', '+00:00')) > cutoff:
                continue
            item_id = item['external_id']
            detail = by_item.get(item_id, dict(status='not_attempted', attempts=0, evidence_id=None,
                                               error=run['error'], event_outcomes=[]))
            items.append(dict(item_id=item_id, **detail))
        stage_summary.append(dict(stage_id=stage['stage_id'], run_status=run['status'], events=len(stage['events']),
                                  notifications=len(stage['notifications']), evidence_rows=len(stage['evidence']), items=items))
    observations = []
    for detail in stage_summary[-1]['items']:
        item_id = detail['item_id']
        label = expected[item_id]
        assessment = stored.get(item_id, {}).get('assessment')
        observations.append({'item_id': item_id, 'expected_relevance': label['relevance'],
                             'actual_relevance': assessment.get('is_relevant') if assessment else None,
                             'processing_status': detail['status'], 'error': detail['error']})
    evaluation_valid = (result['provider'] != 'mock' and gold['annotation_status'] == 'frozen'
                        and case.get('formal_scoring_allowed', True))
    reason = ('Mock is ExampleSoft-specific.' if result['provider'] == 'mock' else
              'Ground truth is not frozen.' if gold['annotation_status'] != 'frozen' else
              'Curated retrospective input lacks verified historical snapshots; formal scoring is disabled.'
              if not case.get('formal_scoring_allowed', True) else None)
    return {'evaluation_valid': evaluation_valid, 'reason': reason,
            'observations': observations,
            'stage_summary': stage_summary}


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
        verify_freeze(case, gold, gold_path)
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT.parent,
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT.parent,
                               capture_output=True, text=True, check=True).stdout
        report = {'experiment': {'commit': commit, 'working_tree_dirty': bool(dirty), 'provider': args.provider,
                                 'model_id': settings.model if args.provider == 'qwen' else None,
                                 'model_base_url': settings.base_url if args.provider == 'qwen' else None,
                                 'prompt_version': PROMPT_VERSION if args.provider == 'qwen' else None,
                                 'model_timeout_seconds': settings.timeout if args.provider == 'qwen' else None,
                                 'model_max_attempts': settings.max_attempts if args.provider == 'qwen' else None,
                                 'case_sha256': digest(ROOT / 'evaluation/cases' / case_id / 'case.json'),
                                 'fixture_sha256': digest(ROOT / case['fixture']),
                                 'gold_sha256': digest(gold_path),
                                 'replay_options': {'fixture_batch_id': 'all', 'prefilter_enabled': False,
                                                    'near_duplicate_enabled': True}},
                  'result': result, 'diagnostic': diagnostic(result, gold, load(ROOT / case['fixture']), case)}
        report_path = run_dir / 'report.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (run_dir / 'review.md').write_text(build_review_sheet(report_path), encoding='utf-8')
        state_score = score_replay_state(report, gold)
        (run_dir / 'state_score.json').write_text(json.dumps(state_score, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (run_dir / 'state_score.md').write_text(state_score_markdown(state_score), encoding='utf-8')
        (run_dir / 'state_adjudication.template.json').write_text(
            json.dumps(state_review_template(state_score, report['experiment']['gold_sha256']),
                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'case_id': case_id, 'report': str(run_dir / 'report.json'),
                          'review': str(run_dir / 'review.md'),
                          'state_score': str(run_dir / 'state_score.json'),
                          'summary': [{key: value for key, value in stage.items() if key != 'items'}
                                      for stage in report['diagnostic']['stage_summary']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
