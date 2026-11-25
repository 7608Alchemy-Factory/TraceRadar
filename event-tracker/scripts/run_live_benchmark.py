"""Capture a bounded live RSS evaluation run without exposing annotations to Runtime.

The case file contains only the topic, explicitly approved RSS sources and a time
window. Results are saved under an ignored local report directory. Run
score_live_benchmark.py later with independently prepared annotations.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi.testclient import TestClient  # noqa: E402
from app.core import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from revision import source_revision  # noqa: E402


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def utc_time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Time window values must include a timezone.')
    return result.astimezone(timezone.utc)


def validate_case(case):
    if not isinstance(case.get('case_id'), str) or not case['case_id'].strip():
        raise ValueError('case_id is required.')
    if not isinstance(case.get('topic'), dict) or not case['topic'].get('title') or not case['topic'].get('description'):
        raise ValueError('topic needs title and description.')
    sources = case.get('approved_sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('approved_sources must be a nonempty list of explicit RSS definitions.')
    ids = [source.get('source_id') for source in sources]
    if len(ids) != len(set(ids)) or any(source.get('type') != 'rss' for source in sources):
        raise ValueError('Approved sources must have unique IDs and type=rss.')
    window = case.get('window') or {}
    start, end = utc_time(window['start']), utc_time(window['end'])
    if start >= end:
        raise ValueError('window.start must precede window.end.')
    return start, end


def require(response, label):
    if response.status_code >= 400:
        raise RuntimeError(f'{label}: HTTP {response.status_code}: {response.text[:500]}')
    return response.json()


def wait_run(client, run_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = require(client.get(f'/api/runs/{run_id}'), 'poll run')
        if detail['run']['status'] not in ('pending', 'running'):
            return detail
        time.sleep(.25)
    return require(client.get(f'/api/runs/{run_id}'), 'poll timed-out run')


def capture_once(case, case_path, provider, timeout, run_dir, rss_client=None):
    start, end = validate_case(case)
    run_dir.mkdir(parents=True, exist_ok=False)
    settings = Settings(database_path=str(run_dir / 'tracker.db'))
    app = create_app(settings)
    if rss_client is not None:
        app.state.manager.connector.rss.client = rss_client
    with TestClient(app, base_url='http://127.0.0.1', raise_server_exceptions=False) as client:
        topic = require(client.post('/api/topics', json=case['topic']), 'create topic')
        topic_id = topic['topic_id']
        sources = [require(client.post('/api/sources', json=source), 'register RSS source')
                   for source in case['approved_sources']]
        # Recommendations are observable, but only explicitly listed sources are approved.
        before = require(client.post(f'/api/topics/{topic_id}/source-recommendations',
                                     json={'llm_summary': False}), 'source recommendations before')
        require(client.put(f'/api/topics/{topic_id}/sources',
                           json={'approved_source_ids': [source['source_id'] for source in sources]}),
                'approve listed sources')
        request = {'fixture_batch_id': 'live', 'provider': provider,
                   'source_ids': [source['source_id'] for source in sources],
                   'as_of': end.isoformat(), 'prefilter_enabled': False,
                   'near_duplicate_enabled': True}
        queued = require(client.post(f'/api/topics/{topic_id}/runs', json=request), 'start live run')
        detail = wait_run(client, queued['run_id'], timeout)
        dashboard = require(client.get(f'/api/topics/{topic_id}/dashboard'), 'dashboard')
        evidence = require(client.get(f'/api/topics/{topic_id}/evidence'), 'evidence decisions')
        events = [require(client.get(f"/api/events/{row['event_id']}"), 'event')
                  for row in dashboard['events']]
        notifications = require(client.get(f'/api/topics/{topic_id}/notifications'), 'notifications')
        after = require(client.post(f'/api/topics/{topic_id}/source-recommendations',
                                    json={'llm_summary': False}), 'source recommendations after')
        raw_inputs = [{'position': position, 'evidence': raw, 'outcome': outcome}
                      for position, raw, outcome in app.state.repo.run_inputs(queued['run_id'])]

    manifest = {
        'case_id': case['case_id'], 'capture_id': run_dir.name,
        'captured_at': datetime.now(timezone.utc).isoformat(),
        **source_revision(ROOT.parent),
        'case_sha256': hashlib.sha256(case_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        'provider': provider, 'model_id': settings.model if provider == 'qwen' else None,
        'prompt_version': detail['run'].get('prompt_version'),
        'model_base_url': settings.base_url if provider == 'qwen' else None,
        'model_timeout_seconds': settings.timeout if provider == 'qwen' else None,
        'model_max_attempts': settings.max_attempts if provider == 'qwen' else None,
        'window_start': start.isoformat(), 'window_end': end.isoformat(),
        'runtime_window_rule': 'RSS connector applies only window_end; window_start is analysis-only.',
        'feed_content_rule': 'RSS/Atom entry content or summary; article body is not fetched.',
        'search_rule': 'Approved RSS feeds only; no general web search or article fetch.',
    }
    report = {'manifest': manifest, 'topic': topic, 'approved_sources': sources,
              'source_recommendations_before': before, 'source_recommendations_after': after,
              'run_request': request, 'run_detail': detail, 'raw_inputs': raw_inputs,
              'evidence_decisions': evidence, 'dashboard': dashboard,
              'events': events, 'notifications': notifications}
    write_json(run_dir / 'capture.json', report)
    labels = {'case_id': case['case_id'], 'capture_id': run_dir.name,
              'candidate_labels': [dict(candidate_key=f"{row['evidence']['source_id']}:{row['evidence']['external_id']}",
                                        url=row['evidence']['url'], title=row['evidence']['title'],
                                        relevance='unjudged', event_keys=[], review_note='')
                                   for row in raw_inputs],
              'known_items': []}
    write_json(run_dir / 'labels.template.json', labels)
    return {'capture': str(run_dir / 'capture.json'), 'labels_template': str(run_dir / 'labels.template.json'),
            'run_status': detail['run']['status'], 'fetched': len(raw_inputs), 'events': len(events)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True, help='Topic and approved RSS sources only; no gold labels.')
    parser.add_argument('--provider', choices=['qwen', 'mock'], default='qwen')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/reports/live-local')
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 20 or args.timeout <= 0:
        parser.error('repeats must be 1–20 and timeout positive')
    case = read_json(args.case)
    validate_case(case)
    args.output.mkdir(parents=True, exist_ok=True)
    for _ in range(args.repeats):
        run_dir = args.output / f"{case['case_id']}-{uuid4().hex[:8]}"
        print(json.dumps(capture_once(case, args.case, args.provider, args.timeout, run_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
