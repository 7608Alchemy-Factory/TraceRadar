"""Capture search discovery without running Event assessment or notifications."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.connectors import ConnectorRouter, _build_google_news_rss_url, recommend_sources, validate_source_definition  # noqa: E402
from app.core import Settings  # noqa: E402
from app.web_connectors import _resolve_active_search_provider  # noqa: E402


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run_source(router, source, *, as_of, corpus_root):
    source = validate_source_definition(source, corpus_root)
    try:
        items = router.fetch(source, 'live', as_of=as_of, existing_items=[], in_run_urls=set())
        return {'source': source, 'items': items, 'stats': dict(router.last_fetch_stats), 'error': None}
    except Exception as exc:
        return {'source': source, 'items': [], 'stats': {},
                'error': {'type': type(exc).__name__, 'code': getattr(exc, 'code', None),
                          'message': getattr(exc, 'message', str(exc))[:300]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    case_bytes = args.case.read_bytes()
    case = json.loads(case_bytes)
    start = datetime.fromisoformat(case['window']['start'])
    end = datetime.fromisoformat(case['window']['end'])
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        parser.error('window dates must include time zones and start must precede end')
    settings = Settings()
    topic = case['topic']
    recommendation = recommend_sources(
        topic, [], current_events=[], evidence_decisions=[], notifications=[],
        approved_source_ids=set(), llm_summary=True, settings=settings,
        search_languages=[case['language']],
    )
    ai_suggestions = [s for s in recommendation['sources']
                      if s.get('role') == 'branch' and (s.get('config') or {}).get('cold_start')
                      and s.get('type') == 'web_search'][:case['ai_max_queries']]
    router = ConnectorRouter(settings.fixture_path, settings.corpus_root, settings=settings)
    start_date = start.date().isoformat()
    as_of = end.astimezone(timezone.utc).isoformat()
    baseline = {
        'source_id': 'benchmark_baseline', 'name': 'Fixed baseline query', 'type': 'web_search',
        'endpoint': f"search://{quote_plus(case['baseline_query'])}",
        'config': {'query': case['baseline_query'], 'search_language': case['language'],
                   'after_date': start_date, 'max_items': case['baseline_max_items'],
                   'fetch_full_text': True},
    }
    rss = {
        'source_id': 'benchmark_rss', 'name': 'Google News RSS', 'type': 'rss',
        'endpoint': _build_google_news_rss_url(case['baseline_query'], after_date=start_date,
                                               locale=case['language']),
        'config': {'max_items': case['rss_max_items'], 'fetch_full_text': True},
    }
    arms = {'baseline_web_search': [baseline], 'ai_queries_web_search': [], 'rss': [rss]}
    for i, suggestion in enumerate(ai_suggestions, 1):
        cfg = dict(suggestion['config'])
        cfg.update(after_date=start_date, max_items=case['ai_max_items_per_query'], fetch_full_text=True)
        cfg.pop('recency_days', None)
        arms['ai_queries_web_search'].append({
            'source_id': f'benchmark_ai_{i}', 'name': suggestion['name'], 'type': 'web_search',
            'endpoint': suggestion['endpoint'], 'config': cfg,
        })
    captures = {name: [run_source(router, src, as_of=as_of, corpus_root=settings.corpus_root)
                       for src in sources] for name, sources in arms.items()}
    report = {
        'case_id': case['case_id'], 'captured_at': datetime.now(timezone.utc).isoformat(),
        'case_sha256': hashlib.sha256(case_bytes.replace(b'\r\n', b'\n')).hexdigest(),
        'topic': topic, 'window': case['window'],
        'runtime': {'model_id': settings.model, 'search_provider_preference': settings.search_provider,
                    'effective_search_provider': _resolve_active_search_provider(settings, baseline['config'])[0],
                    'bocha_configured': bool(settings.bocha_api_key),
                    'tavily_configured': bool(settings.tavily_api_key)},
        'cold_start': {'enabled': recommendation['llm_summary_enabled'],
                       'mode': recommendation['llm_mode'],
                       'note': recommendation['llm_summary_note'],
                       'error': recommendation['llm_summary_error'],
                       'suggestions': [{'query': s['config']['query'], 'language': s['config'].get('search_language'),
                                        'reason': s.get('reason')} for s in ai_suggestions],
                       'usage': recommendation.get('llm_usage')},
        'arms': captures,
        'scope_note': 'Search and connector output only; no Event/notification processing. Reference labels are separate.',
    }
    write_json(args.output, report)
    print(args.output.resolve())
    for name, rows in captures.items():
        print(f"{name}: queries={len(rows)}, items={sum(len(x['items']) for x in rows)}, "
              f"errors={sum(x['error'] is not None for x in rows)}")


if __name__ == '__main__':
    main()
