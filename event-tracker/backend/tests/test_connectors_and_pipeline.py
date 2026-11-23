import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors import ConnectorRouter, ReplayConnector, RssConnector, recommend_sources, validate_source_definition
from app.core import AppError, Settings
from app.evidence_processing import (
    canonicalize_url,
    clean_text,
    compute_content_hash,
    find_duplicate_match,
    parse_published_at,
    score_relevance,
)
from app.main import create_app


def _wait_run(client, run_id):
    for _ in range(400):
        data = client.get(f'/api/runs/{run_id}').json()
        if data['run']['status'] not in ('pending', 'running'):
            return data
        time.sleep(0.01)
    pytest.fail('Run timed out')


def test_url_canonicalization_and_text_cleaning():
    u1 = 'HTTPS://News.Example.com:443/world/story-1/?utm_source=x&id=42&fbclid=abc#section'
    u2 = 'https://news.example.com/world/story-1?id=42'
    assert canonicalize_url(u1) == u2
    assert clean_text('<p>Hello <b>World</b> &amp; AI<script>alert(1)</script></p>') == 'Hello World & AI'
    assert parse_published_at('Sun, 15 Nov 2026 06:00:00 +0800') == '2026-11-14T22:00:00Z'
    assert parse_published_at('not-a-date') is None


def test_near_duplicate_catches_reworded_repost_but_keeps_conflict():
    base = {
        'evidence_id': 'ev-1',
        'url': 'https://outlet-a.example/news/chip-deal?utm_source=rss',
        'title': 'Acme Corp announces 4.2 billion dollar acquisition of ChipLabs in Austin',
        'content': (
            'Acme Corp officially announced today a 4.2 billion dollar agreement to acquire '
            'ChipLabs in Austin, Texas, with regulatory review expected early next year.'
        ),
        'content_hash': compute_content_hash(
            'Acme Corp officially announced today a 4.2 billion dollar agreement to acquire '
            'ChipLabs in Austin, Texas, with regulatory review expected early next year.'
        ),
    }
    syndicated_repost = {
        'evidence_id': 'ev-2',
        'url': 'https://outlet-b.example/reprint/chip-deal',
        'title': 'Acme Corp announces $4.2B acquisition of ChipLabs in Austin',
        'content': (
            'Acme Corp announced today a 4.2 billion dollar agreement to acquire '
            'ChipLabs in Austin, Texas; regulatory review is expected early next year.'
        ),
        'content_hash': compute_content_hash('different hash due to punctuation'),
    }
    distinct_conflict = {
        'evidence_id': 'ev-3',
        'url': 'https://outlet-c.example/news/chip-deal-denied',
        'title': 'ChipLabs spokesperson denies Acme acquisition talks and calls report premature',
        'content': (
            'A ChipLabs spokesperson denied any signed acquisition deal with Acme Corp, '
            'stating that board discussions collapsed over valuation disagreements last week.'
        ),
        'content_hash': compute_content_hash('conflict text'),
    }
    dup = find_duplicate_match(syndicated_repost, [base], enable_near_duplicate=True)
    assert dup is not None
    assert dup['canonical_evidence_id'] == 'ev-1'
    assert dup['reason'] == 'near_duplicate'
    assert find_duplicate_match(distinct_conflict, [base], enable_near_duplicate=True) is None


def test_replay_connector_prevents_future_leakage_and_path_traversal(tmp_path):
    corpus_dir = tmp_path / 'fixtures' / 'sports'
    corpus_dir.mkdir(parents=True)
    records = [
        {
            'source_id': 'wire',
            'external_id': 'item-1',
            'url': 'https://wire.example/1',
            'title': 'Player leaves Lakers',
            'content': 'Agent confirms player will enter free agency on June 30.',
            'published_at': '2026-06-30T10:00:00Z',
        },
        {
            'source_id': 'wire',
            'external_id': 'item-2',
            'url': 'https://wire.example/2',
            'title': 'Player signs with 76ers',
            'content': 'Official signing announced on July 24.',
            'published_at': '2026-07-24T15:00:00Z',
        },
    ]
    (corpus_dir / 'all.json').write_text(json.dumps(records), encoding='utf-8')
    connector = ReplayConnector(tmp_path / 'fixtures')
    source = {'source_id': 'wire', 'type': 'replay', 'endpoint': 'replay://sports', 'config': {'corpus_path': 'sports'}}

    slice_early = connector.fetch(source, batch='all', as_of='2026-07-01T00:00:00Z')
    assert [e['external_id'] for e in slice_early] == ['item-1']
    assert connector.last_stats['future_filtered'] == 1

    slice_late = connector.fetch(source, batch='all', as_of='2026-08-01T00:00:00Z')
    assert [e['external_id'] for e in slice_late] == ['item-1', 'item-2']

    with pytest.raises(AppError, match='fixtures directory'):
        validate_source_definition(
            {'source_id': 'bad', 'name': 'Bad', 'type': 'replay', 'endpoint': 'replay://../../etc', 'config': {}},
            tmp_path / 'fixtures',
        )


def test_rss_connector_parses_rss_and_atom_and_handles_failures(tmp_path):
    rss_xml = '''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Feed</title>
      <item>
        <title>ExampleSoft releases emergency patch</title>
        <link>https://news.example/patch?utm_source=rss</link>
        <guid>rss-1</guid>
        <pubDate>Sun, 15 Nov 2026 04:00:00 GMT</pubDate>
        <description>&lt;p&gt;Patch &lt;b&gt;v1.2&lt;/b&gt; is live.&lt;/p&gt;</description>
      </item>
      <item>
        <title>Missing timestamp item</title>
        <link>https://news.example/no-date</link>
      </item>
    </channel></rss>'''
    atom_xml = '''<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Atom security advisory</title>
        <id>atom-1</id>
        <link rel="alternate" href="https://sec.example/advisory"/>
        <updated>2026-11-15T05:00:00Z</updated>
        <summary>Details published.</summary>
      </entry>
    </feed>'''

    calls = {'count': 0}

    def handler(request):
        calls['count'] += 1
        if 'flaky' in str(request.url) and calls['count'] == 1:
            return httpx.Response(503)
        if 'broken' in str(request.url):
            return httpx.Response(200, text='<rss><broken')
        if 'atom' in str(request.url):
            return httpx.Response(200, text=atom_xml)
        return httpx.Response(200, text=rss_xml)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rss = RssConnector(client=client, max_attempts=2)
        items = rss.fetch({'source_id': 'flaky_rss', 'endpoint': 'https://flaky.example/rss', 'config': {'max_items': 10}})
        assert calls['count'] == 2
        assert len(items) == 1
        assert items[0]['url'] == 'https://news.example/patch'
        assert items[0]['content'] == 'Patch v1.2 is live.'
        assert items[0]['published_at'] == '2026-11-15T04:00:00Z'
        assert rss.last_stats['skipped_missing_timestamp'] == 1

        atom_items = rss.fetch({'source_id': 'atom_rss', 'endpoint': 'https://atom.example/feed', 'config': {}})
        assert len(atom_items) == 1
        assert atom_items[0]['external_id'] == 'atom-1'

        with pytest.raises(AppError, match='Malformed RSS/Atom XML'):
            rss.fetch({'source_id': 'bad_rss', 'endpoint': 'https://broken.example/rss', 'config': {}})

    with pytest.raises(AppError, match='Loopback'):
        validate_source_definition(
            {'source_id': 'local', 'name': 'Local', 'type': 'rss', 'endpoint': 'http://127.0.0.1:9000/rss', 'config': {}},
            tmp_path,
        )


def test_end_to_end_rss_isolation_near_dedup_manual_and_prefilter(tmp_path):
    rss_good = '''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>ExampleSoft vulnerability A disclosed</title>
        <link>https://media1.example/vuln-a?utm_medium=feed</link>
        <guid>g1</guid>
        <pubDate>Sun, 01 Nov 2026 09:00:00 GMT</pubDate>
        <description>ExampleSoft vulnerability A disclosed. A remote attacker can access private records in version 1.0.</description>
      </item>
      <item>
        <title>ExampleSoft vulnerability A disclosed in report</title>
        <link>https://media2.example/vuln-a-copy</link>
        <guid>g2</guid>
        <pubDate>Sun, 01 Nov 2026 09:30:00 GMT</pubDate>
        <description>ExampleSoft vulnerability A disclosed; a remote attacker can access private records in version 1.0.</description>
      </item>
      <item>
        <title>Local weather forecast sunny</title>
        <link>https://media1.example/weather</link>
        <guid>g3</guid>
        <pubDate>Sun, 01 Nov 2026 10:00:00 GMT</pubDate>
        <description>Clear skies expected throughout the weekend.</description>
      </item>
    </channel></rss>'''

    def handler(request):
        if 'bad-rss' in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=rss_good)

    settings = Settings(database_path=str(tmp_path / 'tracker.db'))
    app = create_app(settings)
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as rss_client:
        app.state.manager.connector = ConnectorRouter(settings.fixture_path, settings.corpus_root, rss_client=rss_client)
        with TestClient(app) as client:
            topic = client.post(
                '/api/topics',
                json={'title': 'ExampleSoft security', 'description': 'Track ExampleSoft vulnerabilities and patches'},
            ).json()
            tid = topic['topic_id']

            recs = client.post(f'/api/topics/{tid}/source-recommendations').json()
            assert recs['recommendation_mode'] == 'topic_aware_v1'
            assert any(s['type'] == 'rss' for s in recs['sources'])

            assert client.post('/api/sources', json={
                'source_id': 'good_rss',
                'name': 'Good RSS',
                'type': 'rss',
                'endpoint': 'https://good-rss.example/feed.xml',
                'config': {'tier': 'media', 'max_items': 10},
            }).status_code == 201
            assert client.post('/api/sources', json={
                'source_id': 'bad_rss',
                'name': 'Bad RSS',
                'type': 'rss',
                'endpoint': 'https://bad-rss.example/feed.xml',
                'config': {'tier': 'media', 'max_items': 10},
            }).status_code == 201
            assert client.post('/api/sources', json={
                'source_id': 'manual',
                'name': 'Manual input',
                'type': 'manual',
                'endpoint': 'manual://user',
                'config': {'tier': 'manual'},
            }).status_code == 201

            assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['good_rss', 'bad_rss', 'manual']}).status_code == 200

            run_resp = client.post(f'/api/topics/{tid}/runs', json={
                'fixture_batch_id': 'live',
                'provider': 'mock',
                'prefilter_enabled': True,
                'near_duplicate_enabled': True,
                'manual_items': [{
                    'source_id': 'manual',
                    'title': 'Manual: ExampleSoft vulnerability A patch released',
                    'content': 'ExampleSoft vulnerability A patch released in version 1.2.',
                    'published_at': '2026-11-02T09:00:00Z',
                }],
            })
            assert run_resp.status_code == 202
            detail = _wait_run(client, run_resp.json()['run_id'])
            # bad_rss failed cleanly, while good_rss + manual committed event A -> patched,
            # g2 skipped by near_duplicate, and g3 skipped by prefilter!
            assert detail['run']['status'] == 'completed_with_errors'
            assert detail['run']['counts']['committed'] == 2
            assert detail['run']['counts']['skipped'] == 1
            assert detail['run']['counts']['irrelevant'] == 1
            assert detail['run']['counts']['errors'] == 1
            assert any(
                s['step_name'] == 'normalize_and_deduplicate' and s['decision'].get('reason') == 'near_duplicate'
                for s in detail['steps']
            )
            assert any(
                s['step_name'] == 'prefilter_relevance' and s['decision'].get('reason') == 'prefilter_low_relevance'
                for s in detail['steps']
            )
            dash = client.get(f'/api/topics/{tid}/dashboard').json()
            assert dash['events'][0]['state']['current_status'] == 'patched'


def test_relevance_prefilter_scoring():
    topic = {'title': 'NVIDIA Blackwell GPU release', 'description': 'Track supply chain and shipping delays'}
    relevant = {'title': 'NVIDIA Blackwell GPU shipping delay reported', 'content': 'Supply chain constraints hit Q4 shipments.'}
    unrelated = {'title': 'Premier League match highlights', 'content': 'Arsenal won 2-0 on Sunday afternoon.'}
    assert score_relevance(topic, relevant)['passed'] is True
    assert score_relevance(topic, unrelated)['passed'] is False


def test_recommended_rss_can_be_approved_directly_and_replay_handles_multi_outlet_corpus(tmp_path):
    corpus_dir = tmp_path / 'fixtures' / 'multi'
    corpus_dir.mkdir(parents=True)
    records = [
        {
            'source_id': 'espn',
            'external_id': 'story-1',
            'url': 'https://espn.example/1',
            'title': 'ExampleSoft vulnerability A disclosed by ESPN tech',
            'content': 'ExampleSoft vulnerability A disclosed. A remote attacker can access private records.',
            'published_at': '2026-11-01T09:00:00Z',
        },
        {
            'source_id': 'vendor_blog',
            'external_id': 'story-1',
            'url': 'https://vendor.example/1',
            'title': 'ExampleSoft vulnerability A patch released',
            'content': 'ExampleSoft vulnerability A patch released in version 1.2.',
            'published_at': '2026-11-02T09:00:00Z',
        },
    ]
    (corpus_dir / 'all.json').write_text(json.dumps(records), encoding='utf-8')

    settings = Settings(database_path=str(tmp_path / 'tracker.db'), corpus_root=tmp_path / 'fixtures')
    with TestClient(create_app(settings)) as client:
        topic = client.post(
            '/api/topics',
            json={'title': 'ExampleSoft security', 'description': 'Track ExampleSoft vulnerabilities and patches'},
        ).json()
        tid = topic['topic_id']

        recs = client.post(f'/api/topics/{tid}/source-recommendations').json()
        rss_ids = [s['source_id'] for s in recs['sources'] if s['type'] == 'rss']
        assert rss_ids and all(s['registered'] for s in recs['sources'])
        assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': rss_ids}).status_code == 200

        assert client.post('/api/sources', json={
            'source_id': 'multi_replay',
            'name': 'Multi-outlet replay',
            'type': 'replay',
            'endpoint': 'replay://multi',
            'config': {'corpus_path': 'multi'},
        }).status_code == 201
        assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['multi_replay']}).status_code == 200

        r1 = client.post(f'/api/topics/{tid}/runs', json={
            'fixture_batch_id': 'all',
            'provider': 'mock',
            'as_of': '2026-11-01T23:59:59Z',
        })
        assert r1.status_code == 202
        d1 = _wait_run(client, r1.json()['run_id'])
        assert d1['run']['status'] == 'completed'
        assert d1['run']['counts']['committed'] == 1

        r2 = client.post(f'/api/topics/{tid}/runs', json={
            'fixture_batch_id': 'all',
            'provider': 'mock',
            'as_of': '2026-11-03T00:00:00Z',
        })
        assert r2.status_code == 202
        d2 = _wait_run(client, r2.json()['run_id'])
        assert d2['run']['status'] == 'completed'
        assert d2['run']['counts']['committed'] == 1
        assert d2['run']['counts']['skipped'] == 1
        dash = client.get(f'/api/topics/{tid}/dashboard').json()
        assert dash['events'][0]['state']['current_status'] == 'patched'


def test_source_revision_and_invalid_fixture_batch_isolated(tmp_path):
    settings = Settings(database_path=str(tmp_path / 'tracker.db'))
    with TestClient(create_app(settings)) as client:
        topic = client.post(
            '/api/topics',
            json={'title': 'ExampleSoft security', 'description': 'Track ExampleSoft vulnerabilities and patches'},
        ).json()
        tid = topic['topic_id']

        assert client.post('/api/sources', json={
            'source_id': 'manual',
            'name': 'Manual input',
            'type': 'manual',
            'endpoint': 'manual://user',
            'config': {},
        }).status_code == 201
        assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['vendor', 'manual']}).status_code == 200

        # Invalid batch for fixture 'vendor' fails that source cleanly while 'manual' still commits.
        r = client.post(f'/api/topics/{tid}/runs', json={
            'fixture_batch_id': 'live',
            'provider': 'mock',
            'manual_items': [{
                'external_id': 'm-1',
                'title': 'ExampleSoft vulnerability A disclosed',
                'content': 'ExampleSoft vulnerability A disclosed in module X.',
                'published_at': '2026-11-01T09:00:00Z',
            }],
        })
        detail = _wait_run(client, r.json()['run_id'])
        assert detail['run']['status'] == 'completed_with_errors'
        assert detail['run']['counts']['committed'] == 1
        assert any(s['error'] and 'only supports batches' in s['error']['message'] for s in detail['steps'])

        # Mutating content at the same (source_id, external_id) triggers SOURCE_REVISION without crashing worker,
        # while another valid item in the same run still commits cleanly.
        r_rev = client.post(f'/api/topics/{tid}/runs', json={
            'fixture_batch_id': 'live',
            'provider': 'mock',
            'manual_items': [
                {
                    'external_id': 'm-1',
                    'title': 'ExampleSoft vulnerability A disclosed',
                    'content': 'ExampleSoft vulnerability A silently altered body.',
                    'published_at': '2026-11-01T09:00:00Z',
                },
                {
                    'external_id': 'm-2',
                    'title': 'ExampleSoft vulnerability A patch released',
                    'content': 'ExampleSoft vulnerability A patch released in version 1.2.',
                    'published_at': '2026-11-02T09:00:00Z',
                },
            ],
        })
        detail_rev = _wait_run(client, r_rev.json()['run_id'])
        assert detail_rev['run']['status'] == 'completed_with_errors'
        assert detail_rev['run']['counts']['committed'] == 1
        assert any(s['error'] and s['error']['code'] == 'SOURCE_REVISION' for s in detail_rev['steps'])


def test_hardening_guards_preserve_valid_news_and_block_extreme_inputs(tmp_path, monkeypatch):
    # 1. Malformed URL falls back safely instead of crashing urlsplit; normal text preserved; >24k capped.
    assert canonicalize_url('http://[::1/bad-url') == 'http://[::1/bad-url'
    assert len(clean_text('A' * 30000)) == 24000
    assert clean_text('Normal Lakers and 76ers NBA trade report.') == 'Normal Lakers and 76ers NBA trade report.'

    # 2. Invalid max_items raises clean AppError (422), not ValueError (500).
    for bad_max in ('abc', True, 0, 999):
        with pytest.raises(AppError, match='max_items'):
            validate_source_definition(
                {'source_id': 'rss_bad', 'name': 'Bad', 'type': 'rss', 'endpoint': 'https://news.example/rss', 'config': {'max_items': bad_max}},
                tmp_path,
            )

    # 3. Redirect to loopback/private IP is blocked before second request.
    import app.connectors as conn_mod

    seen_urls = []

    def fake_get(url, headers=None, timeout=15.0, follow_redirects=False):
        seen_urls.append(url)
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1:8080/secret'})

    monkeypatch.setattr(conn_mod.httpx, 'get', fake_get)
    monkeypatch.setattr(conn_mod.socket, 'getaddrinfo', lambda host, port=None: [(2, 1, 6, '', ('93.184.216.34', 0))])
    rss_live = RssConnector(max_attempts=1)
    with pytest.raises(AppError, match='Loopback'):
        rss_live.fetch({'source_id': 'redir', 'endpoint': 'https://public.example/rss', 'config': {}})
    assert seen_urls == ['https://public.example/rss']

    # 3b. TUN/Fake-IP DNS pool (198.18.0.0/15) is allowed for hostnames while LAN DNS (192.168.x.x) is blocked.
    monkeypatch.setattr(conn_mod.socket, 'getaddrinfo', lambda host, port=None: [(2, 1, 6, '', ('198.18.0.11', 0))])
    conn_mod._validate_http_url('https://news.google.com/rss', resolve_dns=True)
    monkeypatch.setattr(conn_mod.socket, 'getaddrinfo', lambda host, port=None: [(2, 1, 6, '', ('192.168.1.10', 0))])
    with pytest.raises(AppError, match='Private network'):
        conn_mod._validate_http_url('https://rebind.example/rss', resolve_dns=True)

    # 4. ReplayConnector degrades gracefully per-item and records skip stats without dropping valid items.
    bad_dir = tmp_path / 'fixtures' / 'badjson'
    bad_dir.mkdir(parents=True)
    (bad_dir / 'all.json').write_text('{not valid json', encoding='utf-8')
    replay = ReplayConnector(tmp_path / 'fixtures')
    with pytest.raises(AppError, match='Malformed replay JSON'):
        replay.fetch({'source_id': 'badjson', 'endpoint': 'replay://badjson', 'config': {'corpus_path': 'badjson'}}, batch='all')

    mixed_dir = tmp_path / 'fixtures' / 'mixed'
    mixed_dir.mkdir(parents=True)
    mixed_records = [
        'not-a-dict',
        {'title': 'No timestamp item', 'content': 'Missing date.', 'published_at': 'bad-date'},
        {'title': '', 'content': '', 'published_at': '2026-07-01T12:00:00Z'},
        {
            'source_id': 'wire',
            'title': 'Valid item without external_id',
            'content': 'Lakers acquire Walker Kessler in sign-and-trade.',
            'published_at': '2026-07-01T18:00:00Z',
        },
        {
            'source_id': 'wire',
            'external_id': 'future-1',
            'title': 'Future leak item',
            'content': 'Should be filtered by as_of.',
            'published_at': '2026-08-01T00:00:00Z',
        },
    ]
    (mixed_dir / 'all.json').write_text(json.dumps(mixed_records), encoding='utf-8')
    items = replay.fetch(
        {'source_id': 'mixed_src', 'endpoint': 'replay://mixed', 'config': {'corpus_path': 'mixed'}},
        batch='all',
        as_of='2026-07-15T00:00:00Z',
    )
    assert len(items) == 1
    assert items[0]['external_id'].startswith('wire:replay-')
    assert replay.last_stats == {
        'returned': 1,
        'future_filtered': 1,
        'skipped_invalid_timestamp': 1,
        'skipped_invalid_items': 2,
    }

    # 5. Direct ManualConnector guards catch blank HTML-only payloads, as_of leakage, and bad timestamps.
    from app.connectors import ManualConnector

    manual = ManualConnector()
    manual_src = {'source_id': 'manual', 'type': 'manual', 'endpoint': 'manual://user', 'config': {}}
    with pytest.raises(AppError, match='Invalid published_at timestamp'):
        manual.fetch(manual_src, manual_items=[{'title': 'Bad date', 'content': 'Body', 'published_at': 'not-a-timestamp'}])

    manual_out = manual.fetch(
        manual_src,
        as_of='2026-07-15T00:00:00Z',
        manual_items=[
            {'title': '<p>  </p>', 'content': '<script>x=1</script>'},
            {'title': 'Future manual', 'content': 'Should wait.', 'published_at': '2026-08-01T00:00:00Z'},
            {'title': 'Valid manual', 'content': 'Additional trade context.', 'published_at': '2026-07-02T10:00:00Z'},
        ],
    )
    assert len(manual_out) == 1
    assert manual.last_stats == {'returned': 1, 'future_filtered': 1, 'skipped_invalid_items': 1}

    # 6. Run Trace exposes fetch_source stats and API validates malformed manual_items at boundary with 422.
    settings = Settings(database_path=str(tmp_path / 'trace.db'), corpus_root=tmp_path / 'fixtures')
    with TestClient(create_app(settings)) as client:
        topic = client.post('/api/topics', json={'title': 'Lakers roster moves', 'description': 'Track trades'}).json()
        tid = topic['topic_id']
        assert client.post('/api/sources', json={
            'source_id': 'mixed_src',
            'name': 'Mixed replay',
            'type': 'replay',
            'endpoint': 'replay://mixed',
            'config': {'corpus_path': 'mixed'},
        }).status_code == 201
        assert client.post('/api/sources', json={
            'source_id': 'manual',
            'name': 'Manual input',
            'type': 'manual',
            'endpoint': 'manual://user',
            'config': {},
        }).status_code == 201
        assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['mixed_src', 'manual']}).status_code == 200

        bad_api_resp = client.post(f'/api/topics/{tid}/runs', json={
            'fixture_batch_id': 'all',
            'provider': 'mock',
            'manual_items': [{'title': '', 'content': ''}],
        })
        assert bad_api_resp.status_code == 422

        run_resp = client.post(f'/api/topics/{tid}/runs', json={
            'fixture_batch_id': 'all',
            'provider': 'mock',
            'as_of': '2026-07-15T00:00:00Z',
            'manual_items': [
                {'title': 'Valid manual', 'content': 'Additional trade context.', 'published_at': '2026-07-02T10:00:00Z'},
            ],
        })
        assert run_resp.status_code == 202
        detail = _wait_run(client, run_resp.json()['run_id'])
        fetch_steps = [s for s in detail['steps'] if s['step_name'] == 'fetch_source']
        mixed_trace = next(s for s in fetch_steps if s['decision'].get('source_id') == 'mixed_src')
        assert mixed_trace['decision']['stats']['future_filtered'] == 1
        assert mixed_trace['decision']['stats']['skipped_invalid_timestamp'] == 1
        assert mixed_trace['decision']['stats']['skipped_invalid_items'] == 2
        manual_trace = next(s for s in fetch_steps if s['decision'].get('source_id') == 'manual')
        assert manual_trace['decision']['stats']['returned'] == 1


def test_boolean_replay_and_zero_token_branch_discovery_and_noise_audit():
    from pathlib import Path
    from app.connectors import ReplayConnector, recommend_sources

    corpus_root = Path(__file__).resolve().parents[2] / 'fixtures'
    replay = ReplayConnector(corpus_root)
    lbj_only = replay.fetch(
        {
            'source_id': 'lbj_anchor',
            'type': 'replay',
            'endpoint': 'replay://lbj_2026_daily_sim/all.json',
            'config': {'corpus_path': 'lbj_2026_daily_sim/all.json', 'query_any': ['lebron', 'james']},
        },
        'all',
        as_of='2026-07-03T23:59:59Z',
    )
    sixers_only = replay.fetch(
        {
            'source_id': 'kw_76ers',
            'type': 'replay',
            'endpoint': 'replay://lbj_2026_daily_sim/all.json',
            'config': {'corpus_path': 'lbj_2026_daily_sim/all.json', 'query_any': ['76ers', 'sixers', 'philadelphia']},
        },
        'all',
        as_of='2026-07-03T23:59:59Z',
    )
    # The Jaylen Brown Sixers trade does NOT mention LeBron -> absent in lbj_only, present in sixers_only!
    assert not any('Jaylen Brown trade was a risk' in x['title'] for x in lbj_only)
    assert any('Jaylen Brown trade was a risk' in x['title'] for x in sixers_only)
    assert replay.last_stats['query_filtered'] > 0

    # Chronological zero-hindsight-bias candidate ordering: Timberwolves (06-30) before Warriors (07-02) before 76ers (07-03)
    topic = {'topic_id': '11223344-aaaa', 'title': 'LeBron James 2026 转会追踪', 'description': 'Track LBJ free agency'}
    decisions = [
        {
            'evidence': {'source_id': 'lbj_anchor_11223344', 'published_at': '2026-06-30T15:00:00Z',
                         'title': 'Timberwolves contact LeBron James', 'content': 'Minnesota Timberwolves contacted LeBron James.'},
            'status': 'completed', 'assessment': {'is_relevant': True},
        },
        {
            'evidence': {'source_id': 'lbj_anchor_11223344', 'published_at': '2026-07-02T14:00:00Z',
                         'title': 'Heat, Cavaliers, Golden State Warriors linked to LeBron', 'content': 'Warriors and Heat lead odds.'},
            'status': 'completed', 'assessment': {'is_relevant': True},
        },
        {
            'evidence': {'source_id': 'lbj_anchor_11223344', 'published_at': '2026-07-03T12:00:00Z',
                         'title': 'Rich Paul maps 76ers for LeBron', 'content': 'Philadelphia 76ers highlighted.'},
            'status': 'completed', 'assessment': {'is_relevant': True},
        },
    ]
    rec = recommend_sources(topic, [], current_events=[], evidence_decisions=decisions, corpus_root=corpus_root)
    branch_ids = [s['source_id'] for s in rec['sources'] if s['source_id'].startswith('kw_11223344_')]
    assert 'kw_11223344_timberwolves' in branch_ids
    assert 'kw_11223344_warriors' in branch_ids
    assert 'kw_11223344_76ers' in branch_ids

    # Silence vs. Noise Audit: silence = NO alert; 3 irrelevant = noise_alert; milestone = milestone_closed on non-winners!
    wolves_src = {
        'source_id': 'kw_11223344_timberwolves',
        'name': '支线候选：Minnesota Timberwolves',
        'type': 'replay',
        'endpoint': 'replay://lbj_2026_daily_sim/all.json',
        'config': {'corpus_path': 'lbj_2026_daily_sim/all.json', 'role': 'branch', 'branch_slug': 'timberwolves'},
    }
    warriors_src = {
        'source_id': 'kw_11223344_warriors',
        'name': '支线候选：Golden State Warriors',
        'type': 'replay',
        'endpoint': 'replay://lbj_2026_daily_sim/all.json',
        'config': {'corpus_path': 'lbj_2026_daily_sim/all.json', 'role': 'branch', 'branch_slug': 'warriors'},
    }
    # Quiet check: 0 items -> quiet, NOT retired
    quiet_rec = recommend_sources(topic, [wolves_src, warriors_src], current_events=[], evidence_decisions=[], corpus_root=corpus_root, approved_source_ids={'kw_11223344_timberwolves', 'kw_11223344_warriors'})
    w_quiet = next(s for s in quiet_rec['sources'] if s['source_id'] == 'kw_11223344_timberwolves')
    assert w_quiet['health_status'] == 'quiet' and not w_quiet['retirement_recommended']

    # Noise check: 3 irrelevant articles -> noise_alert
    noisy_decisions = [
        {'evidence': {'source_id': 'kw_11223344_timberwolves', 'published_at': f'2026-07-0{i}T12:00:00Z', 'title': f'Summer League {i}', 'content': 'Rookies'},
         'status': 'completed', 'assessment': {'is_relevant': False}}
        for i in (5, 8, 9)
    ]
    noisy_rec = recommend_sources(topic, [wolves_src, warriors_src], current_events=[], evidence_decisions=noisy_decisions, corpus_root=corpus_root, approved_source_ids={'kw_11223344_timberwolves', 'kw_11223344_warriors'})
    w_noisy = next(s for s in noisy_rec['sources'] if s['source_id'] == 'kw_11223344_timberwolves')
    assert w_noisy['health_status'] == 'noise_alert' and w_noisy['retirement_recommended'] is True

    # Milestone guard: Draymond Green re-signing with Warriors must NOT falsely close LeBron's free agency!
    draymond_only_events = [{'title': 'Warriors pursuit of LeBron and Draymond contract', 'version': 4, 'state': {'current_status': 'Draymond Green re-signed with the Golden State Warriors on a new deal.', 'key_facts': []}}]
    not_closed_rec = recommend_sources(topic, [warriors_src, wolves_src], current_events=draymond_only_events, evidence_decisions=[], corpus_root=corpus_root, approved_source_ids={'kw_11223344_warriors', 'kw_11223344_timberwolves'})
    wolves_not_closed = next(s for s in not_closed_rec['sources'] if s['source_id'] == 'kw_11223344_timberwolves')
    assert wolves_not_closed['health_status'] != 'milestone_closed' and wolves_not_closed['retirement_recommended'] is False

    # Milestone closure: LeBron picks Philadelphia -> Warriors flagged milestone_closed
    settled_events = [{'title': 'LeBron James Free Agency', 'version': 8, 'state': {'current_status': 'LeBron James picks Philadelphia 76ers as final NBA challenge', 'key_facts': []}}]
    closed_rec = recommend_sources(topic, [warriors_src], current_events=settled_events, evidence_decisions=[], corpus_root=corpus_root, approved_source_ids={'kw_11223344_warriors'})
    gsw_closed = next(s for s in closed_rec['sources'] if s['source_id'] == 'kw_11223344_warriors')
    assert gsw_closed['health_status'] == 'milestone_closed' and gsw_closed['retirement_recommended'] is True

    # Crisp CJK+ASCII live Google News RSS anchor query (no 6-term AND lock to 0 results)
    blg_rec = recommend_sources(
        {'topic_id': 'bb112233-9999', 'title': 'BLG LPL 人员变动与成员近期娱乐动向（直播、采访、庆功宴）', 'description': 'LPL BLG roster and streams'},
        [],
    )
    blg_gnews = next(s for s in blg_rec['sources'] if s['source_id'] == 'gnews_bb112233')
    assert 'BLG+LPL' in blg_gnews['endpoint'] and 'hl=zh-CN' in blg_gnews['endpoint']

    # 0-Extra-Token key_fact grounding + McDonald's HK sub-campaign discovery + optional ON/OFF llm_summary
    mcd_topic = {'topic_id': 'aabbccdd-1111', 'title': '香港麥當勞 2026 優惠與新品追蹤', 'description': '追蹤香港麥當勞限時優惠、套票與聯乘'}
    mcd_events = [
        {
            'title': '香港麥當勞 2026 Pokémon 聯乘',
            'version': 1,
            'state': {
                'current_status': 'Pokémon 盲盒漢堡已推出',
                'key_facts': [{'fact': '香港麥當勞正式推出Pokémon聯乘盲盒漢堡，$39歎黑椒雞堡隨機附送比卡超包裝。', 'evidence_ids': ['e1']}],
            },
        }
    ]
    mcd_decisions = [
        {
            'evidence': {
                'source_id': 'mcd_anchor_aabbccdd',
                'published_at': '2026-11-11T10:00:00Z',
                'title': '麥當勞聯乘Pokémon盲盒漢堡及主題美食系列登場',
                'content': '香港麥當勞推出Pokémon盲盒漢堡，$39歎黑椒雞堡。',
            },
            'status': 'completed',
            'assessment': {'is_relevant': True},
        }
    ]
    mcd_rec = recommend_sources(
        mcd_topic, [], current_events=mcd_events, evidence_decisions=mcd_decisions, corpus_root=corpus_root
    )
    assert any(s['source_id'] == 'mcd_anchor_aabbccdd' and s['role'] == 'anchor' for s in mcd_rec['sources'])
    poke = next(s for s in mcd_rec['sources'] if s['source_id'] == 'kw_aabbccdd_pokemon')
    assert poke['role'] == 'branch'
    assert '比卡超包裝' in poke['fact_evidence']
    assert '比卡超包裝' in poke['reason']
    assert mcd_rec['llm_summary_enabled'] is False
