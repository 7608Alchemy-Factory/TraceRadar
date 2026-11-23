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
_PUBLISHER_SUFFIX_RE = re.compile(r'\s+[-–—|]\s+[^-–—|]{1,40}$')
_QUOTED_PHRASE_RE = re.compile(r'[「『《“"]([A-Za-z0-9\u4e00-\u9fff\s\-\.\+\$]{2,18})[」』》”"]')
# General proper-noun / alphanumeric brand phrase extractor (strictly domain-agnostic).
_LATIN_TOKEN = r'(?:[A-Z\u00C0-\u00DE][A-Za-z0-9\u00C0-\u024F&\-\.]{1,18}|\d+[A-Za-z]{2,8}|[A-Za-z]{1,4}\d{1,4})'
_LATIN_ENTITY_RE = re.compile(rf'(?<![A-Za-z0-9])({_LATIN_TOKEN}(?:\s+{_LATIN_TOKEN}){{0,2}})(?![A-Za-z0-9])')
_CJK_RUN_RE = re.compile(r'[\u4e00-\u9fff]{2,12}')
_CJK_SPLIT_PARTICLES_RE = re.compile(r'[的了在與和及為將于於被以至起後中上下不可有是稱指或等但對向從跟同就都已再也仍並并讓给給把]')

# Universal English, temporal, and major news-outlet stopwords (strictly domain-agnostic).
_GENERIC_PROPER_STOP = {
    'the', 'and', 'for', 'with', 'from', 'into', 'over', 'after', 'before', 'this', 'that', 'these', 'those',
    'will', 'would', 'could', 'should', 'what', 'when', 'where', 'which', 'who', 'why', 'how', 'new', 'top',
    'best', 'most', 'more', 'all', 'its', 'his', 'her', 'their', 'our', 'your', 'has', 'have', 'had', 'are',
    'was', 'were', 'been', 'not', 'but', 'can', 'may', 'out', 'off', 'now', 'here', 'still', 'just', 'first',
    'last', 'next', 'final', 'official', 'update', 'updates', 'breaking', 'report', 'reports', 'reported',
    'sources', 'source', 'news', 'daily', 'post', 'times', 'press', 'media', 'journal', 'review', 'weekly',
    'today', 'yesterday', 'tomorrow', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'january', 'february', 'march', 'april', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
    'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
    'espn', 'yahoo', 'sports', 'cbs', 'nbc', 'abc', 'bbc', 'cnn', 'reuters', 'bloomberg', 'forbes', 'athletic',
    'bleacher', 'hoopshype', 'si', 'sohu', 'thepaper', 'sina', 'netease', 'hupu', 'weibo', 'zhihu', 'bilibili',
    'baidu', 'toutiao', 'hk01', 'hket', 'ufood', 'uhk', 'am730', 'mingpao', 'singtao', 'bastille', 'hypebeast',
    'lifestyle', 'asia', 'hong', 'kong', 'china', 'usa', 'com', 'cn', 'hk', 'org', 'net', 'app', 'inc', 'ltd',
    'year', 'years', 'month', 'months', 'week', 'weeks', 'day', 'days', 'time', 'part', 'step', 'plan', 'list',
    'guide', 'deal', 'deals', 'free', 'agency', 'agent', 'agents', 'trade', 'trades', 'season', 'summer', 'league',
    'game', 'games', 'team', 'teams', 'player', 'players', 'star', 'stars', 'podcast', 'video', 'photo', 'watch',
}

# Universal Chinese non-entity function/generic words (strictly domain-agnostic).
_CJK_STOP_PHRASES = {
    '香港', '中國', '中国', '全球', '今日', '昨日', '明日', '近日', '近期', '目前', '最新', '全新', '正式', '首次',
    '宣布', '公布', '推出', '登場', '登场', '上線', '上线', '開始', '开始', '結束', '结束', '回歸', '回归', '報道',
    '报道', '表示', '指出', '透露', '回應', '回应', '確認', '确认', '消息', '進展', '进展', '動態', '动态', '官方',
    '活動', '活动', '系列', '期間', '期间', '限定', '限時', '限时', '優惠', '优惠', '免費', '免费', '折扣', '價格',
    '价格', '相關', '相关', '繼續', '继续', '持續', '持续', '同時', '同时', '已經', '已经', '我們', '我们', '他們',
    '他们', '以及', '包括', '成為', '成为', '獲得', '获得', '進行', '进行', '部分', '全部', '主要', '重要', '可能',
    '預計', '预计', '據報', '据报', '傳聞', '传闻', '媒體', '媒体', '網友', '网友', '用戶', '用户', '市場', '市场',
    '公司', '品牌', '合作', '聯乘', '联乘', '聯名', '联名', '獨家', '独家', '全港', '分店', '門店', '门店',
    '套餐', '新品', '商品', '服務', '服务', '功能', '版本', '系統', '系统', '問題', '问题', '情況', '情况', '方式',
}

# Generic, domain-agnostic terminal status cues (never team/brand specific).
_TERMINAL_STATUS_RE = re.compile(
    r'\b(patched|resolved|closed|expired|cancelled|canceled|completed|archived|ended)\b|'
    r'(已结束|已結束|已过期|已過期|已结案|已結案|已修复|已修復|已取消|已终止|已終止|已完结|已完結)',
    re.I,
)


def _strip_publisher_suffix(text: str) -> str:
    return _PUBLISHER_SUFFIX_RE.sub('', str(text or '').strip()).strip()


def _find_grounded_fact_snippet(
    aliases: list[str],
    current_events: list[dict] | None,
    relevant_rows: list[dict] | None,
) -> tuple[str, str]:
    """0-Extra-Token rationale: reuse already-generated LLM key_facts / supported_claims mentioning the candidate."""
    for ev in reversed(list(current_events or [])):
        facts = list(((ev.get('state') or {}).get('key_facts') or []))
        for f in reversed(facts):
            fact_text = str(f.get('fact') or '').strip()
            low = fact_text.lower()
            if fact_text and any(_term_in_text(a, low) for a in aliases):
                return 'key_fact', fact_text[:220]

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

    for row in reversed(list(relevant_rows or [])):
        ev = row.get('evidence') or {}
        title = _strip_publisher_suffix(str(ev.get('title') or ''))
        blob = f"{title} {ev.get('content', '')}".lower()
        if title and any(_term_in_text(a, blob) for a in aliases):
            pub = str(ev.get('published_at') or '')[:10]
            return f'headline@{pub}' if pub else 'headline', title[:180]
    return '', ''


def _advise_watchlist_with_llm(
    topic: dict,
    unapproved_candidates: list[dict],
    approved_branches: list[dict],
    current_events: list[dict] | None,
    notifications: list[dict] | None,
    settings,
) -> tuple[str | None, dict[str, str], dict[str, str], list[dict]]:
    """Optional ON/OFF single-call general AI Watchlist advisor:
    1. Compares & ranks unapproved candidates strictly from fact_evidence.
    2. Semantically judges which approved branches are settled/expired/obsolete across ANY domain.
    3. Proposes up to 2 open-domain follow-up queries grounded in current_events/key_facts.
    """
    if not settings or (not unapproved_candidates and not approved_branches and not current_events):
        return None, {}, {}, []
    api_key = getattr(settings, 'api_key', '')
    base_url = getattr(settings, 'base_url', '')
    model = getattr(settings, 'model', '')
    if not (api_key and base_url and model):
        return None, {}, {}, []

    payload = {
        'topic': {'title': topic.get('title', ''), 'description': topic.get('description', '')},
        'current_events': [
            {
                'title': e.get('title', ''),
                'version': e.get('version', 0),
                'status': str((e.get('state') or {}).get('current_status', ''))[:280],
                'recent_facts': [
                    str(f.get('fact', ''))[:160]
                    for f in list(((e.get('state') or {}).get('key_facts') or []))[-3:]
                ],
            }
            for e in (current_events or [])[:8]
        ],
        'recent_milestones': [
            {
                'priority': n.get('priority'),
                'reason_codes': n.get('reason_codes') or [],
                'message': str(n.get('message') or '')[:180],
            }
            for n in (notifications or [])[-6:]
        ],
        'unapproved_candidates': [
            {
                'source_id': c['source_id'],
                'label': (c.get('config') or {}).get('branch_label') or c['name'],
                'fact_evidence': c.get('fact_evidence') or c.get('reason', ''),
            }
            for c in unapproved_candidates[:8]
        ],
        'approved_branches': [
            {
                'source_id': b['source_id'],
                'label': (b.get('config') or {}).get('branch_label') or b['name'],
                'health_status': b.get('health_status'),
                'fact_evidence': b.get('fact_evidence') or '',
            }
            for b in approved_branches[:10]
        ],
    }
    system = (
        'You are a domain-agnostic Watchlist advisor for an autonomous event tracker. '
        'Using ONLY the supplied topic, current_events, recent_milestones, unapproved_candidates, and approved_branches, return ONLY JSON:\n'
        '{\n'
        '  "summary_note": "1-2 concise Chinese sentences highlighting top candidates to approve AND any branches that can now be archived",\n'
        '  "per_source": {"<unapproved_source_id>": "1 concise Chinese sentence grounded strictly in fact_evidence explaining whether to approve or skip"},\n'
        '  "retire_branches": [{"source_id": "<approved_branch_source_id>", "reason": "1 concise Chinese sentence explaining why this branch is now settled, expired, or superseded based on current_events"}],\n'
        '  "suggested_queries": [{"label": "concise entity or sub-topic name", "query": "2-4 focused search keywords", "reason": "1 concise Chinese sentence grounded in current_events"}]\n'
        '}\n'
        'Rules: Never invent facts outside the input. Only put a branch into retire_branches if current_events clearly show its storyline ended, expired, or lost out to a finalized resolution (silence alone is NOT a reason to retire). Return at most 2 suggested_queries only if they are genuinely missing from unapproved_candidates and approved_branches.'
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
            timeout=25.0,
        )
        if resp.status_code != 200:
            return None, {}, {}, []
        parsed = json.loads(resp.json()['choices'][0]['message']['content'])
        if not isinstance(parsed, dict):
            return None, {}, {}, []
        note = str(parsed.get('summary_note') or '').strip()[:380] or None
        raw_map = parsed.get('per_source') if isinstance(parsed.get('per_source'), dict) else {}
        per_source = {str(k): str(v).strip()[:240] for k, v in raw_map.items() if str(v or '').strip()}
        approved_ids = {b['source_id'] for b in approved_branches}
        retire_map: dict[str, str] = {}
        for item in (parsed.get('retire_branches') or []):
            if isinstance(item, dict):
                sid = str(item.get('source_id') or '').strip()
                rsn = str(item.get('reason') or '').strip()[:240]
                if sid in approved_ids and rsn:
                    retire_map[sid] = rsn
        suggested: list[dict] = []
        for item in (parsed.get('suggested_queries') or [])[:2]:
            if isinstance(item, dict):
                lbl = str(item.get('label') or '').strip()[:40]
                qry = str(item.get('query') or '').strip()[:60]
                rsn = str(item.get('reason') or '').strip()[:220]
                if lbl and qry:
                    suggested.append({'label': lbl, 'query': qry, 'reason': rsn})
        return note, per_source, retire_map, suggested
    except Exception:
        return None, {}, {}, []


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
        core = re.sub(r'（[^）]*）|\([^)]*\)', ' ', title)
        core = re.sub(r'[，。！？、；：“”‘’【】\[\]|/\\:]+', ' ', core)
        core = _CJK_FILLER_RE.sub(' ', core)
        ascii_entities = re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{1,19}(?![A-Za-z0-9])', core)
        if len(ascii_entities) >= 2:
            dedup_ascii: list[str] = []
            for tok in ascii_entities:
                if tok.lower() not in {x.lower() for x in dedup_ascii}:
                    dedup_ascii.append(tok)
            return ' '.join(dedup_ascii[:3])
        parts = [p.strip() for p in re.split(r'[\s与和及]+', core) if p.strip()]
        compact = ' '.join(parts[:3])
        return compact[:24].strip() or title[:24]
    text = f'{title} {desc}'.lower()
    tokens = [t for t in tokenize(text, drop_stopwords=True) if len(t) >= 2 and not _CJK_RE.search(t)][:6]
    return ' '.join(tokens) if tokens else (title[:48] or 'news')


def _build_google_news_rss_url(query: str, *, recency_days: int | None = None, prefer_zh: bool = False) -> str:
    q = ' '.join(str(query or '').split()).strip() or 'news'
    if isinstance(recency_days, int) and 1 <= recency_days <= 365 and 'when:' not in q:
        q = f'{q} when:{recency_days}d'
    if prefer_zh or _CJK_RE.search(q):
        return f'https://news.google.com/rss/search?q={quote_plus(q)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    return f'https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en'


def _extract_general_terms(text: str) -> list[str]:
    """Domain-agnostic 0-Token phrase extraction: quoted phrases, capitalized/branded Latin phrases, and clean CJK phrases."""
    cleaned = _strip_publisher_suffix(text)
    found: list[str] = []

    for m in _QUOTED_PHRASE_RE.findall(cleaned):
        phrase = ' '.join(m.split()).strip()
        if 2 <= len(phrase) <= 16 and not phrase.isdigit() and phrase not in _CJK_STOP_PHRASES:
            found.append(phrase)

    for m in _LATIN_ENTITY_RE.findall(cleaned):
        phrase = ' '.join(m.split()).strip(' .-&')
        if len(phrase) < 2:
            continue
        words = phrase.split()
        while words and words[0].lower() in _GENERIC_PROPER_STOP:
            words.pop(0)
        while words and words[-1].lower() in _GENERIC_PROPER_STOP:
            words.pop()
        if not words:
            continue
        phrase = ' '.join(words)
        low = phrase.lower()
        if len(phrase) < 2 or low in _GENERIC_PROPER_STOP or phrase.isdigit():
            continue
        if len(words) == 1 and re.fullmatch(r's\d{1,2}|20\d{2}', low):
            continue
        found.append(phrase)

    for run in _CJK_RUN_RE.findall(cleaned):
        for seg in _CJK_SPLIT_PARTICLES_RE.split(run):
            seg = seg.strip()
            if 2 <= len(seg) <= 5 and seg not in _CJK_STOP_PHRASES:
                found.append(seg)

    seen: set[str] = set()
    uniq: list[str] = []
    for item in found:
        k = item.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(item)
    return uniq


def _discover_general_candidates(
    topic: dict,
    relevant_rows: list[dict],
    current_events: list[dict] | None,
) -> list[dict]:
    """100% domain-agnostic 0-Token branch/entity discovery from already-assessed relevant articles and LLM event titles."""
    title_str = str(topic.get('title', '') or '')
    title_lower = title_str.lower()
    anchor_tokens = {t.lower() for t in _extract_clean_query(topic).split() if len(t) >= 2}

    event_title_terms: set[str] = set()
    for ev in (current_events or []):
        for term in _extract_general_terms(str(ev.get('title') or '')):
            event_title_terms.add(term.lower())

    stats: dict[str, dict] = {}
    for row in relevant_rows:
        ev = row.get('evidence') or {}
        pub = str(ev.get('published_at') or '')[:10]
        headline = _strip_publisher_suffix(str(ev.get('title') or ''))
        snippet = str(ev.get('content') or '')[:260]
        doc_terms = _extract_general_terms(headline)
        for qm in _QUOTED_PHRASE_RE.findall(snippet):
            phrase = ' '.join(qm.split()).strip()
            if 2 <= len(phrase) <= 16 and phrase not in _CJK_STOP_PHRASES:
                doc_terms.append(phrase)

        seen_in_doc: set[str] = set()
        for term in doc_terms:
            low = term.lower()
            if low in seen_in_doc or low in anchor_tokens or low in title_lower:
                continue
            words = low.split()
            if words and all(_term_in_text(w, title_lower) for w in words):
                continue
            seen_in_doc.add(low)
            entry = stats.get(low)
            if not entry:
                stats[low] = {
                    'term': term,
                    'low': low,
                    'count': 0,
                    'first_date': pub,
                    'in_event_title': low in event_title_terms,
                    'is_cjk_only': bool(_CJK_RE.search(term)) and not re.search(r'[A-Za-z0-9]', term),
                    'is_quoted': bool(re.search(rf'[「『《“"]{re.escape(term)}[」』》”"]', f'{headline} {snippet}')),
                }
                entry = stats[low]
            entry['count'] += 1
            if pub and (not entry['first_date'] or pub < entry['first_date']):
                entry['first_date'] = pub
            if low in event_title_terms:
                entry['in_event_title'] = True

    filtered = [
        info for info in stats.values()
        if (not info['is_cjk_only']) or info['is_quoted'] or info['count'] >= 2 or info['in_event_title']
    ]

    filtered.sort(key=lambda x: (-len(x['term']), -x['count'], x['low']))
    merged: list[dict] = []
    for cand in filtered:
        parent = next(
            (
                m for m in merged
                if cand['low'] != m['low']
                and _term_in_text(cand['low'], m['low'])
                and (m['count'] >= cand['count'] or m['in_event_title'])
            ),
            None,
        )
        if parent is not None:
            parent['count'] = max(parent['count'], cand['count'])
            if cand['first_date'] and (not parent['first_date'] or cand['first_date'] < parent['first_date']):
                parent['first_date'] = cand['first_date']
            continue
        merged.append(cand)

    merged.sort(
        key=lambda x: (
            0 if (x['in_event_title'] or x['is_quoted']) else 1,
            -x['count'],
            x['first_date'],
            x['low'],
        )
    )
    return merged[:8]


def _audit_source_health(
    src: dict,
    evidence_decisions: list[dict] | None,
    current_events: list[dict] | None = None,
) -> tuple[str, str | None]:
    """100% general 0-Token Silence vs. Noise Audit + generic terminal-status check for branch sources."""
    cfg = src.get('config') or {}
    if cfg.get('role') != 'branch':
        return 'active', None

    sid = src['source_id']
    assessed_irrelevant = 0
    useful = 0
    recent_outcomes: list[tuple[str, bool]] = []
    linked_event_ids: set[str] = set()
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
                assoc = ass.get('association') or {}
                if assoc.get('event_id'):
                    linked_event_ids.add(str(assoc['event_id']))
                for dec in (ass.get('event_decisions') or []):
                    d_assoc = dec.get('association') or {}
                    if d_assoc.get('event_id'):
                        linked_event_ids.add(str(d_assoc['event_id']))

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

    # Rule 2: Generic terminal-status check (nuanced semantic closure is judged by AI ON mode)
    if linked_event_ids and current_events:
        matched_events = [e for e in current_events if str(e.get('event_id')) in linked_event_ids]
        if matched_events and all(
            _TERMINAL_STATUS_RE.search(str((e.get('state') or {}).get('current_status', '') or ''))
            for e in matched_events
        ):
            return (
                'milestone_closed',
                '🏁 该支线关联事件已进入完结/过期状态，可取消勾选归档以保持词表精简。',
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
    """100% general topic-scoped Watchlist & Channel recommender:
    - Zero hardcoded domain/team/brand tables.
    - 0-Token entity/phrase discovery grounded in LLM EventState.key_facts.
    - 0-Token Silence vs. High-Noise statistical health audit.
    - Optional ON/OFF single-call AI advisor for candidate priority comparison, semantic branch archival, and open-domain query suggestions.
    """
    title_str = str(topic.get('title', '') or '')
    desc_str = str(topic.get('description', '') or '')
    text = f'{title_str} {desc_str}'.lower()
    query = _extract_clean_query(topic)
    prefer_zh = bool(_CJK_RE.search(title_str))
    topic_slug = str(topic.get('topic_id', 'default'))[:8].lower()
    approved_set = set(approved_source_ids or set())

    # Pre-collect relevant evidence chronologically for 0-Token discovery & fact grounding.
    relevant_rows = []
    for row in (evidence_decisions or []):
        st = row.get('status')
        ass = row.get('assessment') or {}
        if st == 'review' or (st == 'completed' and ass.get('is_relevant') is True):
            relevant_rows.append(row)
    relevant_rows.sort(key=lambda r: str((r.get('evidence') or {}).get('published_at', '')))

    # Data-driven detection: if this topic is anchored on an offline replay corpus, branches query that same corpus.
    active_replay_anchor = next(
        (
            s for s in repo_sources
            if s['source_id'] in approved_set
            and s.get('type') == 'replay'
            and (s.get('config') or {}).get('corpus_path')
            and (s.get('config') or {}).get('role') != 'branch'
        ),
        None,
    )

    # Filter out other topics' private generated sources so cross-topic tabs stay clean.
    recommended = []
    for src in repo_sources:
        sid = src['source_id']
        if sid.startswith('kw_') and not sid.startswith(f'kw_{topic_slug}_') and sid not in approved_set:
            continue
        if sid.startswith('gnews_') and sid != f'gnews_{topic_slug}' and sid not in approved_set:
            continue
        if (sid.startswith('replay_') or sid.startswith('anchor_')) and sid not in approved_set:
            continue
        cfg = src.get('config') or {}
        tier = cfg.get('tier', 'simulated')
        role = cfg.get('role') or (
            'anchor' if (sid == f'gnews_{topic_slug}' or (src.get('type') == 'replay' and sid in approved_set)) else 'channel'
        )
        base_reason = (
            'Registered source in workspace.'
            if src['type'] != 'fixture'
            else 'Registered simulated demo source.'
        )
        health_status, health_note = _audit_source_health(
            src,
            evidence_decisions,
            current_events=current_events,
        )
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

    dynamic_candidates: list[dict] = []

    # General 0-Token Candidate Discovery (identical algorithm for ANY replay corpus OR ANY live RSS topic!)
    if relevant_rows:
        discovered = _discover_general_candidates(topic, relevant_rows, current_events)
        anchor_prefix = ' '.join(query.split()[:2]).strip() or query
        for info in discovered:
            term = info['term']
            slug = hashlib.sha256(term.lower().encode('utf-8')).hexdigest()[:8]
            sid = f'kw_{topic_slug}_{slug}'
            _, fact_snip = _find_grounded_fact_snippet([term], current_events, relevant_rows)
            reason = (
                f'依据（已提炼事实）：{fact_snip}'
                if fact_snip
                else f"0-Token 实体发现：{info['first_date']} 起在相关报道中提及「{term}」（共 {info['count']} 篇）。"
            )
            if active_replay_anchor:
                replay_path = active_replay_anchor['config']['corpus_path']
                dynamic_candidates.append(
                    dict(
                        source_id=sid,
                        name=f"扩展支线：{term}（{info['first_date']} 首次提及 · {info['count']} 篇）",
                        type='replay',
                        endpoint=f'replay://{replay_path}',
                        config={
                            'corpus_path': replay_path,
                            'query_any': [term],
                            'tier': 'media',
                            'role': 'branch',
                            'branch_label': term,
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
            else:
                kw_q = f'{anchor_prefix} {term}'.strip() if not _term_in_text(anchor_prefix, term.lower()) else term
                kw_q = ' '.join(kw_q.split())
                dynamic_candidates.append(
                    dict(
                        source_id=sid,
                        name=f"扩展关键词：{kw_q}（{info['first_date']} 提及 · {info['count']} 篇）",
                        type='rss',
                        endpoint=_build_google_news_rss_url(kw_q, recency_days=30, prefer_zh=prefer_zh),
                        config={'tier': 'aggregator', 'max_items': 8, 'role': 'branch', 'branch_label': term},
                        reason=reason,
                        fact_evidence=fact_snip,
                        role='branch',
                        tier='aggregator',
                        registered=False,
                        health_status='candidate',
                        retirement_recommended=False,
                    )
                )

    # Always provide general live keyword RSS anchor + general world news channel
    dynamic_candidates.extend(
        [
            dict(
                source_id=f'gnews_{topic_slug}',
                name=f'实时关键词 RSS：{query[:40]}',
                type='rss',
                endpoint=_build_google_news_rss_url(
                    query,
                    recency_days=30 if any(k in text for k in ('最新', '近期', 'latest', 'recent')) else None,
                    prefer_zh=prefer_zh,
                ),
                config={'tier': 'aggregator', 'max_items': 10, 'role': 'anchor'},
                reason='实时 Google News 关键词订阅（自动识别中/英文语区，无需预设领域词表）。',
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

    existing_by_id = {s['source_id']: idx for idx, s in enumerate(recommended)}
    for cand in dynamic_candidates:
        cid = cand['source_id']
        if cid not in existing_by_id:
            recommended.append(cand)
            existing_by_id[cid] = len(recommended) - 1
        else:
            idx = existing_by_id[cid]
            if recommended[idx].get('health_status') in ('quiet', 'active') and cid not in approved_set and cand.get('health_status') == 'candidate':
                recommended[idx]['name'] = cand['name']
                recommended[idx]['reason'] = cand['reason']
                recommended[idx]['fact_evidence'] = cand.get('fact_evidence', '')
                recommended[idx]['health_status'] = 'candidate'

    # Optional ON/OFF General AI Advisor: priority comparison + AI-judged semantic archival + open-domain query proposals
    llm_summary_note = None
    if llm_summary:
        unapproved_branches = [
            s for s in recommended
            if s.get('role') == 'branch' and s['source_id'] not in approved_set and s['source_id'].startswith(f'kw_{topic_slug}_')
        ]
        approved_branches = [
            s for s in recommended
            if s.get('role') == 'branch' and s['source_id'] in approved_set
        ]
        llm_summary_note, per_source, retire_map, suggested_queries = _advise_watchlist_with_llm(
            topic, unapproved_branches, approved_branches, current_events, notifications, settings
        )
        for s in recommended:
            sid = s['source_id']
            if sid in per_source:
                s['reason'] = f"🤖 AI 决策建议：{per_source[sid]}" + (f" ｜ 依据：{s['fact_evidence']}" if s.get('fact_evidence') else '')
            if sid in retire_map:
                s['health_status'] = 'milestone_closed'
                s['retirement_recommended'] = True
                if not s['name'].startswith('🏁'):
                    s['name'] = f"🏁 [AI 建议归档] {s['name']}"
                s['reason'] = f"🏁 AI 结案/归档判断：{retire_map[sid]}"
        existing_labels = {
            str((s.get('config') or {}).get('branch_label', '')).lower()
            for s in recommended
            if s.get('role') == 'branch'
        }
        for prop in suggested_queries:
            if prop['label'].lower() in existing_labels:
                continue
            slug = hashlib.sha256(prop['query'].lower().encode('utf-8')).hexdigest()[:8]
            sid = f'kw_{topic_slug}_ai_{slug}'
            if sid in existing_by_id:
                continue
            if active_replay_anchor:
                replay_path = active_replay_anchor['config']['corpus_path']
                terms = [t for t in prop['query'].split() if len(t) >= 2] or [prop['label']]
                recommended.append(
                    dict(
                        source_id=sid,
                        name=f"AI 扩展建议：{prop['label']}",
                        type='replay',
                        endpoint=f'replay://{replay_path}',
                        config={
                            'corpus_path': replay_path,
                            'query_any': terms,
                            'tier': 'media',
                            'role': 'branch',
                            'branch_label': prop['label'],
                        },
                        reason=f"🤖 AI 主动建议：{prop['reason']}",
                        fact_evidence=prop['reason'],
                        role='branch',
                        tier='media',
                        registered=False,
                        health_status='candidate',
                        retirement_recommended=False,
                    )
                )
            else:
                recommended.append(
                    dict(
                        source_id=sid,
                        name=f"AI 扩展建议：{prop['query']}",
                        type='rss',
                        endpoint=_build_google_news_rss_url(prop['query'], recency_days=30, prefer_zh=prefer_zh),
                        config={'tier': 'aggregator', 'max_items': 8, 'role': 'branch', 'branch_label': prop['label']},
                        reason=f"🤖 AI 主动建议：{prop['reason']}",
                        fact_evidence=prop['reason'],
                        role='branch',
                        tier='aggregator',
                        registered=False,
                        health_status='candidate',
                        retirement_recommended=False,
                    )
                )
            existing_by_id[sid] = len(recommended) - 1

    def _sort_key(s):
        sid = s['source_id']
        role = s.get('role', 'channel')
        if role == 'anchor' and sid != f'gnews_{topic_slug}':
            return (0, sid)
        if role == 'branch' or sid.startswith(f'kw_{topic_slug}_'):
            return (1, 0 if s.get('retirement_recommended') else 1, sid)
        if sid == f'gnews_{topic_slug}':
            return (2, sid)
        return (3, sid)

    recommended.sort(key=_sort_key)
    return {
        'recommendation_mode': 'topic_aware_v1',
        'rationale_mode': 'general_fact_grounded_v3',
        'llm_summary_enabled': bool(llm_summary and llm_summary_note is not None),
        'llm_summary_note': llm_summary_note,
        'sources': recommended,
    }
