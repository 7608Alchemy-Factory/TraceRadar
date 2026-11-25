"""Exercise the live capture and scorer against an isolated fake RSS feed."""
import importlib.util
import json
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FeedClient:
    def get(self, url, **kwargs):
        assert url == 'https://example.com/feed.xml'
        return httpx.Response(200, text='''<rss version="2.0"><channel>
          <item><guid>item-1</guid><title>Tracked offer</title>
          <link>https://example.com/offer</link>
          <description>Offer starts today.</description>
          <pubDate>Fri, 20 Nov 2026 10:00:00 GMT</pubDate></item>
        </channel></rss>''')


def test_capture_and_score_live_rss(tmp_path):
    capture = load_script('run_live_benchmark')
    scorer = load_script('score_live_benchmark')
    case = {'case_id': 'smoke',
            'topic': {'title': 'Tracked offer', 'description': 'Follow this offer.'},
            'approved_sources': [{'source_id': 'test_rss', 'name': 'Test RSS',
                                  'type': 'rss', 'endpoint': 'https://example.com/feed.xml',
                                  'config': {'max_items': 10}}],
            'window': {'start': '2026-11-19T00:00:00Z', 'end': '2026-11-21T00:00:00Z'}}
    case_path = tmp_path / 'case.json'
    case_path.write_text(json.dumps(case), encoding='utf-8')
    run_dir = tmp_path / 'run'
    summary = capture.capture_once(case, case_path, 'mock', 30, run_dir, rss_client=FeedClient())
    assert summary['run_status'] == 'completed'
    assert summary['fetched'] == 1
    data = capture.read_json(run_dir / 'capture.json')
    assert data['raw_inputs'][0]['evidence']['content'] == 'Offer starts today.'
    assert data['manifest']['prompt_version']
    labels = capture.read_json(run_dir / 'labels.template.json')
    labels['candidate_labels'][0]['relevance'] = 'relevant'
    labels['known_items'] = [{'id': 'known-1', 'acceptable_urls': ['https://example.com/offer']}]
    result = scorer.score(data, labels)
    assert result['known_item_coverage']['coverage'] == 1
    assert result['information_relevance']['counts']['tp'] + result['information_relevance']['counts']['fn'] + result['information_relevance']['counts']['no_decision'] == 1
    assert result['retrieval_diagnostics']['in_window'] == 1


def test_scorer_rejects_labels_for_other_capture():
    scorer = load_script('score_live_benchmark')
    try:
        scorer.score({'manifest': {'case_id': 'a', 'capture_id': 'x'}},
                     {'case_id': 'a', 'capture_id': 'y'})
    except ValueError as error:
        assert 'different capture' in str(error)
    else:
        raise AssertionError('Mismatched labels must be rejected.')
