"""Deterministic unit & integration tests for web_fetch, WebSearchConnector (Bocha/Tavily/Free),
WebpageMonitorConnector (same-URL in-place diff revisions), RSS full-text enrichment, and search settings API.
"""
import json
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.connectors as conn_mod
import app.web_connectors as wc_mod
from app.connectors import ConnectorRouter, _validate_http_url, validate_source_definition
from app.core import AppError, Settings
from app.main import create_app
from app.web_connectors import _HtmlContentExtractor, _compute_webpage_diff, web_fetch


def _wait_run(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f'/api/runs/{run_id}').json()
        if detail['run']['status'] not in ('pending', 'running'):
            return detail
        time.sleep(0.03)
    raise TimeoutError(f'Run {run_id} timed out')


def test_html_extractor_prefers_article_and_parses_dates():
    sample = """
    <html>
      <head>
        <title>ExampleSoft Advisory</title>
        <meta property="article:published_time" content="2026-11-01T09:00:00Z" />
        <meta property="article:modified_time" content="2026-11-02T10:30:00Z" />
        <style>body { color: red; }</style>
        <script>alert('ignore me');</script>
      </head>
      <body>
        <nav>Home | About | Login</nav>
        <article>
          <h1>ExampleSoft Vulnerability A</h1>
          <p>A critical vulnerability affecting ExampleSoft Server 1.0 was disclosed today. Remote attackers may read private records if unpatched.</p>
          <p>Update: Patch 1.2 has been released and verified by the security team. All operators should upgrade immediately to mitigate exposure.</p>
        </article>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    ext = _HtmlContentExtractor()
    ext.feed(sample)
    title, text, pub, mod = ext.finalize()
    assert title == 'ExampleSoft Advisory'
    assert 'Home | About' not in text
    assert 'alert(' not in text
    assert 'Patch 1.2 has been released' in text
    assert pub == '2026-11-01T09:00:00Z'
    assert mod == '2026-11-02T10:30:00Z'


def test_web_fetch_ssrf_blocks_before_http_or_jina(monkeypatch):
    calls = []
    monkeypatch.setattr(wc_mod.httpx, 'stream', lambda *a, **kw: calls.append(a))
    for bad in (
        'http://127.0.0.1:8080/secret',
        'http://169.254.169.254/latest/meta-data',
        'http://192.168.1.5/internal',
        'http://user:pass@public.example/page',
        'http://metadata.google.internal/computeMetadata/v1/',
        'http://intranet.corp/secret',
        'http://singlelabel/secret',
        'http://example.com:99999/badport',
    ):
        with pytest.raises(AppError):
            web_fetch(bad, validate_url=_validate_http_url, allow_jina_fallback=True)
    assert calls == []

    # Unresolved DNS fails closed before any HTTP or Jina call
    monkeypatch.setattr(conn_mod.socket, 'getaddrinfo', lambda *a, **kw: (_ for _ in ()).throw(conn_mod.socket.gaierror('no dns')))
    with pytest.raises(AppError):
        web_fetch('https://nonexistent.invalid/page', validate_url=_validate_http_url, allow_jina_fallback=True)
    assert calls == []

    # 401/404 and ?token=... URLs must NEVER fall back to r.jina.ai; cross-origin redirects strip Authorization
    monkeypatch.setattr(conn_mod.socket, 'getaddrinfo', lambda host, port=None: [(2, 1, 6, '', ('93.184.216.34', 0))])
    seen_reqs = []

    class _DummyStream:
        def __init__(self, status_code: int, headers: dict | None = None, body: bytes = b''):
            self.status_code = status_code
            self.headers = headers or {}
            self._body = body
            self.encoding = 'utf-8'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self):
            if self._body:
                yield self._body

    def fake_stream(method, url, headers=None, timeout=10.0, follow_redirects=False):
        seen_reqs.append((url, dict(headers or {})))
        if 'auth401' in url:
            return _DummyStream(401, {'content-type': 'text/html'}, b'Unauthorized')
        if 'token=secret' in url:
            return _DummyStream(403, {'content-type': 'text/html'}, b'Forbidden')
        if 'redir-start' in url:
            return _DummyStream(302, {'location': 'https://other-origin.example/final'})
        return _DummyStream(200, {'content-type': 'text/plain'}, b'OK final body ' * 20)

    monkeypatch.setattr(wc_mod.httpx, 'stream', fake_stream)
    r401 = web_fetch('https://public.example/auth401', validate_url=_validate_http_url, allow_jina_fallback=True)
    rtok = web_fetch('https://public.example/doc?token=secret', validate_url=_validate_http_url, allow_jina_fallback=True)
    assert r401['ok'] is False and rtok['ok'] is False
    assert all('r.jina.ai' not in u for u, _ in seen_reqs)

    seen_reqs.clear()
    wc_mod._stream_bounded_get(
        'https://origin-a.example/redir-start',
        headers={'Authorization': 'Bearer super-secret', 'User-Agent': 'test'},
        timeout=5.0,
        validate_url=_validate_http_url,
    )
    assert len(seen_reqs) == 2
    assert 'Authorization' in seen_reqs[0][1]
    assert 'Authorization' not in seen_reqs[1][1]


def test_web_search_validation_and_bocha_adapter(tmp_path, monkeypatch):
    for bad_src in (
        {'source_id': 'ws1', 'name': 'x', 'type': 'web_search', 'endpoint': 'search://', 'config': {}},
        {'source_id': 'ws2', 'name': 'x', 'type': 'web_search', 'endpoint': 'search://ok', 'config': {'max_items': True}},
        {'source_id': 'ws3', 'name': 'x', 'type': 'web_search', 'endpoint': 'search://ok', 'config': {'after_date': '2026-02-31'}},
        {'source_id': 'ws4', 'name': 'x', 'type': 'web_search', 'endpoint': 'search://ok', 'config': {'search_provider': 'evil'}},
        {'source_id': 'wp1', 'name': 'x', 'type': 'webpage', 'endpoint': 'http://127.0.0.1/admin', 'config': {}},
        {'source_id': 'wp2', 'name': 'x', 'type': 'webpage', 'endpoint': 'https://example.com', 'config': {'max_chars': 999999}},
    ):
        with pytest.raises(AppError):
            validate_source_definition(bad_src, tmp_path)

    captured = []

    def fake_post(url, headers=None, json=None, timeout=20.0, **kw):
        captured.append((url, headers, json))
        return httpx.Response(
            200,
            json={
                'code': 200,
                'data': {
                    'webPages': {
                        'value': [
                            {
                                'name': 'OpenAI & Anthropic publish competing proofs',
                                'url': 'https://science.example/p-vs-np-2026',
                                'summary': 'Detailed summary exceeding five hundred characters ' + ('word ' * 110),
                                'snippet': 'Short snippet',
                                'datePublished': '2026-09-15T08:00:00+08:00',
                            },
                            {
                                'name': 'Old pre-cutoff article',
                                'url': 'https://science.example/old-2025',
                                'summary': 'Old summary text.',
                                'datePublished': '2025-05-01T00:00:00Z',
                            },
                        ]
                    }
                },
            },
        )

    monkeypatch.setattr(wc_mod.httpx, 'post', fake_post)
    st = Settings(
        database_path=str(tmp_path / 't.db'),
        search_provider='auto',
        bocha_api_key='sk-test-bocha-key',
        bocha_base_url='https://api.bochaai.com/v1',
    )
    router = ConnectorRouter(st.fixture_path, st.corpus_root, settings=st)
    items = router.fetch(
        {
            'source_id': 'ws_pnp',
            'name': 'PNP',
            'type': 'web_search',
            'endpoint': 'search://P+vs+NP',
            'config': {'query': 'OpenAI Anthropic 千禧年难题 P vs NP', 'after_date': '2026-07-01', 'max_items': 5, 'fetch_full_text': True},
        },
        'live',
        existing_items=[],
    )
    assert len(items) == 1
    assert items[0]['url'] == 'https://science.example/p-vs-np-2026'
    assert router.last_fetch_stats['provider_used'] == 'bocha'
    assert router.last_fetch_stats['fulltext_attempted'] == 0  # Bocha rich summary >= 480 chars skipped redundant fetch
    assert captured[0][0] == 'https://api.bochaai.com/v1/web-search'
    assert captured[0][1]['Authorization'] == 'Bearer sk-test-bocha-key'

    # Auto mode routes explicit English queries away from China-only Bocha unless Tavily is configured
    eff_en, _ = wc_mod._resolve_active_search_provider(st, {'query': 'OpenAI P vs NP', 'search_language': 'en-US'})
    eff_zh, _ = wc_mod._resolve_active_search_provider(st, {'query': '香港麦当劳 优惠', 'search_language': 'zh-HK'})
    eff_forced, _ = wc_mod._resolve_active_search_provider(st, {'query': 'OpenAI P vs NP', 'search_provider': 'bocha'})
    assert eff_en == 'free'
    assert eff_zh == 'bocha'
    assert eff_forced == 'bocha'


def test_webpage_monitor_inplace_same_url_update_advances_state(tmp_path):
    changed, diff_body, added, removed = _compute_webpage_diff(
        'Line one intro\nVulnerability disclosed in v1.0',
        'Line one intro\nVulnerability disclosed in v1.0\nPatch released in version 1.2 today.',
    )
    assert changed is True and added == 1 and removed == 0 and 'Patch released in version 1.2' in diff_body

    pages = [
        {
            'ok': True,
            'url': 'https://vendor.example/advisories/cve-1',
            'title': 'ExampleSoft Security Advisory',
            'content': 'ExampleSoft vulnerability A disclosed.',
            'full_text': (
                'ExampleSoft Security Advisory\n'
                'Product: ExampleSoft Enterprise Server\n'
                'ExampleSoft vulnerability A disclosed.\n'
                'A remote attacker can access private records in version 1.0.\n'
                'Mitigation: Restrict access until patch is available.'
            ),
            'published_at': '2026-11-01T09:00:00Z',
            'modified_at': '2026-11-01T09:00:00Z',
            'method': 'direct_httpx',
            'error': None,
        },
        {
            'ok': True,
            'url': 'https://vendor.example/advisories/cve-1',
            'title': 'ExampleSoft Security Advisory',
            'content': 'ExampleSoft vulnerability A disclosed.',
            'full_text': (
                'ExampleSoft Security Advisory\n'
                'Product: ExampleSoft Enterprise Server\n'
                'ExampleSoft vulnerability A disclosed.\n'
                'A remote attacker can access private records in version 1.0.\n'
                'Mitigation: Restrict access until patch is available.'
            ),
            'published_at': '2026-11-01T09:00:00Z',
            'modified_at': '2026-11-01T09:00:00Z',
            'method': 'direct_httpx',
            'error': None,
        },
        {
            'ok': True,
            'url': 'https://vendor.example/advisories/cve-1',
            'title': 'ExampleSoft Security Advisory',
            'content': 'ExampleSoft vulnerability A patch released.',
            'full_text': (
                'ExampleSoft Security Advisory\n'
                'Product: ExampleSoft Enterprise Server\n'
                'ExampleSoft vulnerability A disclosed.\n'
                'A remote attacker can access private records in version 1.0.\n'
                'Mitigation: Restrict access until patch is available.\n'
                'Update 2026-11-02: ExampleSoft vulnerability A patch released in version 1.2.'
            ),
            'published_at': '2026-11-01T09:00:00Z',
            'modified_at': '2026-11-02T10:00:00Z',
            'method': 'direct_httpx',
            'error': None,
        },
    ]
    idx = {'n': 0}

    def fake_fetch(url, **kw):
        out = pages[idx['n']]
        idx['n'] += 1
        return out

    st = Settings(database_path=str(tmp_path / 'wp.db'), env_path=tmp_path / '.env', bocha_api_key='', tavily_api_key='')
    with TestClient(create_app(st), base_url='http://127.0.0.1:5173') as client:
        tid = client.post(
            '/api/topics',
            json={'title': 'ExampleSoft security', 'description': 'Track vulnerabilities and patches'},
        ).json()['topic_id']
        assert client.post(
            '/api/sources',
            json={
                'source_id': 'advisory_page',
                'name': 'Advisory Page',
                'type': 'webpage',
                'endpoint': 'https://vendor.example/advisories/cve-1',
                'config': {},
            },
        ).status_code == 201
        assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['advisory_page']}).status_code == 200

        with patch.object(wc_mod, 'web_fetch', side_effect=fake_fetch):
            d1 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
            d2 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
            d3 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])

        assert d1['run']['counts']['committed'] == 1
        assert d2['run']['counts']['fetched'] == 0
        assert d3['run']['counts']['committed'] == 1
        dash = client.get(f'/api/topics/{tid}/dashboard').json()
        assert len(dash['events']) == 1
        assert dash['events'][0]['state']['current_status'] == 'patched'
        assert dash['events'][0]['version'] == 2


def test_search_settings_api_privacy_and_origin_guard(tmp_path):
    env_p = tmp_path / '.env'
    st = Settings(
        database_path=str(tmp_path / 's.db'),
        env_path=env_p,
        search_provider='auto',
        bocha_api_key='',
        tavily_api_key='',
    )
    with TestClient(create_app(st), base_url='http://127.0.0.1:5173') as client:
        r0 = client.get('/api/settings/search')
        assert r0.status_code == 200
        assert r0.json()['active_provider'] == 'free'
        assert r0.json()['bocha_configured'] is False

        # Cross-origin PUT blocked
        assert client.put(
            '/api/settings/search',
            json={'provider': 'bocha', 'bocha_api_key': 'sk-secret-value'},
            headers={'Origin': 'https://attacker.example'},
        ).status_code == 403

        # Local PUT saves to .env and never echoes secret
        r1 = client.put(
            '/api/settings/search',
            json={'provider': 'auto', 'bocha_api_key': 'sk-secret-value-12345'},
            headers={'Origin': 'http://127.0.0.1:5173'},
        )
        assert r1.status_code == 200
        body = r1.json()
        assert body['bocha_configured'] is True
        assert body['active_provider'] == 'bocha'
        assert 'sk-secret' not in json.dumps(body)
        # Non-ASCII key rejected 422 WITHOUT mutating in-memory settings (P1-5 atomicity)
        r_bad = client.put(
            '/api/settings/search',
            json={'provider': 'free', 'bocha_api_key': 'sk-非法字符'},
            headers={'Origin': 'http://127.0.0.1:5173'},
        )
        assert r_bad.status_code == 422
        assert client.get('/api/settings/search').json()['active_provider'] == 'bocha'
        from app.main import SearchSettingsInput
        assert 'sk-secret-repr' not in repr(SearchSettingsInput(bocha_api_key='sk-secret-repr'))


def test_redteam_p0_repeat_web_search_custom_source_and_webpage_revert(tmp_path, monkeypatch):
    """Regression coverage for Red-Team P0-1 (repeat web_search no SOURCE_REVISION), P0-2 (custom web_search
    stays visible for approval), P1-2 (RSS cannot spoof webpage-rev-), P1-3 (webpage A->B->A revert & failed-run
    recovery & CJK short diff & max_chars=None), and P1-4 (undated dropped under after_date).
    """
    # P1-3 short CJK line diff
    changed_cjk, diff_cjk, added_cjk, _ = _compute_webpage_diff('状态：调查中', '状态：调查中\n已修复')
    assert changed_cjk and added_cjk == 1 and '已修复' in diff_cjk

    # P1-4 undated results dropped when after_date is set
    monkeypatch.setattr(
        wc_mod.httpx,
        'post',
        lambda *a, **kw: httpx.Response(
            200,
            json={
                'code': 200,
                'data': {
                    'webPages': {
                        'value': [
                            {'name': 'Undated old wiki', 'url': 'https://wiki.example/old', 'summary': 'Old undated page'},
                            {'name': 'Dated fresh news', 'url': 'https://news.example/fresh', 'summary': 'ExampleSoft vulnerability A disclosed.', 'datePublished': '2026-11-01T09:00:00Z'},
                        ]
                    }
                },
            },
        ),
    )
    fetch_count = {'n': 0}

    def fake_fetch(url, **kw):
        fetch_count['n'] += 1
        return {
            'ok': True,
            'url': url,
            'title': 'Dated fresh news',
            'content': 'ExampleSoft vulnerability A disclosed. Full enriched text body with details on version 1.0 remote access.',
            'full_text': 'ExampleSoft vulnerability A disclosed. Full enriched text body with details on version 1.0 remote access.',
            'published_at': '2026-11-01T09:00:00Z',
            'modified_at': None,
            'method': 'direct_httpx',
            'error': None,
        }

    st = Settings(database_path=str(tmp_path / 'p0.db'), env_path=tmp_path / '.env', search_provider='bocha', bocha_api_key='sk-test')
    with TestClient(create_app(st), base_url='http://127.0.0.1:5173') as client:
        tid = client.post(
            '/api/topics',
            json={'title': 'ExampleSoft security', 'description': 'Track vulnerabilities and patches'},
        ).json()['topic_id']

        # P0-2: User adds custom web_search (role='branch') -> MUST stay visible in source-recommendations
        r_add = client.post(
            '/api/sources',
            json={
                'source_id': 'ws_custom',
                'name': '全网搜索：ExampleSoft',
                'type': 'web_search',
                'endpoint': 'search://ExampleSoft',
                'config': {'query': 'ExampleSoft 漏洞', 'after_date': '2026-10-01', 'fetch_full_text': True, 'role': 'branch', 'branch_label': 'ExampleSoft'},
            },
        )
        assert r_add.status_code == 201
        recs = client.post(f'/api/topics/{tid}/source-recommendations', json={}).json()
        assert any(s['source_id'] == 'ws_custom' for s in recs['sources'])
        assert client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['ws_custom']}).status_code == 200

        # P0-1: Run 1 enriches thin snippet -> full text; Run 2 skips fetch AND must NOT raise SOURCE_REVISION (422)!
        with patch.object(wc_mod, 'web_fetch', side_effect=fake_fetch):
            d1 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
            d2 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
        assert d1['run']['status'] == 'completed' and d1['run']['counts']['committed'] == 1
        assert d2['run']['status'] == 'completed' and d2['run']['counts']['skipped'] == 1 and d2['run']['counts']['errors'] == 0
        assert fetch_count['n'] == 1  # Run 2 skipped web_fetch completely!

        # P1-3: Webpage revert A -> B -> A + config max_chars=None (must NOT trigger SOURCE_REVISION!)
        states = [
            {'ok': True, 'url': 'https://vendor.example/status', 'title': 'Status', 'content': 'ExampleSoft vulnerability A disclosed.', 'full_text': 'Header line\nExampleSoft vulnerability A disclosed.\nStatus open.', 'published_at': '2026-11-01T09:00:00Z', 'modified_at': '2026-11-01T09:00:00Z', 'method': 'direct_httpx', 'error': None},
            {'ok': True, 'url': 'https://vendor.example/status', 'title': 'Status', 'content': 'ExampleSoft vulnerability A affected versions expanded to 1.0-1.1.', 'full_text': 'Header line\nExampleSoft vulnerability A disclosed.\nExampleSoft vulnerability A affects versions 1.0 through 1.1.', 'published_at': '2026-11-01T09:00:00Z', 'modified_at': '2026-11-02T09:00:00Z', 'method': 'direct_httpx', 'error': None},
            # Revert back to A!
            {'ok': True, 'url': 'https://vendor.example/status', 'title': 'Status', 'content': 'ExampleSoft vulnerability A disclosed.', 'full_text': 'Header line\nExampleSoft vulnerability A disclosed.\nStatus open.', 'published_at': '2026-11-01T09:00:00Z', 'modified_at': '2026-11-03T09:00:00Z', 'method': 'direct_httpx', 'error': None},
        ]
        s_idx = {'i': 0}

        def fake_revert_fetch(url, **kw):
            out = states[s_idx['i']]
            s_idx['i'] += 1
            return out

        assert client.post(
            '/api/sources',
            json={'source_id': 'wp_revert', 'name': 'Status Page', 'type': 'webpage', 'endpoint': 'https://vendor.example/status', 'config': {'max_chars': None}},
        ).status_code == 201
        tid2 = client.post('/api/topics', json={'title': 'ExampleSoft security', 'description': 'Track vulnerabilities and patches'}).json()['topic_id']
        client.put(f'/api/topics/{tid2}/sources', json={'approved_source_ids': ['wp_revert']})
        with patch.object(wc_mod, 'web_fetch', side_effect=fake_revert_fetch):
            r1 = _wait_run(client, client.post(f'/api/topics/{tid2}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
            r2 = _wait_run(client, client.post(f'/api/topics/{tid2}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
            r3 = _wait_run(client, client.post(f'/api/topics/{tid2}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
        assert r1['run']['status'] == 'completed' and r1['run']['counts']['errors'] == 0
        assert r2['run']['status'] == 'completed' and r2['run']['counts']['errors'] == 0
        assert r3['run']['status'] == 'completed' and r3['run']['counts']['errors'] == 0  # NO SOURCE_REVISION on A->B->A!

        # P1-2: Spoofed webpage-rev- external_id on non-webpage source does NOT bypass canonical_url dedup
        from app.evidence_processing import find_duplicate_match
        existing = [{'evidence_id': 'e1', 'canonical_evidence_id': 'e1', 'url': 'https://news.example/story', 'title': 'Same headline news story', 'content': 'ExampleSoft released patch version 1.2 today for enterprise users.', 'content_hash': 'h1'}]
        spoof = {'external_id': 'webpage-rev-fake', 'url': 'https://news.example/story', 'title': 'Same headline news story', 'content': 'ExampleSoft released patch version 1.2 today for enterprise users!!', 'content_hash': 'h2'}
        assert find_duplicate_match(spoof, existing, enable_near_duplicate=True) is not None


def test_e2e_polish_cross_source_url_dedup_entity_clean_and_query_boundary(tmp_path, monkeypatch):
    """Regression tests for E2E findings #2 (cross-source same URL reuses full-text hash -> exact_hash skip),
    #5 ([原文摘录] & generic CJK verbs filtered + '两大AI厂商 P vs NP' anchor prefix intact),
    and #6 (_clean_gnews_query truncates at word boundary instead of mid-word).
    """
    from app.connectors import _clean_gnews_query, _extract_general_terms, recommend_sources

    # #5: template marker and generic CJK verbs excluded
    terms = _extract_general_terms('简要总结\n\n[原文摘录]\nOpenAI 宣称解决并证明指数时间假设为假。')
    assert '原文摘录' not in terms
    assert not any(w in terms for w in ('为假', '解决', '证明'))
    assert 'OpenAI' in terms

    # #5: anchor_prefix does not chop '两大AI厂商 P vs NP' at 'P'
    recs = recommend_sources(
        {'topic_id': '11223344-pnp', 'title': '两大AI厂商 P vs NP 与千禧年难题进展', 'description': '追踪 OpenAI Anthropic P vs NP'},
        [],
        current_events=[],
        evidence_decisions=[
            {
                'status': 'completed',
                'assessment': {'is_relevant': True},
                'evidence': {
                    'source_id': 'ws_1',
                    'published_at': '2026-11-01T00:00:00Z',
                    'title': 'OpenAI publishes Navier-Stokes proof draft',
                    'content': 'OpenAI and Anthropic researchers discuss Navier-Stokes and Lean 4 formalization.',
                },
            }
        ],
    )
    branch_queries = [
        (s.get('config') or {}).get('query', '')
        for s in recs['sources']
        if s.get('role') == 'branch' and s.get('type') == 'web_search'
    ]
    assert branch_queries
    assert all('两大AI厂商 P ' not in q or '两大AI厂商 P vs NP' in q for q in branch_queries), branch_queries

    # #6: word-boundary truncation never cuts mid-word ('mathemati')
    long_q = 'Navier-Stokes partial solution verification OpenAI Anthropic mathematicians peer review 2026'
    cleaned_80 = _clean_gnews_query(long_q, 80)
    assert len(cleaned_80) <= 80
    assert not cleaned_80.endswith('mathemati')
    assert 'mathematicians' in cleaned_80
    cleaned_60 = _clean_gnews_query('Navier-Stokes partial solution verification OpenAI mathematicians', 60)
    assert cleaned_60 == 'Navier-Stokes partial solution verification OpenAI'

    # #2: Cross-source same URL skips web_fetch AND hits exact_hash dedup (0 redundant LLM assessment!)
    shared_url = 'https://cnbc.example/2026/09/09/openai-navier-stokes-math'
    full_body = 'ExampleSoft vulnerability A disclosed. ' + ('Detailed full article body paragraph text. ' * 60)
    monkeypatch.setattr(
        wc_mod.httpx,
        'post',
        lambda *a, **kw: httpx.Response(
            200,
            json={
                'code': 200,
                'data': {
                    'webPages': {
                        'value': [
                            {
                                'name': 'OpenAI Navier-Stokes report',
                                'url': shared_url,
                                'summary': 'ExampleSoft vulnerability A disclosed. Short 120-char snippet.',
                                'datePublished': '2026-11-01T09:00:00Z',
                            }
                        ]
                    }
                },
            },
        ),
    )
    fetch_calls = {'n': 0}

    def fake_fetch(url, **kw):
        fetch_calls['n'] += 1
        return {
            'ok': True,
            'url': url,
            'title': 'OpenAI Navier-Stokes report',
            'content': full_body,
            'full_text': full_body,
            'published_at': '2026-11-01T09:00:00Z',
            'modified_at': None,
            'method': 'direct_httpx',
            'error': None,
        }

    st = Settings(database_path=str(tmp_path / 'cross.db'), env_path=tmp_path / '.env', search_provider='bocha', bocha_api_key='sk-test')
    with TestClient(create_app(st), base_url='http://127.0.0.1:5173') as client:
        tid = client.post('/api/topics', json={'title': 'ExampleSoft security', 'description': 'Track vulnerabilities'}).json()['topic_id']
        client.post('/api/sources', json={'source_id': 'ws_src_a', 'name': 'Src A', 'type': 'web_search', 'endpoint': 'search://a', 'config': {'query': '千禧年 A', 'fetch_full_text': True}})
        client.post('/api/sources', json={'source_id': 'ws_src_b', 'name': 'Src B', 'type': 'web_search', 'endpoint': 'search://b', 'config': {'query': '千禧年 B', 'fetch_full_text': True}})
        client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['ws_src_a']})
        with patch.object(wc_mod, 'web_fetch', side_effect=fake_fetch):
            r1 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
            assert r1['run']['counts']['committed'] == 1 and fetch_calls['n'] == 1

            # Round 2 adds second source ws_src_b returning the SAME URL with a short snippet
            client.put(f'/api/topics/{tid}/sources', json={'approved_source_ids': ['ws_src_b']})
            r2 = _wait_run(client, client.post(f'/api/topics/{tid}/runs', json={'fixture_batch_id': 'live', 'provider': 'mock'}).json()['run_id'])
        assert r2['run']['status'] == 'completed'
        assert r2['run']['counts']['skipped'] == 1
        assert r2['run']['counts']['committed'] == 0 and r2['run']['counts']['no_change'] == 0 and r2['run']['counts']['errors'] == 0
        assert fetch_calls['n'] == 1  # No redundant web_fetch AND no redundant LLM assessment!


def test_watchlist_no_mixed_lang_concat_ai_verdict_ranking_and_ja_ko_routing(monkeypatch):
    """Verify: (1) English entities on a mixed CJK+EN topic get clean English anchor prefix & en-US tag instead of
    Frankenstein CJK+EN concatenation; (2) AI advisor floats 'approve' & AI-suggested queries to top and sinks 'skip'
    to bottom without hiding any candidate; (3) Japanese/Korean queries with kanji still route away from China-only Bocha.
    """
    from app.connectors import recommend_sources

    # (3) JA/KO auto-routing even when Japanese query contains Kanji (\u4e00-\u9fff)
    st_bocha = Settings(search_provider='auto', bocha_api_key='sk-b', tavily_api_key='')
    st_both = Settings(search_provider='auto', bocha_api_key='sk-b', tavily_api_key='tvly-t')
    assert wc_mod._resolve_active_search_provider(st_bocha, {'query': 'AI 数学 ニュース'})[0] == 'free'
    assert wc_mod._resolve_active_search_provider(st_both, {'query': 'AI 数学 ニュース'})[0] == 'tavily'
    assert wc_mod._resolve_active_search_provider(st_both, {'query': 'GEN.G 리계약 2026'})[0] == 'tavily'
    assert wc_mod._resolve_active_search_provider(st_both, {'query': '香港麦当劳 优惠'})[0] == 'bocha'

    # (1) Mixed topic '两大AI厂商 P vs NP 与千禧年难题进展' -> English entity 'Exponential Time Hypothesis' gets 'P vs NP ...' (en-US)
    ev_decs = [
        {
            'status': 'completed',
            'assessment': {'is_relevant': True},
            'evidence': {
                'source_id': 'ws_1',
                'published_at': '2026-10-27T00:00:00Z',
                'title': 'Two AI systems disprove Exponential Time Hypothesis',
                'content': 'OpenAI and Anthropic systems independently disproved Exponential Time Hypothesis while 数学家 discussed 指数时间假设.',
            },
        }
    ]
    base_recs = recommend_sources(
        {'topic_id': '9e72d0b5-pnp', 'title': '两大AI厂商 P vs NP 与千禧年难题进展', 'description': '追踪 PNP 与千禧年难题'},
        [],
        current_events=[],
        evidence_decisions=ev_decs,
    )
    eth_en = next((s for s in base_recs['sources'] if (s.get('config') or {}).get('branch_label') == 'Exponential Time Hypothesis'), None)
    assert eth_en is not None
    assert eth_en['config']['query'] == 'P vs NP Exponential Time Hypothesis'
    assert eth_en['config']['search_language'] == 'en-US'

    # (2) AI advisor verdict ranking: AI-suggested & 'approve' float above 'skip' while keeping all candidates visible
    cands = [s for s in base_recs['sources'] if s.get('role') == 'branch' and s.get('health_status') == 'candidate']
    assert len(cands) >= 2
    skip_sid, approve_sid = cands[0]['source_id'], cands[1]['source_id']
    fake_ai = (
        {
            'summary_note': '优先批准核心候选，跳过泛化项。',
            'per_source': {
                skip_sid: '信息过泛，建议跳过。',
                approve_sid: '属核心进展，建议批准。',
            },
            'verdicts': {
                skip_sid: 'skip',
                approve_sid: 'approve',
            },
            'retire_branches': [],
            'suggested_queries': [
                {'label': 'Valiant conjecture', 'query': 'Valiant conjecture proof verification', 'language': 'en-US', 'reason': '核心验证方向'}
            ],
        },
        {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150},
        None,
    )
    monkeypatch.setattr(conn_mod, '_call_watchlist_llm', lambda *a, **kw: fake_ai)
    ai_recs = recommend_sources(
        {'topic_id': '9e72d0b5-pnp', 'title': '两大AI厂商 P vs NP 与千禧年难题进展', 'description': '追踪 PNP 与千禧年难题'},
        [],
        current_events=[],
        evidence_decisions=ev_decs,
        llm_summary=True,
        settings=Settings(api_key='k', base_url='https://api.example.com/v1', model='m'),
    )
    ai_cands = [s for s in ai_recs['sources'] if s.get('role') == 'branch' and s.get('health_status') == 'candidate']
    ids_order = [s['source_id'] for s in ai_cands]
    ai_sugg_sid = next(sid for sid in ids_order if '_ai_' in sid)
    assert skip_sid in ids_order and approve_sid in ids_order  # Nothing hidden/deleted!
    assert ids_order.index(ai_sugg_sid) < ids_order.index(skip_sid)
    assert ids_order.index(approve_sid) < ids_order.index(skip_sid)
    by_id = {s['source_id']: s for s in ai_cands}
    assert by_id[approve_sid]['ai_verdict'] == 'approve'
    assert by_id[skip_sid]['ai_verdict'] == 'skip'
    assert by_id[ai_sugg_sid]['ai_verdict'] == 'approve'



