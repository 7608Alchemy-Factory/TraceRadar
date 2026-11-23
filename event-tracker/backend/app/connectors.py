"""Source connectors (Fixture, time-sliced Replay, RSS/Atom, Manual) and topic source recommendations."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
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
        cfg = source.get('config') or {}
        filter_source = cfg.get('filter_source_id')
        has_bool_filter = bool(cfg.get('query_any') or cfg.get('query_all') or cfg.get('query_not'))

        result = []
        future_filtered = 0
        query_filtered = 0
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
            if has_bool_filter and not _matches_boolean_query(f'{raw_title} {raw_content}', cfg):
                query_filtered += 1
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
        stats = {
            'returned': len(result),
            'future_filtered': future_filtered,
            'skipped_invalid_timestamp': skipped_invalid_timestamp,
            'skipped_invalid_items': skipped_invalid_items,
        }
        if has_bool_filter:
            stats['query_filtered'] = query_filtered
        self.last_stats = stats
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
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
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


_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_CJK_FILLER_RE = re.compile(r'(最新的|最新|关于|追踪|跟踪|关注|动向|动态|消息|情况|的|了)')
_ASCII_ENTITY_RE = re.compile(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,19}(?![A-Za-z0-9])')
_GENERIC_PROPER_STOP = {
    'the', 'nba', 'lpl', 'lck', 'espn', 'yahoo', 'sports', 'news', 'reports', 'report', 'sources',
    'free', 'agency', 'agent', 'agents', 'trade', 'trades', 'season', 'summer', 'league', 'july',
    'june', 'august', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'lebron', 'james', 'klutch', 'game', 'over', 'podcast', 'shams', 'charania', 'athletic',
    'liberty', 'ballers', 'official', 'update', 'updates', 'breaking', 'final', 'nba.com',
    'sohu', 'thepaper', 'sina', 'netease', 'hupu', 'weibo', 'zhihu', 'bilibili', 'baidu', 'toutiao',
    's16', 's15', 's14', 'cn', 'com',
}

# Neutral, zero-hindsight-bias NBA team registry (all major contenders treated identically).
_NBA_TEAM_PROFILES: list[tuple[str, str, list[str]]] = [
    ('timberwolves', 'Minnesota Timberwolves', ['timberwolves', 'minnesota', 'wolves']),
    ('warriors', 'Golden State Warriors', ['warriors', 'golden state', 'draymond green']),
    ('heat', 'Miami Heat', ['miami heat', 'heat']),
    ('cavaliers', 'Cleveland Cavaliers', ['cavaliers', 'cleveland', 'cavs']),
    ('76ers', 'Philadelphia 76ers', ['76ers', 'sixers', 'philadelphia']),
    ('nuggets', 'Denver Nuggets', ['nuggets', 'denver']),
    ('knicks', 'New York Knicks', ['knicks', 'new york knicks']),
    ('mavericks', 'Dallas Mavericks', ['mavericks', 'dallas']),
    ('spurs', 'San Antonio Spurs', ['spurs', 'san antonio']),
    ('celtics', 'Boston Celtics', ['celtics', 'boston']),
    ('clippers', 'LA Clippers', ['clippers']),
    ('rockets', 'Houston Rockets', ['rockets', 'houston']),
    ('suns', 'Phoenix Suns', ['phoenix suns', 'suns']),
    ('bucks', 'Milwaukee Bucks', ['milwaukee bucks', 'bucks']),
]


# Neutral, zero-hindsight-bias McDonald's HK sub-campaign / collab profiles (surfaced only after appearing in ingested news).
_MCD_HK_CAMPAIGN_PROFILES: list[tuple[str, str, list[str]]] = [
    ('mcgriddles', 'McGriddles 全日早餐系列', ['mcgriddles', '班戟']),
    ('chiikawa', 'CHIIKAWA 開心樂園餐聯乘', ['chiikawa', '吉伊卡哇']),
    ('mcspicy_gdragon', '脆辣雞腿飽 / 韓辣牛油 G-DRAGON 聯乘', ['脆辣雞腿飽', '韓辣牛油', 'g-dragon', 'gochujang']),
    ('vouchers', '麥麥慳套票 / $100 四餐', ['麥麥慳', '套票']),
    ('afternoon_tea', '鹽味焦糖脆條 / $20-$22 下午茶', ['下午茶', '焦糖', '脆條', '麥旋風']),
    ('jackson_wang', '王嘉爾 Under The Castle 萬聖節聯乘', ['王嘉爾', 'jackson wang', '黑魂版', 'under the castle']),
    ('pokemon', 'Pokémon 寶可夢 30 週年 / Pokémon GO 聯乘', ['pokémon', 'pokemon', '寶可夢', '比卡超']),
    ('bt21', 'BT21 開心樂園餐聯乘', ['bt21', 'chimmy', 'tata']),
    ('winterfest', '旅發局繽紛冬日賞 / 買一送一', ['繽紛冬日', '買一送一', '買1送1', '旅發局']),
]


def _find_grounded_fact_snippet(
    aliases: list[str],
    current_events: list[dict] | None,
    relevant_rows: list[dict] | None,
) -> tuple[str, str]:
    """0-Extra-Token rationale: reuse already-generated LLM key_facts / supported_claims mentioning the candidate."""
    # 1. Prefer already-committed EventState.key_facts (newest first)
    for ev in reversed(list(current_events or [])):
        facts = list(((ev.get('state') or {}).get('key_facts') or []))
        for f in reversed(facts):
            fact_text = str(f.get('fact') or '').strip()
            low = fact_text.lower()
            if fact_text and any(_term_in_text(a, low) for a in aliases):
                return 'key_fact', fact_text[:220]

    # 2. Fall back to supported_claims in assessed evidence_decisions (includes Needs Review rumors!)
    for row in reversed(list(relevant_rows or [])):
        ass = row.get('assessment') or {}
        claims = list(ass.get('supported_claims') or [])
        for dec in (ass.get('event_decisions') or []):
            claims.extend(dec.get('supported_claims') or [])
        for c in claims:
            fact_text = str(c.get('fact') or '').strip()
            low = fact_text.lower()
            if fact_text and any(_term_in_text(a, low) for a in aliases):
                pub = str((row.get('evidence') or {}).get('published_at') or '')[:10]
                return f'claim@{pub}' if pub else 'claim', fact_text[:220]

    # 3. Last resort: exact headline of the latest relevant article mentioning the candidate
    for row in reversed(list(relevant_rows or [])):
        ev = row.get('evidence') or {}
        title = str(ev.get('title') or '').strip()
        blob = f"{title} {ev.get('content', '')}".lower()
        if title and any(_term_in_text(a, blob) for a in aliases):
            pub = str(ev.get('published_at') or '')[:10]
            return f'headline@{pub}' if pub else 'headline', title[:180]
    return '', ''


def _summarize_candidates_with_llm(
    topic: dict,
    candidates: list[dict],
    current_events: list[dict] | None,
    settings,
) -> tuple[str | None, dict[str, str]]:
    """Optional ON/OFF lightweight single-call LLM summary comparing surfaced candidate branches."""
    if not candidates or not settings:
        return None, {}
    api_key = getattr(settings, 'api_key', '')
    base_url = getattr(settings, 'base_url', '')
    model = getattr(settings, 'model', '')
    if not (api_key and base_url and model):
        return None, {}

    payload = {
        'topic': {'title': topic.get('title', ''), 'description': topic.get('description', '')},
        'current_events': [
            {
                'title': e.get('title', ''),
                'version': e.get('version', 0),
                'status': str((e.get('state') or {}).get('current_status', ''))[:260],
            }
            for e in (current_events or [])[:4]
        ],
        'candidates': [
            {
                'source_id': c['source_id'],
                'label': (c.get('config') or {}).get('branch_label') or c['name'],
                'fact_evidence': c.get('fact_evidence') or c.get('reason', ''),
            }
            for c in candidates[:6]
        ],
    }
    system = (
        'You help a user decide which newly discovered candidate search keywords/branches to approve for an event tracker. '
        'Return ONLY JSON matching {"summary_note": "1-2 concise Chinese sentences comparing which 1-2 candidates are highest priority to check right now and why", '
        '"per_source": {"<source_id>": "1 concise Chinese sentence explaining why to approve or skip this branch based ONLY on fact_evidence"}}. '
        'Never invent facts not present in the input.'
    )
    try:
        resp = httpx.post(
            base_url.rstrip('/') + '/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'temperature': 0,
                'response_format': {'type': 'json_object'},
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                ],
            },
            timeout=20.0,
        )
        if resp.status_code != 200:
            return None, {}
        parsed = json.loads(resp.json()['choices'][0]['message']['content'])
        if not isinstance(parsed, dict):
            return None, {}
        note = str(parsed.get('summary_note') or '').strip()[:360] or None
        raw_map = parsed.get('per_source') if isinstance(parsed.get('per_source'), dict) else {}
        per_source = {str(k): str(v).strip()[:240] for k, v in raw_map.items() if str(v or '').strip()}
        return note, per_source
    except Exception:
        return None, {}


def _term_in_text(term: str, lowered_text: str) -> bool:
    t = str(term or '').strip().lower()
    if not t:
        return False
    if _CJK_RE.search(t):
        return t in lowered_text
    if ' ' in t:
        return t in lowered_text
    return bool(re.search(rf'(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])', lowered_text))


def _matches_boolean_query(text: str, config: dict) -> bool:
    lowered = (text or '').lower()
    query_not = config.get('query_not') or []
    if any(_term_in_text(term, lowered) for term in query_not):
        return False
    query_all = config.get('query_all') or []
    if query_all and not all(_term_in_text(term, lowered) for term in query_all):
        return False
    query_any = config.get('query_any') or []
    if query_any and not any(_term_in_text(term, lowered) for term in query_any):
        return False
    return True


def _extract_clean_query(topic: dict) -> str:
    title = str(topic.get('title', '') or '').strip()
    desc = str(topic.get('description', '') or '').strip()
    if _CJK_RE.search(title):
        # Drop parenthetical examples/sub-facets so Google News RSS does not AND-lock to 0 hits.
        core = re.sub(r'（[^）]*）|\([^)]*\)', ' ', title)
        core = re.sub(r'[，。！？、；：“”‘’【】\[\]|/\\:]+', ' ', core)
        core = _CJK_FILLER_RE.sub(' ', core)
        ascii_entities = _ASCII_ENTITY_RE.findall(core)
        # If the title already contains 2+ high-signal ASCII identifiers (e.g. "BLG LPL"),
        # keep them as a crisp high-recall anchor so diverse sub-facets (roster/streams/interviews) all enter Round 1.
        if len(ascii_entities) >= 2:
            dedup_ascii: list[str] = []
            for tok in ascii_entities:
                if tok.lower() not in {x.lower() for x in dedup_ascii}:
                    dedup_ascii.append(tok)
            return ' '.join(dedup_ascii[:3])
        # Otherwise split on conjunctions and keep at most 2 concise chunks (~20 chars).
        parts = [p.strip() for p in re.split(r'[\s与和及]+', core) if p.strip()]
        compact = ' '.join(parts[:3])
        return compact[:24].strip() or title[:24]
    text = f'{title} {desc}'.lower()
    tokens = [t for t in tokenize(text, drop_stopwords=True) if len(t) >= 2 and not _CJK_RE.search(t)][:6]
    return ' '.join(tokens) if tokens else (title[:48] or 'news')


def _build_google_news_rss_url(query: str, *, recency_days: int | None = None) -> str:
    q = ' '.join(str(query or '').split()).strip() or 'news'
    if isinstance(recency_days, int) and 1 <= recency_days <= 365 and 'when:' not in q:
        q = f'{q} when:{recency_days}d'
    if _CJK_RE.search(q) or any(tok in q.upper() for tok in ('LPL', 'BLG', 'WBG', 'TES', 'JDG', 'EDG', 'RNG', 'HK')):
        return f'https://news.google.com/rss/search?q={quote_plus(q)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    return f'https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en'


def _detect_milestone_winner(
    current_events: list[dict] | None,
    notifications: list[dict] | None = None,
) -> tuple[bool, set[str]]:
    """Detect whether the main LeBron decision storyline reached final resolution and which team won (never triggered by other players' contracts)."""
    has_breaking_notif = any(
        n.get('priority') == 'breaking' or 'major_milestone' in (n.get('reason_codes') or [])
        for n in (notifications or [])
    )
    subject_decision_re = re.compile(
        r'(?:lebron(?:\s+james)?|lbj|詹姆斯).{0,70}'
        r'(?:picks?|picked|signs?|signed|agrees?|agreed|joins?|joined|commits?|committed|chooses?|chose|'
        r'选择|選擇|加盟|签下|簽下|签约|簽約|官宣|同意与|同意與|达成协议|達成協議)',
        re.I,
    )
    winning_patterns: list[tuple[str, re.Pattern]] = [
        ('76ers', re.compile(r'(philadelphia\s+76ers|76ers|sixers|费城76人|76人)', re.I)),
        ('warriors', re.compile(r'(golden\s+state\s+warriors|warriors|金州勇士|勇士)', re.I)),
        ('cavaliers', re.compile(r'(cleveland\s+cavaliers|cavaliers|cavs|克利夫兰骑士|骑士)', re.I)),
        ('heat', re.compile(r'(miami\s+heat|迈阿密热火|热火)', re.I)),
        ('timberwolves', re.compile(r'(minnesota\s+timberwolves|timberwolves|明尼苏达森林狼|森林狼)', re.I)),
    ]
    settled = False
    winning_slugs: set[str] = set()
    for ev in (current_events or []):
        st = ev.get('state') or {}
        status_text = str(st.get('current_status', '') or '')
        m = subject_decision_re.search(status_text)
        if not m:
            continue
        # Only inspect the clause immediately around LeBron's decision (prevent Draymond Green / Jaylen Brown false triggers!)
        clause = status_text[m.start():m.end() + 70]
        for slug, pat in winning_patterns:
            if pat.search(clause):
                settled = True
                winning_slugs.add(slug)
    if settled and has_breaking_notif:
        return True, winning_slugs
    return settled, winning_slugs


def _audit_source_health(
    src: dict,
    evidence_decisions: list[dict] | None,
    *,
    milestone_settled: bool,
    winning_slugs: set[str],
) -> tuple[str, str | None]:
    """0-Token Silence vs. Noise Audit + Milestone Closure check for branch sources."""
    cfg = src.get('config') or {}
    if cfg.get('role') != 'branch':
        return 'active', None

    sid = src['source_id']
    assessed_irrelevant = 0
    useful = 0
    recent_outcomes: list[tuple[str, bool]] = []
    for row in (evidence_decisions or []):
        ev = row.get('evidence') or {}
        if ev.get('source_id') != sid:
            continue
        st = row.get('status')
        ass = row.get('assessment') or {}
        pub = str(ev.get('published_at') or '')
        if st == 'review':
            useful += 1
            recent_outcomes.append((pub, True))
        elif st == 'completed' and ass:
            if ass.get('is_relevant') is False:
                assessed_irrelevant += 1
                recent_outcomes.append((pub, False))
            elif ass.get('is_relevant') is True:
                useful += 1
                recent_outcomes.append((pub, True))

    recent_outcomes.sort(key=lambda pair: pair[0])
    trailing_irrelevant = 0
    for _, is_rel in reversed(recent_outcomes):
        if not is_rel:
            trailing_irrelevant += 1
        else:
            break

    # Rule 1: High-Noise Audit (Silence with 0 items or duplicates NEVER triggers retirement!)
    if assessed_irrelevant >= 3 and (useful == 0 or trailing_irrelevant >= 3):
        return (
            'noise_alert',
            f'⚠️ 噪音审计告警：该支线近期连续 {trailing_irrelevant} 篇（累计 {assessed_irrelevant} 篇）新报道均被判无关，正在消耗验证 Token，建议取消勾选退役！',
        )

    # Rule 2: Milestone Closure (main storyline finalized -> unchosen competing branches can be archived)
    branch_slug = str(cfg.get('branch_slug') or '').lower()
    if milestone_settled and branch_slug and winning_slugs and branch_slug not in winning_slugs:
        return (
            'milestone_closed',
            '🏁 主线事件已官宣落定，该未中标竞争支线可取消勾选归档以保持词表精简。',
        )

    if useful > 0:
        return 'productive', f'✅ 活跃有效：已贡献 {useful} 篇相关报道（噪音 {assessed_irrelevant} 篇）。'
    return 'quiet', '静默观察中：暂无多余噪音消耗（重复报道由 0-Token 去重层拦截，不误杀静默支线）。'


def recommend_sources(
    topic: dict,
    repo_sources: list[dict],
    *,
    current_events: list[dict] | None = None,
    evidence_decisions: list[dict] | None = None,
    notifications: list[dict] | None = None,
    corpus_root: Path | None = None,
    approved_source_ids: set[str] | None = None,
    llm_summary: bool = False,
    settings=None,
) -> dict:
    """Recommend topic-scoped watchlist anchors/branches (with 0-Token key_fact rationales + optional ON/OFF LLM comparison) and media channels."""
    title_str = str(topic.get('title', '') or '')
    desc_str = str(topic.get('description', '') or '')
    text = f'{title_str} {desc_str}'.lower()
    query = _extract_clean_query(topic)
    topic_slug = str(topic.get('topic_id', 'default'))[:8].lower()
    approved_set = set(approved_source_ids or set())

    lbj_sim_rel = 'lbj_2026_daily_sim/all.json'
    lbj_sim_available = bool(corpus_root and (corpus_root / lbj_sim_rel).exists())
    is_lbj_topic = any(k in text for k in ('lebron', 'lbj', '詹姆斯'))

    mcd_sim_rel = 'mcd_hk_real_2026/all.json'
    mcd_sim_available = bool(corpus_root and (corpus_root / mcd_sim_rel).exists())
    is_mcd_topic = any(k in text for k in ('麥當勞', '麦当劳', 'mcdonald'))

    milestone_settled, winning_slugs = _detect_milestone_winner(current_events, notifications)

    # Pre-collect relevant evidence chronologically for 0-Token entity discovery & fact grounding.
    relevant_rows = []
    for row in (evidence_decisions or []):
        st = row.get('status')
        ass = row.get('assessment') or {}
        if st == 'review' or (st == 'completed' and ass.get('is_relevant') is True):
            relevant_rows.append(row)
    relevant_rows.sort(key=lambda r: str((r.get('evidence') or {}).get('published_at', '')))

    # Filter out other topics' private generated sources so cross-topic tabs stay clean.
    recommended = []
    for src in repo_sources:
        sid = src['source_id']
        if sid.startswith('lbj_anchor_') and sid != f'lbj_anchor_{topic_slug}' and sid not in approved_set:
            continue
        if sid.startswith('mcd_anchor_') and sid != f'mcd_anchor_{topic_slug}' and sid not in approved_set:
            continue
        if sid.startswith('kw_') and not sid.startswith(f'kw_{topic_slug}_') and sid not in approved_set:
            continue
        cfg = src.get('config') or {}
        tier = cfg.get('tier', 'simulated')
        role = cfg.get('role') or ('anchor' if sid == f'gnews_{topic_slug}' else 'channel')
        base_reason = (
            'Registered source in workspace.'
            if src['type'] != 'fixture'
            else 'Registered simulated cybersecurity demo source.'
        )
        health_status, health_note = _audit_source_health(
            src,
            evidence_decisions,
            milestone_settled=milestone_settled,
            winning_slugs=winning_slugs,
        )
        # Re-ground fact evidence for existing branch sources so rationales stay human-readable!
        fact_evidence = ''
        if role == 'branch':
            aliases = list(cfg.get('query_any') or [])
            if not aliases and cfg.get('branch_label'):
                aliases = [str(cfg['branch_label'])]
            if aliases:
                _, fact_evidence = _find_grounded_fact_snippet(aliases, current_events, relevant_rows)
        display_name = src['name']
        if health_status == 'noise_alert' and not display_name.startswith('⚠️'):
            display_name = f'⚠️ [建议降级/退役] {display_name}'
        elif health_status == 'milestone_closed' and not display_name.startswith('🏁'):
            display_name = f'🏁 [可归档] {display_name}'
        reason_text = health_note or base_reason
        if fact_evidence and health_status in ('quiet', 'productive', 'active'):
            reason_text = f'依据：{fact_evidence} ｜ {reason_text}'
        recommended.append(
            dict(
                src,
                name=display_name,
                reason=reason_text,
                fact_evidence=fact_evidence,
                role=role,
                tier=tier,
                registered=True,
                health_status=health_status,
                retirement_recommended=health_status in ('noise_alert', 'milestone_closed'),
            )
        )

    dynamic_candidates = []
    # Topic 1: LBJ Daily Simulation Anchor (53 real URLs)
    if is_lbj_topic and lbj_sim_available:
        dynamic_candidates.append(
            dict(
                source_id=f'lbj_anchor_{topic_slug}',
                name='主搜索词：LeBron James（逐日模拟池 · 53条全真链）',
                type='replay',
                endpoint=f'replay://{lbj_sim_rel}',
                config={
                    'corpus_path': lbj_sim_rel,
                    'query_any': ['lebron', 'james'],
                    'tier': 'media',
                    'role': 'anchor',
                },
                reason='冷启动布尔主词（OR: LeBron / James）：只抓取直接提及老詹的报道，作为零泄题基线。',
                fact_evidence='',
                role='anchor',
                tier='media',
                registered=False,
                health_status='active',
                retirement_recommended=False,
            )
        )

    # Topic 2: McDonald's HK Real 2026 Promotions Anchor (51 real URLs)
    if is_mcd_topic and mcd_sim_available:
        dynamic_candidates.append(
            dict(
                source_id=f'mcd_anchor_{topic_slug}',
                name='主搜索词：香港麥當勞優惠與新品（51條 2026 真實港媒與官網語料）',
                type='replay',
                endpoint=f'replay://{mcd_sim_rel}',
                config={
                    'corpus_path': mcd_sim_rel,
                    'query_all': ['麥當勞'],
                    'query_any': ['優惠', '套餐', '聯乘', '登場', '回歸', '新品', 'App', '開心樂園餐', '買一送一', '買1送1'],
                    'tier': 'media',
                    'role': 'anchor',
                },
                reason='冷啟動布爾主詞（麥當勞 AND 優惠/套餐/聯乘/新品）：抓取香港主流媒體與官網促銷主線，自動發現大型聯乘/套票子活動。',
                fact_evidence='',
                role='anchor',
                tier='media',
                registered=False,
                health_status='active',
                retirement_recommended=False,
            )
        )

    # 0-Token Zero-Hindsight-Bias Chronological Branch Discovery from RELEVANT ingested evidence
    if is_lbj_topic and lbj_sim_available and relevant_rows:
        discovered_teams: dict[str, dict] = {}
        title_lower = title_str.lower()
        for row in relevant_rows:
            ev = row.get('evidence') or {}
            blob = f"{ev.get('title', '')} {ev.get('content', '')}".lower()
            pub_date = str(ev.get('published_at', ''))[:10]
            for slug, team_name, aliases in _NBA_TEAM_PROFILES:
                if any(_term_in_text(a, title_lower) for a in aliases):
                    continue
                if any(_term_in_text(a, blob) for a in aliases):
                    if slug not in discovered_teams:
                        discovered_teams[slug] = {
                            'slug': slug,
                            'name': team_name,
                            'aliases': aliases,
                            'first_date': pub_date,
                            'count': 0,
                        }
                    discovered_teams[slug]['count'] += 1

        ordered_teams = sorted(discovered_teams.values(), key=lambda t: (t['first_date'], -t['count'], t['slug']))
        for info in ordered_teams[:10]:
            sid = f"kw_{topic_slug}_{info['slug']}"
            _, fact_snip = _find_grounded_fact_snippet(info['aliases'], current_events, relevant_rows)
            reason = (
                f"依据（已提炼事实）：{fact_snip}"
                if fact_snip
                else f"0-Token 实体发现：{info['first_date']} 起在相关报道中提及 {info['name']}（共 {info['count']} 篇）。"
            )
            dynamic_candidates.append(
                dict(
                    source_id=sid,
                    name=f"支线候选：{info['name']}（{info['first_date']} 首次提及 · {info['count']} 篇）",
                    type='replay',
                    endpoint=f'replay://{lbj_sim_rel}',
                    config={
                        'corpus_path': lbj_sim_rel,
                        'query_any': info['aliases'],
                        'tier': 'media',
                        'role': 'branch',
                        'branch_slug': info['slug'],
                        'branch_label': info['name'],
                    },
                    reason=reason,
                    fact_evidence=fact_snip,
                    role='branch',
                    tier='media',
                    registered=False,
                    health_status='candidate',
                    retirement_recommended=False,
                )
            )
    elif is_mcd_topic and mcd_sim_available and relevant_rows:
        discovered_camps: dict[str, dict] = {}
        title_lower = title_str.lower()
        for row in relevant_rows:
            ev = row.get('evidence') or {}
            blob = f"{ev.get('title', '')} {ev.get('content', '')}".lower()
            pub_date = str(ev.get('published_at', ''))[:10]
            for slug, camp_name, aliases in _MCD_HK_CAMPAIGN_PROFILES:
                if any(_term_in_text(a, title_lower) for a in aliases):
                    continue
                if any(_term_in_text(a, blob) for a in aliases):
                    if slug not in discovered_camps:
                        discovered_camps[slug] = {
                            'slug': slug,
                            'name': camp_name,
                            'aliases': aliases,
                            'first_date': pub_date,
                            'count': 0,
                        }
                    discovered_camps[slug]['count'] += 1

        ordered_camps = sorted(discovered_camps.values(), key=lambda c: (c['first_date'], -c['count'], c['slug']))
        for info in ordered_camps[:12]:
            sid = f"kw_{topic_slug}_{info['slug']}"
            _, fact_snip = _find_grounded_fact_snippet(info['aliases'], current_events, relevant_rows)
            reason = (
                f"依據（已提煉事實）：{fact_snip}"
                if fact_snip
                else f"0-Token 活動發現：{info['first_date']} 起提及「{info['name']}」（共 {info['count']} 篇）。"
            )
            dynamic_candidates.append(
                dict(
                    source_id=sid,
                    name=f"子活動候選：{info['name']}（{info['first_date']} 首次提及 · {info['count']} 篇）",
                    type='replay',
                    endpoint=f'replay://{mcd_sim_rel}',
                    config={
                        'corpus_path': mcd_sim_rel,
                        'query_any': info['aliases'],
                        'tier': 'media',
                        'role': 'branch',
                        'branch_slug': info['slug'],
                        'branch_label': info['name'],
                    },
                    reason=reason,
                    fact_evidence=fact_snip,
                    role='branch',
                    tier='media',
                    registered=False,
                    health_status='candidate',
                    retirement_recommended=False,
                )
            )
    elif relevant_rows:
        # Generic 0-Token entity expansion for any live RSS topic (e.g. BLG S16 -> Viper / Knight / Elk)
        counts: dict[str, int] = {}
        first_seen: dict[str, str] = {}
        title_lower = title_str.lower()
        anchor_ascii = _ASCII_ENTITY_RE.findall(title_str)
        # Preserve domain disambiguation (e.g. "BLG LPL" / "BLG 英雄联盟") to prevent cross-division drift!
        domain_tag = 'LPL' if ('lpl' in title_lower or '英雄联盟' in title_lower or 's16' in title_lower) else ''
        primary_subject = next((tok for tok in anchor_ascii if tok.lower() not in ('lpl', 'lck', 's16', 'nba')), None)
        anchor = primary_subject or (anchor_ascii[0] if anchor_ascii else query.split()[0])
        # Also discover high-signal CJK sub-facets (e.g. 直播/采访/庆功宴/转会/续约) alongside ASCII player/entity names
        cjk_facets = ('直播', '采访', '专访', '庆功宴', '转会', '续约', '离队', '官宣')
        for row in relevant_rows:
            ev = row.get('evidence') or {}
            pub = str(ev.get('published_at') or '')[:10]
            # Strip trailing Google News RSS publisher suffix (" - Sohu", " - 直播吧") so outlets never pose as entities
            clean_title = re.sub(r'\s+-\s+[^-]{1,36}$', '', str(ev.get('title', '') or ''))
            clean_body = re.sub(r'\s+-\s+[^-]{1,36}$', '', str(ev.get('content', '') or '')[:300])
            blob = f"{clean_title} {clean_body}"
            for tok in _ASCII_ENTITY_RE.findall(blob):
                low = tok.lower()
                if low in _GENERIC_PROPER_STOP or low in title_lower or low == anchor.lower():
                    continue
                counts[tok] = counts.get(tok, 0) + 1
                first_seen.setdefault(tok, pub)
            for facet in cjk_facets:
                if facet in blob:
                    counts[facet] = counts.get(facet, 0) + 1
                    first_seen.setdefault(facet, pub)
        top_ents = [ent for ent, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if cnt >= 1][:6]
        for ent in top_ents:
            kw_q = f'{anchor} {domain_tag} {ent}'.strip() if domain_tag and domain_tag.lower() != anchor.lower() else f'{anchor} {ent}'
            kw_q = ' '.join(kw_q.split())
            slug = hashlib.sha256(kw_q.lower().encode('utf-8')).hexdigest()[:8]
            _, fact_snip = _find_grounded_fact_snippet([ent], current_events, relevant_rows)
            reason = (
                f'依据（已提炼事实）：{fact_snip}'
                if fact_snip
                else f'0-Token 实体发现：已入库相关报道提及 {ent}（{counts[ent]} 次），勾选批准后可专项追踪。'
            )
            dynamic_candidates.append(
                dict(
                    source_id=f'kw_{topic_slug}_{slug}',
                    name=f'扩展关键词：{kw_q}（{first_seen.get(ent, "")} 提及 · {counts[ent]} 篇）',
                    type='rss',
                    endpoint=_build_google_news_rss_url(kw_q, recency_days=30),
                    config={'tier': 'aggregator', 'max_items': 8, 'role': 'branch', 'branch_label': ent},
                    reason=reason,
                    fact_evidence=fact_snip,
                    role='branch',
                    tier='aggregator',
                    registered=False,
                    health_status='candidate',
                    retirement_recommended=False,
                )
            )

    dynamic_candidates.extend(
        [
            dict(
                source_id=f'gnews_{topic_slug}',
                name=f'实时关键词 RSS：{query[:40]}',
                type='rss',
                endpoint=_build_google_news_rss_url(query, recency_days=30 if any(k in text for k in ('最新', '近期', 'latest', 'recent')) else None),
                config={'tier': 'aggregator', 'max_items': 10, 'role': 'anchor'},
                reason='实时 Google News 关键词订阅（自动识别中/英文语区）。',
                fact_evidence='',
                role='anchor',
                tier='aggregator',
                registered=False,
                health_status='active',
                retirement_recommended=False,
            ),
            dict(
                source_id='bbc_world_rss',
                name='BBC World News RSS',
                type='rss',
                endpoint='https://feeds.bbci.co.uk/news/world/rss.xml',
                config={'tier': 'media', 'max_items': 10, 'role': 'channel'},
                reason='Reliable general world & breaking news feed.',
                fact_evidence='',
                role='channel',
                tier='media',
                registered=False,
                health_status='active',
                retirement_recommended=False,
            ),
        ]
    )
    if any(k in text for k in ('cve', 'security', 'vulnerability', 'cyber', '漏洞', '安全', 'examplesoft')):
        dynamic_candidates.append(
            dict(
                source_id='bleepingcomputer_rss',
                name='BleepingComputer Security RSS',
                type='rss',
                endpoint='https://www.bleepingcomputer.com/feed/',
                config={'tier': 'media', 'max_items': 10, 'role': 'channel'},
                reason='Cybersecurity advisories and vulnerability coverage.',
                fact_evidence='',
                role='channel',
                tier='media',
                registered=False,
                health_status='active',
                retirement_recommended=False,
            )
        )
    if any(k in text for k in ('nba', 'lebron', 'lbj', 'basketball', 'trade', 'sports', '湖人', '詹姆斯')):
        dynamic_candidates.append(
            dict(
                source_id='espn_nba_rss',
                name='ESPN NBA News RSS',
                type='rss',
                endpoint='https://www.espn.com/espn/rss/nba/news',
                config={'tier': 'media', 'max_items': 10, 'role': 'channel'},
                reason='NBA roster moves, rumors and official transactions.',
                fact_evidence='',
                role='channel',
                tier='media',
                registered=False,
                health_status='active',
                retirement_recommended=False,
            )
        )

    existing_by_id = {s['source_id']: idx for idx, s in enumerate(recommended)}
    for cand in dynamic_candidates:
        cid = cand['source_id']
        if cid not in existing_by_id:
            recommended.append(cand)
        else:
            idx = existing_by_id[cid]
            if recommended[idx].get('health_status') in ('quiet', 'active') and cid not in approved_set and cand.get('health_status') == 'candidate':
                recommended[idx]['name'] = cand['name']
                recommended[idx]['reason'] = cand['reason']
                recommended[idx]['fact_evidence'] = cand.get('fact_evidence', '')
                recommended[idx]['health_status'] = 'candidate'

    # Optional ON/OFF LLM priority rationale comparison across unapproved candidates
    llm_summary_note = None
    if llm_summary:
        unapproved_branches = [
            s for s in recommended
            if s.get('role') == 'branch' and s['source_id'] not in approved_set and s['source_id'].startswith(f'kw_{topic_slug}_')
        ]
        llm_summary_note, per_source = _summarize_candidates_with_llm(topic, unapproved_branches, current_events, settings)
        for s in recommended:
            if s['source_id'] in per_source:
                s['reason'] = f"🤖 AI 决策建议：{per_source[s['source_id']]}" + (f" ｜ 依据：{s['fact_evidence']}" if s.get('fact_evidence') else '')

    def _sort_key(s):
        sid = s['source_id']
        role = s.get('role', 'channel')
        if role == 'anchor' and sid in (f'lbj_anchor_{topic_slug}', f'mcd_anchor_{topic_slug}'):
            return (0, sid)
        if role == 'branch' or sid.startswith(f'kw_{topic_slug}_'):
            return (1, 0 if s.get('retirement_recommended') else 1, sid)
        if sid == f'gnews_{topic_slug}':
            return (2, sid)
        return (3, sid)

    recommended.sort(key=_sort_key)
    return {
        'recommendation_mode': 'topic_aware_v1',
        'rationale_mode': 'fact_grounded_v2',
        'llm_summary_enabled': bool(llm_summary and llm_summary_note is not None),
        'llm_summary_note': llm_summary_note,
        'sources': recommended,
    }
