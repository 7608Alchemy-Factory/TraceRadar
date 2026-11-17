"""Source connectors (Fixture, time-sliced Replay, RSS/Atom, Manual) and topic source recommendations."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import time
from typing import Protocol
from urllib.parse import quote_plus, urljoin, urlsplit
import xml.etree.ElementTree as ET
import httpx

from .core import AppError
from .domain import Evidence, now, uid
from .evidence_processing import (
    canonicalize_url,
    clean_text,
    compute_content_hash,
    parse_published_at,
    tokenize,
)

SOURCES = [
    dict(
        source_id='vendor',
        name='ExampleSoft advisory (simulated)',
        type='fixture',
        endpoint='fixture://vendor',
        config={'tier': 'official'},
    ),
    dict(
        source_id='research',
        name='Security research (simulated)',
        type='fixture',
        endpoint='fixture://research',
        config={'tier': 'media'},
    ),
]


class Connector(Protocol):
    def fetch(self, source: dict, batch: str, **kwargs) -> list[dict]: ...


def _to_utc_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = parse_published_at(value)
    if not parsed:
        raise ValueError(f'Invalid timestamp: {value}')
    return datetime.fromisoformat(parsed.replace('Z', '+00:00'))


def _build_evidence(
    *,
    source_id: str,
    external_id: str,
    url: str,
    title: str,
    content: str,
    published_at: str,
) -> dict:
    cleaned_title = clean_text(title) or 'Untitled update'
    cleaned_content = clean_text(content) or cleaned_title
    normalized_url = canonicalize_url(url) or url.strip() or f'urn:evidence:{source_id}:{external_id}'
    identity = uid()
    payload = {
        'evidence_id': identity,
        'source_id': source_id,
        'external_id': external_id,
        'url': normalized_url,
        'title': cleaned_title,
        'content': cleaned_content,
        'published_at': published_at,
        'fetched_at': now(),
        'content_hash': compute_content_hash(cleaned_content),
        'canonical_evidence_id': identity,
    }
    return Evidence.model_validate(payload).model_dump(mode='json')


def _safe_corpus_path(corpus_root: Path, relative_path: str) -> Path:
    root = corpus_root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise AppError('INVALID_SOURCE', 'Corpus path must stay within fixtures directory.', 422)
    return candidate


_MAX_CORPUS_BYTES = 20 * 1024 * 1024
_MAX_FEED_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5
_BLOCKED_HOSTS = {'localhost', '127.0.0.1', '::1', '0.0.0.0'}
# Desktop TUN/Fake-IP proxies (e.g. Clash/Mihomo) map public domains onto 198.18.0.0/15.
_FAKE_IP_BENCHMARK_NET = ipaddress.ip_network('198.18.0.0/15')


def _is_blocked_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_fake_ip_dns: bool = False) -> bool:
    if allow_fake_ip_dns and ip_obj.version == 4 and ip_obj in _FAKE_IP_BENCHMARK_NET:
        return False
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def _validate_http_url(url: str, *, resolve_dns: bool = False) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise AppError('INVALID_SOURCE', f'Invalid RSS URL: {exc}', 422)
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('http', 'https') or not host:
        raise AppError('INVALID_SOURCE', 'RSS endpoint must be an http(s) URL with a valid host.', 422)
    if host in _BLOCKED_HOSTS or host.endswith('.localhost') or host.endswith('.local'):
        raise AppError('INVALID_SOURCE', 'Loopback RSS endpoints are not permitted.', 422)
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise AppError('INVALID_SOURCE', 'Private network RSS endpoints are not permitted.', 422)
        return
    except ValueError:
        pass
    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return
        for info in infos:
            addr = info[4][0].split('%', 1)[0]
            try:
                resolved = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _is_blocked_ip(resolved, allow_fake_ip_dns=True):
                raise AppError('INVALID_SOURCE', 'Private network RSS endpoints are not permitted.', 422)


def _parse_max_items(config: dict) -> int:
    raw_max = config.get('max_items', 15)
    if isinstance(raw_max, bool):
        raise AppError('INVALID_SOURCE', 'RSS max_items must be an integer between 1 and 50.', 422)
    try:
        max_items = int(raw_max)
    except (TypeError, ValueError):
        raise AppError('INVALID_SOURCE', 'RSS max_items must be an integer between 1 and 50.', 422)
    if max_items < 1 or max_items > 50:
        raise AppError('INVALID_SOURCE', 'RSS max_items must be between 1 and 50.', 422)
    return max_items


def validate_source_definition(source: dict, corpus_root: Path) -> dict:
    """Validate source configuration boundaries before registering in the repository."""
    stype = source.get('type', 'fixture')
    endpoint = str(source.get('endpoint', '')).strip()
    config = dict(source.get('config') or {})
    if stype == 'rss':
        _validate_http_url(endpoint, resolve_dns=False)
        config['max_items'] = _parse_max_items(config)
    elif stype == 'replay':
        rel = str(config.get('corpus_path') or endpoint.removeprefix('replay://')).strip()
        if not rel:
            raise AppError('INVALID_SOURCE', 'Replay source requires corpus_path or replay:// path.', 422)
        target = _safe_corpus_path(corpus_root, rel)
        if not target.exists():
            raise AppError('INVALID_SOURCE', f'Replay corpus path not found: {rel}', 422)
        config['corpus_path'] = rel
    elif stype == 'fixture':
        if source.get('source_id') not in {s['source_id'] for s in SOURCES}:
            raise AppError('INVALID_SOURCE', 'Only built-in simulated sources may use type=fixture.', 422)
    elif stype == 'manual':
        pass
    else:
        raise AppError('INVALID_SOURCE', f'Unsupported source type: {stype}', 422)
    return dict(source, config=config)


class FixtureConnector:
    def __init__(self, path: Path):
        self.path = path
        self.last_stats: dict = {}

    def fetch(self, source: dict, batch: str, **kwargs) -> list[dict]:
        # File path comes exclusively from the registry and validated batch enum.
        if source['source_id'] not in {s['source_id'] for s in SOURCES}:
            raise AppError('INVALID_SOURCE', f"Unknown fixture source: {source['source_id']}", 422)
        if batch not in {'1', '2', '3', 'empty'}:
            raise AppError('INVALID_FIXTURE_BATCH', f"Fixture source '{source['source_id']}' only supports batches 1, 2, 3, or empty (got {batch!r}).", 422)
        data = json.loads((self.path / f'{batch}.json').read_text(encoding='utf-8'))
        result = []
        for item in data:
            if item['source_id'] != source['source_id']:
                continue
            item = dict(item)
            item['content'] = ' '.join(item['content'].split())
            identity = uid()
            item.update(
                evidence_id=identity,
                canonical_evidence_id=identity,
                fetched_at=now(),
                content_hash=hashlib.sha256(item['content'].encode()).hexdigest(),
            )
            result.append(Evidence.model_validate(item).model_dump(mode='json'))
        self.last_stats = {'returned': len(result)}
        return sorted(result, key=lambda e: (e['published_at'], e['external_id']))


class ReplayConnector:
    """Time-sliced historical stream replay connector with strict no-future-leakage cutoff."""

    def __init__(self, corpus_root: Path):
        self.corpus_root = corpus_root
        self.last_stats: dict = {}

    def _read_bounded_records(self, file_path: Path, *, is_jsonl: bool = False) -> list:
        size = file_path.stat().st_size
        if size > _MAX_CORPUS_BYTES:
            raise AppError('SOURCE_PARSE_ERROR', f'Replay corpus exceeds {_MAX_CORPUS_BYTES // (1024 * 1024)} MB limit.', 422)
        text = file_path.read_text(encoding='utf-8')
        try:
            if is_jsonl:
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError('SOURCE_PARSE_ERROR', f'Malformed replay JSON in {file_path.name}: {exc.msg}', 422)
        if not isinstance(parsed, list):
            raise AppError('SOURCE_PARSE_ERROR', f'Replay corpus {file_path.name} must contain a JSON array.', 422)
        return parsed

    def _load_records(self, target: Path, batch: str) -> list:
        if target.is_dir():
            batch_file = target / f'{batch}.json'
            if batch_file.exists():
                return self._read_bounded_records(batch_file, is_jsonl=False)
            jsonl_file = target / f'{batch}.jsonl'
            if jsonl_file.exists():
                return self._read_bounded_records(jsonl_file, is_jsonl=True)
            all_file = target / 'all.json'
            if all_file.exists():
                return self._read_bounded_records(all_file, is_jsonl=False)
            raise AppError('SOURCE_NOT_FOUND', f'Replay batch {batch!r} not found in {target.name}.', 404)
        return self._read_bounded_records(target, is_jsonl=target.suffix.lower() == '.jsonl')

    def fetch(self, source: dict, batch: str = 'all', **kwargs) -> list[dict]:
        rel = str(source.get('config', {}).get('corpus_path') or source['endpoint'].removeprefix('replay://'))
        target = _safe_corpus_path(self.corpus_root, rel)
        records = self._load_records(target, batch)
        as_of = kwargs.get('as_of')
        cutoff = _to_utc_dt(as_of) if as_of else None
        filter_source = source.get('config', {}).get('filter_source_id')

        result = []
        future_filtered = 0
        skipped_invalid_timestamp = 0
        skipped_invalid_items = 0
        for item in records:
            if not isinstance(item, dict):
                skipped_invalid_items += 1
                continue
            orig_source = item.get('source_id')
            if filter_source and orig_source != filter_source:
                continue
            item_batch = item.get('batch')
            if batch != 'all' and item_batch is not None and str(item_batch) != str(batch):
                continue
            pub_iso = parse_published_at(item.get('published_at'))
            if not pub_iso:
                skipped_invalid_timestamp += 1
                continue
            if cutoff and _to_utc_dt(pub_iso) > cutoff:
                future_filtered += 1
                continue
            raw_title = str(item.get('title') or '')
            raw_content = str(item.get('content') or '')
            if not clean_text(raw_title) and not clean_text(raw_content):
                skipped_invalid_items += 1
                continue
            raw_ext = str(item.get('external_id') or '').strip()
            if not raw_ext:
                raw_ext = f"replay-{hashlib.sha256(f'{raw_title}:{pub_iso}'.encode('utf-8')).hexdigest()[:16]}"
            ext_id = f'{orig_source}:{raw_ext}' if orig_source and orig_source != source['source_id'] else raw_ext
            result.append(
                _build_evidence(
                    source_id=source['source_id'],
                    external_id=ext_id,
                    url=str(item.get('url') or ''),
                    title=raw_title,
                    content=raw_content,
                    published_at=pub_iso,
                )
            )
        self.last_stats = {
            'returned': len(result),
            'future_filtered': future_filtered,
            'skipped_invalid_timestamp': skipped_invalid_timestamp,
            'skipped_invalid_items': skipped_invalid_items,
        }
        return sorted(result, key=lambda e: (e['published_at'], e['external_id']))


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower() if '}' in tag else tag.lower()


def _child_text(elem: ET.Element, names: tuple[str, ...]) -> str:
    for child in elem:
        if _local_name(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            parts = ''.join(child.itertext()).strip()
            if parts:
                return parts
    return ''


def _atom_link(elem: ET.Element) -> str:
    for child in elem:
        if _local_name(child.tag) == 'link':
            rel = child.attrib.get('rel', 'alternate')
            href = child.attrib.get('href', '').strip()
            if href and rel in ('alternate', ''):
                return href
            if child.text and child.text.strip():
                return child.text.strip()
    return ''


class RssConnector:
    """Resilient RSS 2.0 / Atom connector with bounded retry and strict timestamp parsing."""

    def __init__(self, client=None, max_attempts: int = 2, timeout: float = 15.0):
        self.client = client
        self.max_attempts = max(1, max_attempts)
        self.timeout = timeout
        self.last_stats: dict = {}

    def _fetch_following_safe_redirects(self, url: str, headers: dict) -> httpx.Response:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            _validate_http_url(current, resolve_dns=True)
            resp = httpx.get(current, headers=headers, timeout=self.timeout, follow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('location', '').strip()
                if not location:
                    return resp
                current = urljoin(current, location)
                continue
            return resp
        raise AppError('SOURCE_UNAVAILABLE', 'RSS feed exceeded maximum redirect hops.', 502)

    def _get_feed_xml(self, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                headers = {'User-Agent': 'TraceRadar-Connector/1.0'}
                if self.client is not None:
                    resp = self.client.get(url, headers=headers, timeout=self.timeout)
                else:
                    resp = self._fetch_following_safe_redirects(url, headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.TimeoutException(f'Temporary HTTP {resp.status_code}')
                if resp.is_error:
                    raise AppError('SOURCE_HTTP_ERROR', f'RSS returned HTTP {resp.status_code}.', 502)
                if len(resp.content) > _MAX_FEED_BYTES:
                    raise AppError('SOURCE_PARSE_ERROR', f'RSS/Atom feed payload exceeds {_MAX_FEED_BYTES // (1024 * 1024)} MB limit.', 422)
                return resp.text
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt == self.max_attempts:
                    raise AppError('SOURCE_UNAVAILABLE', f'RSS feed unreachable: {type(exc).__name__}', 502)
                time.sleep(0.3)
        raise AppError('SOURCE_UNAVAILABLE', f'RSS feed unreachable: {last_exc}', 502)

    def fetch(self, source: dict, batch: str = 'live', **kwargs) -> list[dict]:
        xml_text = self._get_feed_xml(source['endpoint'])
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise AppError('SOURCE_PARSE_ERROR', f'Malformed RSS/Atom XML: {exc}', 422)

        as_of = kwargs.get('as_of')
        cutoff = _to_utc_dt(as_of) if as_of else None
        max_items = _parse_max_items(source.get('config') or {})

        entries = [el for el in root.iter() if _local_name(el.tag) in ('item', 'entry')]
        results = []
        skipped_missing_timestamp = 0
        future_filtered = 0

        for entry in entries:
            title = _child_text(entry, ('title',))
            link = _atom_link(entry) or _child_text(entry, ('link',))
            raw_pub = _child_text(entry, ('pubdate', 'published', 'updated', 'date'))
            pub_iso = parse_published_at(raw_pub)
            if not pub_iso:
                skipped_missing_timestamp += 1
                continue
            if cutoff and _to_utc_dt(pub_iso) > cutoff:
                future_filtered += 1
                continue
            body = _child_text(entry, ('encoded', 'content', 'description', 'summary')) or title
            raw_id = _child_text(entry, ('guid', 'id')) or canonicalize_url(link)
            if not raw_id:
                raw_id = hashlib.sha256(f'{title}:{pub_iso}'.encode('utf-8')).hexdigest()[:24]
            results.append(
                _build_evidence(
                    source_id=source['source_id'],
                    external_id=raw_id,
                    url=link,
                    title=title,
                    content=body,
                    published_at=pub_iso,
                )
            )
            if len(results) >= max_items:
                break

        self.last_stats = {
            'returned': len(results),
            'skipped_missing_timestamp': skipped_missing_timestamp,
            'future_filtered': future_filtered,
        }
        return sorted(results, key=lambda e: (e['published_at'], e['external_id']))


class ManualConnector:
    """Converts manually submitted items into validated Evidence records."""

    def __init__(self):
        self.last_stats: dict = {}

    def fetch(self, source: dict, batch: str = 'manual', **kwargs) -> list[dict]:
        raw_items = kwargs.get('manual_items')
        if raw_items is None:
            self.last_stats = {'returned': 0, 'future_filtered': 0, 'skipped_invalid_items': 0}
            return []
        if not isinstance(raw_items, list):
            raise AppError('INVALID_INPUT', 'manual_items must be a list of item objects.', 422)

        as_of = kwargs.get('as_of')
        cutoff = _to_utc_dt(as_of) if as_of else None
        results = []
        future_filtered = 0
        skipped_invalid_items = 0
        for idx, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise AppError('INVALID_INPUT', 'Each manual item must be a JSON object.', 422)
            item_source = raw.get('source_id')
            if item_source and item_source != source['source_id']:
                continue
            raw_title = str(raw.get('title') or '')
            raw_content = str(raw.get('content') or '')
            if not clean_text(raw_title) and not clean_text(raw_content):
                skipped_invalid_items += 1
                continue
            raw_pub = raw.get('published_at')
            if raw_pub is not None and str(raw_pub).strip() != '':
                pub_iso = parse_published_at(str(raw_pub))
                if not pub_iso:
                    raise AppError('INVALID_INPUT', f'Invalid published_at timestamp in manual item: {raw_pub}', 422)
            else:
                pub_iso = now()
            if cutoff and _to_utc_dt(pub_iso) > cutoff:
                future_filtered += 1
                continue
            ext_id = raw.get('external_id') or f"manual-{hashlib.sha256((raw_title + raw_content).encode('utf-8')).hexdigest()[:16]}-{idx}"
            results.append(
                _build_evidence(
                    source_id=source['source_id'],
                    external_id=str(ext_id),
                    url=str(raw.get('url') or ''),
                    title=raw_title,
                    content=raw_content,
                    published_at=pub_iso,
                )
            )
        self.last_stats = {
            'returned': len(results),
            'future_filtered': future_filtered,
            'skipped_invalid_items': skipped_invalid_items,
        }
        return sorted(results, key=lambda e: (e['published_at'], e['external_id']))


class ConnectorRouter:
    """Dispatches source fetches to Fixture, Replay, RSS, or Manual connectors."""

    def __init__(self, fixture_path: Path, corpus_root: Path | None = None, rss_client=None):
        self.fixture = FixtureConnector(fixture_path)
        self.replay = ReplayConnector(corpus_root or fixture_path.parent)
        self.rss = RssConnector(client=rss_client)
        self.manual = ManualConnector()
        self.last_fetch_stats: dict = {}

    def fetch(self, source: dict, batch: str, **kwargs) -> list[dict]:
        stype = source.get('type', 'fixture')
        if stype == 'fixture':
            items = self.fixture.fetch(source, batch, **kwargs)
            self.last_fetch_stats = dict(self.fixture.last_stats)
            return items
        if stype == 'replay':
            items = self.replay.fetch(source, batch, **kwargs)
            self.last_fetch_stats = dict(self.replay.last_stats)
            return items
        if stype == 'rss':
            items = self.rss.fetch(source, batch, **kwargs)
            self.last_fetch_stats = dict(self.rss.last_stats)
            return items
        if stype == 'manual':
            items = self.manual.fetch(source, batch, **kwargs)
            self.last_fetch_stats = dict(self.manual.last_stats)
            return items
        raise AppError('INVALID_SOURCE', f'Unknown connector type: {stype}', 422)


def recommend_sources(topic: dict, repo_sources: list[dict]) -> dict:
    """Recommend existing registered sources plus topic-tailored live RSS candidates."""
    text = f"{topic.get('title', '')} {topic.get('description', '')}".lower()
    tokens = [t for t in tokenize(text, drop_stopwords=True) if len(t) >= 2][:6]
    query = ' '.join(tokens) if tokens else topic.get('title', 'news')

    recommended = []
    for src in repo_sources:
        tier = src.get('config', {}).get('tier', 'simulated')
        recommended.append(
            dict(
                src,
                reason='Registered source in workspace.' if src['type'] != 'fixture' else 'Registered simulated cybersecurity demo source.',
                tier=tier,
                registered=True,
            )
        )

    topic_slug = str(topic.get('topic_id', 'default'))[:8].lower()
    dynamic_candidates = [
        dict(
            source_id=f'gnews_{topic_slug}',
            name=f"Google News RSS ({query[:40]})",
            type='rss',
            endpoint=f'https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en',
            config={'tier': 'aggregator', 'max_items': 10},
            reason='Dynamic topic-specific news search feed.',
            tier='aggregator',
            registered=False,
        ),
        dict(
            source_id='bbc_world_rss',
            name='BBC World News RSS',
            type='rss',
            endpoint='https://feeds.bbci.co.uk/news/world/rss.xml',
            config={'tier': 'media', 'max_items': 10},
            reason='Reliable general world & breaking news feed.',
            tier='media',
            registered=False,
        ),
    ]
    if any(k in text for k in ('cve', 'security', 'vulnerability', 'cyber', '漏洞', '安全', 'examplesoft')):
        dynamic_candidates.append(
            dict(
                source_id='bleepingcomputer_rss',
                name='BleepingComputer Security RSS',
                type='rss',
                endpoint='https://www.bleepingcomputer.com/feed/',
                config={'tier': 'media', 'max_items': 10},
                reason='Cybersecurity advisories and vulnerability coverage.',
                tier='media',
                registered=False,
            )
        )
    if any(k in text for k in ('nba', 'lebron', 'lbj', 'basketball', 'trade', 'sports', '湖人', '詹姆斯')):
        dynamic_candidates.append(
            dict(
                source_id='espn_nba_rss',
                name='ESPN NBA News RSS',
                type='rss',
                endpoint='https://www.espn.com/espn/rss/nba/news',
                config={'tier': 'media', 'max_items': 10},
                reason='NBA roster moves, rumors and official transactions.',
                tier='media',
                registered=False,
            )
        )

    existing_ids = {s['source_id'] for s in recommended}
    for cand in dynamic_candidates:
        if cand['source_id'] not in existing_ids:
            recommended.append(cand)

    return {'recommendation_mode': 'topic_aware_v1', 'sources': recommended}
