"""Production web acquisition layer: SSRF-safe web_fetch, dual-mode WebSearchConnector (Bocha/Tavily/Bing+Google),
WebpageMonitorConnector (snapshot diff with revision IDs), and local search-provider settings helpers.
Zero extra third-party dependencies beyond httpx + Python stdlib.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit
import xml.etree.ElementTree as ET

from dotenv import set_key
import httpx

from .core import AppError
from .domain import now
from .evidence_processing import canonicalize_url, clean_text, parse_published_at
from .llm import is_local_model_url, valid_model_url

_MAX_PAGE_BYTES = 2 * 1024 * 1024  # 2 MB cap per fetched HTML/Markdown document
_MAX_SNAPSHOT_CHARS = 64_000
_MAX_REDIRECTS = 4
_DEFAULT_FETCH_TIMEOUT = 10.0
_DEFAULT_JINA_TIMEOUT = 12.0
_DEFAULT_FETCH_MAX_CHARS = 3200
_DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
)

_BING_MKT_MAP: dict[str, str] = {
    'zh-CN': 'zh-CN',
    'zh-HK': 'zh-HK',
    'en-US': 'en-US',
    'ko': 'ko-KR',
    'ja': 'ja-JP',
}

_BOT_WALL_MARKERS = (
    'cf-browser-verification',
    'just a moment...',
    'attention required! | cloudflare',
    'select all squares containing',
    'verify you are human',
    'enable javascript and cookies to continue',
    'please complete the following challenge',
)

_JSONLD_DATE_RE = re.compile(
    r'"(?:datePublished|dateModified|uploadDate)"\s*:\s*"([^"]{8,40})"',
    re.I,
)
_DOMAIN_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$', re.I)
_ALLOWED_SEARCH_PROVIDERS = ('auto', 'bocha', 'tavily', 'free')
_SENSITIVE_QUERY_KEYS = frozenset({
    'token', 'access_token', 'id_token', 'refresh_token', 'key', 'api_key', 'apikey',
    'sig', 'signature', 'auth', 'authorization', 'secret', 'password', 'pwd', 'session', 'ticket',
})


def _validate_public_url(validate_url, url: str) -> None:
    try:
        validate_url(url, resolve_dns=True, fail_closed_dns=True)
    except TypeError:
        validate_url(url, resolve_dns=True)


def _has_sensitive_query_params(url: str) -> bool:
    try:
        qs = parse_qs(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return True
    return any(k.lower() in _SENSITIVE_QUERY_KEYS for k in qs)


def _url_origin(url: str) -> tuple[str, str, int | None]:
    p = urlsplit(url)
    return (p.scheme.lower(), (p.hostname or '').rstrip('.').lower(), p.port)


class _HtmlContentExtractor(HTMLParser):
    """Lightweight stdlib HTML-to-readable-text extractor with <article>/<main> preference and date metadata."""

    _SKIP_TAGS = frozenset({'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'aside', 'form', 'iframe', 'template'})
    _BLOCK_TAGS = frozenset({'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'blockquote', 'section', 'article', 'pre', 'br'})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._main_depth = 0
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self.title_parts: list[str] = []
        self.meta_published: list[str] = []
        self.meta_modified: list[str] = []
        self.main_lines: list[str] = []
        self.body_lines: list[str] = []
        self._cur_buf: list[str] = []

    def _flush(self) -> None:
        if not self._cur_buf:
            return
        line = ' '.join(''.join(self._cur_buf).split()).strip()
        self._cur_buf.clear()
        if len(line) < 4:
            return
        self.body_lines.append(line)
        if self._main_depth > 0:
            self.main_lines.append(line)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        ad = {k.lower(): (v or '') for k, v in attrs}
        if t == 'script':
            if 'ld+json' in ad.get('type', '').lower():
                self._in_jsonld = True
                self._jsonld_buf.clear()
            self._skip_depth += 1
            return
        if t in self._SKIP_TAGS:
            self._flush()
            self._skip_depth += 1
            return
        if t == 'title':
            self._in_title = True
            return
        if t in ('article', 'main'):
            self._flush()
            self._main_depth += 1
        elif t in self._BLOCK_TAGS:
            self._flush()
        elif t == 'time':
            dt = ad.get('datetime', '').strip()
            if dt:
                self.meta_published.append(dt)
        elif t == 'meta':
            key = (ad.get('property') or ad.get('name') or ad.get('itemprop') or '').lower()
            val = ad.get('content', '').strip()
            if not val:
                return
            if any(k in key for k in ('modified', 'updated')):
                self.meta_modified.append(val)
            elif any(k in key for k in ('published', 'pubdate', 'datepublished', 'dc.date', 'article:published')):
                self.meta_published.append(val)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == 'script':
            if self._in_jsonld:
                blob = ''.join(self._jsonld_buf)
                for m in _JSONLD_DATE_RE.finditer(blob):
                    self.meta_published.append(m.group(1))
                self._in_jsonld = False
                self._jsonld_buf.clear()
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if t in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if t == 'title':
            self._in_title = False
            return
        if t in ('article', 'main'):
            self._flush()
            self._main_depth = max(0, self._main_depth - 1)
        elif t in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth == 0:
            self._cur_buf.append(data)

    def finalize(self) -> tuple[str, str, str | None, str | None]:
        self._flush()
        title = ' '.join(''.join(self.title_parts).split()).strip()[:300]
        chosen = self.main_lines if sum(len(x) for x in self.main_lines) >= 220 else self.body_lines
        # Deduplicate adjacent identical boilerplate lines
        deduped: list[str] = []
        for ln in chosen:
            if not deduped or deduped[-1] != ln:
                deduped.append(ln)
        text = '\n'.join(deduped)
        pub = next((p for raw in self.meta_published if (p := parse_published_at(raw))), None)
        mod = next((p for raw in self.meta_modified if (p := parse_published_at(raw))), None)
        return title, text, pub, mod


def _truncate_text(text: str, max_chars: int) -> str:
    raw = str(text or '').strip()
    if len(raw) <= max_chars:
        return raw
    cut = raw[:max_chars]
    for sep in ('\n', '。', '！', '？', '. ', '! ', '? ', ' '):
        idx = cut.rfind(sep)
        if idx >= int(max_chars * 0.7):
            return cut[: idx + (0 if sep == ' ' else 1)].strip()
    return cut.strip()


def _parse_jina_markdown(raw_text: str) -> tuple[str, str, str | None]:
    """Parse Jina Reader plain/markdown output into (title, clean_body, published_at)."""
    title = ''
    published: str | None = None
    body = raw_text
    if 'Markdown Content:' in raw_text[:2000]:
        head, body = raw_text.split('Markdown Content:', 1)
        for line in head.splitlines():
            if line.startswith('Title:'):
                title = line[6:].strip()[:300]
            elif line.startswith('Published Time:'):
                published = parse_published_at(line[15:].strip())
    # Remove markdown image tags and noisy bare URL blobs while keeping readable link text
    body = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', body)
    body = re.sub(r'\[([^\]]{1,140})\]\([^)]*\)', r'\1', body)
    lines = []
    for ln in body.splitlines():
        s = ' '.join(ln.split()).strip()
        if len(s) < 4:
            continue
        if s.startswith(('http://', 'https://')) and ' ' not in s:
            continue
        if not lines or lines[-1] != s:
            lines.append(s)
    return title, '\n'.join(lines), published


def _stream_bounded_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    validate_url,
    max_bytes: int = _MAX_PAGE_BYTES,
) -> tuple[int, str, str, dict[str, str]]:
    """SSRF-safe, anti-OOM streaming HTTP GET following validated redirects only (strips Auth on cross-origin hops)."""
    current = url
    req_headers = dict(headers)
    prev_origin: tuple[str, str, int | None] | None = None
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_public_url(validate_url, current)
        cur_origin = _url_origin(current)
        if prev_origin is not None and cur_origin != prev_origin:
            req_headers = {k: v for k, v in req_headers.items() if k.lower() not in ('authorization', 'cookie')}
        prev_origin = cur_origin
        with httpx.stream('GET', current, headers=req_headers, timeout=timeout, follow_redirects=False) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get('location', '').strip()
                if loc:
                    current = urljoin(current, loc)
                    continue
            cl = resp.headers.get('content-length')
            if cl and cl.isdigit() and int(cl) > max_bytes:
                raise AppError('SOURCE_PARSE_ERROR', 'Fetched webpage exceeds size limit.', 422)
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    break
            text = buf.decode(resp.encoding or 'utf-8', errors='replace')
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status_code, current, text, resp_headers
    raise AppError('SOURCE_UNAVAILABLE', 'Webpage fetch exceeded maximum redirect hops.', 502)


def _is_google_news_opaque_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or '').lower().rstrip('.')
    return host == 'news.google.com' and '/articles/' in parsed.path


def web_fetch(
    url: str,
    *,
    validate_url,
    timeout: float = _DEFAULT_FETCH_TIMEOUT,
    max_chars: int = _DEFAULT_FETCH_MAX_CHARS,
    allow_jina_fallback: bool = True,
    jina_api_key: str = '',
) -> dict:
    """Shared SSRF-safe web_fetch returning {ok, url, title, content, published_at, modified_at, method, error}.

    Security invariant: _validate_public_url (resolve_dns + fail_closed_dns) ALWAYS runs before direct fetch
    AND before any optional Jina Reader fallback so internal/private/loopback/unresolved/credentialed URLs
    never leave the host.
    """
    clean_u = str(url or '').strip()
    if not clean_u or any(c in clean_u for c in '\r\n\t '):
        return {'ok': False, 'url': clean_u, 'title': '', 'content': '', 'published_at': None, 'modified_at': None, 'method': 'rejected', 'error': 'Invalid URL'}
    # Enforce SSRF boundary upfront (also blocks userinfo credentials, invalid ports, single-label & unresolved DNS)
    _validate_public_url(validate_url, clean_u)
    if _is_google_news_opaque_url(clean_u):
        return {'ok': False, 'url': clean_u, 'title': '', 'content': '', 'published_at': None, 'modified_at': None, 'method': 'skipped_opaque', 'error': 'Opaque Google News redirect URL'}

    headers = {
        'User-Agent': _DEFAULT_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-HK;q=0.8,en-US;q=0.7,en;q=0.6',
    }
    direct_err = None
    needs_fallback = False
    direct_title = ''
    direct_text = ''
    direct_pub: str | None = None
    direct_mod: str | None = None
    final_url = clean_u

    try:
        status, final_url, raw_body, resp_headers = _stream_bounded_get(
            clean_u, headers=headers, timeout=timeout, validate_url=validate_url
        )
        ctype = resp_headers.get('content-type', '').lower()
        last_mod = parse_published_at(resp_headers.get('last-modified', ''))
        if status in (403, 429):
            needs_fallback = True
            direct_err = f'HTTP {status}'
        elif status >= 400:
            # Never forward 401/404/5xx URLs to external Jina Reader
            return {'ok': False, 'url': final_url, 'title': '', 'content': '', 'full_text': '', 'published_at': None, 'modified_at': None, 'method': 'failed', 'error': f'HTTP {status}'}
        elif 'html' in ctype or not ctype:
            lower_head = raw_body[:6000].lower()
            if any(m in lower_head for m in _BOT_WALL_MARKERS):
                needs_fallback = True
                direct_err = 'Bot/JS wall detected'
            else:
                parser = _HtmlContentExtractor()
                parser.feed(raw_body[:600_000])
                direct_title, direct_text, direct_pub, direct_mod = parser.finalize()
                if not direct_mod and last_mod:
                    direct_mod = last_mod
                if len(direct_text) < 180:
                    needs_fallback = True
                    direct_err = 'Extracted content too thin (likely JS SPA)'
        elif 'text/plain' in ctype or 'markdown' in ctype or 'json' in ctype:
            direct_text = raw_body[:_MAX_SNAPSHOT_CHARS]
            direct_mod = last_mod
        else:
            return {'ok': False, 'url': final_url, 'title': '', 'content': '', 'full_text': '', 'published_at': None, 'modified_at': None, 'method': 'unsupported_ctype', 'error': f'Unsupported content-type: {ctype[:40]}'}
    except AppError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        # Do not forward unreachable/DNS-error URLs to third-party Jina Reader
        return {'ok': False, 'url': final_url, 'title': '', 'content': '', 'full_text': '', 'published_at': None, 'modified_at': None, 'method': 'failed', 'error': type(exc).__name__}

    if not needs_fallback and direct_text:
        return {
            'ok': True,
            'url': final_url,
            'title': direct_title,
            'content': _truncate_text(direct_text, max_chars),
            'full_text': direct_text[:_MAX_SNAPSHOT_CHARS],
            'published_at': direct_pub,
            'modified_at': direct_mod,
            'method': 'direct_httpx',
            'error': None,
        }

    # Optional public-only Jina Reader fallback (never forwards private/unresolved/credentialed/token-query URLs)
    target_for_jina = final_url if final_url.startswith(('http://', 'https://')) else clean_u
    parsed_target = urlsplit(target_for_jina)
    target_host = (parsed_target.hostname or '').rstrip('.').lower()
    if (
        allow_jina_fallback
        and target_host != 'r.jina.ai'
        and parsed_target.username is None
        and parsed_target.password is None
        and not _has_sensitive_query_params(target_for_jina)
    ):
        try:
            _validate_public_url(validate_url, target_for_jina)
            jina_headers = {
                'User-Agent': 'TraceRadar-WebFetch/1.0',
                'Accept': 'text/plain',
                'X-Return-Format': 'markdown',
            }
            if jina_api_key and all(0x21 <= ord(c) <= 0x7E for c in jina_api_key.strip()):
                jina_headers['Authorization'] = f'Bearer {jina_api_key.strip()}'
            j_url = f'https://r.jina.ai/{target_for_jina}'
            j_status, _, j_body, _ = _stream_bounded_get(
                j_url,
                headers=jina_headers,
                timeout=max(timeout, _DEFAULT_JINA_TIMEOUT),
                validate_url=validate_url,
            )
            if j_status == 200 and j_body.strip():
                j_title, j_text, j_pub = _parse_jina_markdown(j_body)
                if len(j_text) >= 120:
                    return {
                        'ok': True,
                        'url': target_for_jina,
                        'title': j_title or direct_title,
                        'content': _truncate_text(j_text, max_chars),
                        'full_text': j_text[:_MAX_SNAPSHOT_CHARS],
                        'published_at': j_pub or direct_pub,
                        'modified_at': direct_mod,
                        'method': 'jina_reader',
                        'error': None,
                    }
        except Exception:
            pass

    if direct_text:
        return {
            'ok': True,
            'url': final_url,
            'title': direct_title,
            'content': _truncate_text(direct_text, max_chars),
            'full_text': direct_text[:_MAX_SNAPSHOT_CHARS],
            'published_at': direct_pub,
            'modified_at': direct_mod,
            'method': 'direct_thin',
            'error': None,
        }
    return {
        'ok': False,
        'url': final_url,
        'title': direct_title,
        'content': '',
        'full_text': '',
        'published_at': direct_pub,
        'modified_at': direct_mod,
        'method': 'failed',
        'error': direct_err or 'Unable to fetch webpage text',
    }


def _preserve_existing_revisions(items: list[dict], existing_items: list[dict] | None, build_evidence) -> list[dict]:
    """Preserve previously ingested content/hash for:
    1. Identical (source_id, external_id) keys so repeat runs never raise SOURCE_REVISION (422);
    2. Cross-source identical canonical HTTP(S) URLs so skipping web_fetch still matches exact_hash dedup (0 LLM tokens).
    """
    if not existing_items:
        return items
    existing_by_key: dict[tuple[str, str], dict] = {}
    existing_by_url: dict[str, dict] = {}
    for ex in existing_items:
        if ex.get('source_id') and ex.get('external_id'):
            existing_by_key[(str(ex['source_id']), str(ex['external_id']))] = ex
        cu = canonicalize_url(str(ex.get('url') or ''))
        if cu and cu.startswith(('http://', 'https://')) and not _is_google_news_opaque_url(cu):
            existing_by_url.setdefault(cu, ex)
    if not existing_by_key and not existing_by_url:
        return items
    out: list[dict] = []
    for item in items:
        key = (str(item.get('source_id') or ''), str(item.get('external_id') or ''))
        prior = existing_by_key.get(key)
        if prior is not None:
            if prior.get('content_hash') != item.get('content_hash'):
                out.append(
                    build_evidence(
                        source_id=item['source_id'],
                        external_id=item['external_id'],
                        url=prior.get('url') or item.get('url', ''),
                        title=prior.get('title') or item['title'],
                        content=prior.get('content') or item['content'],
                        published_at=prior.get('published_at') or item['published_at'],
                    )
                )
            else:
                out.append(item)
            continue
        cu = canonicalize_url(str(item.get('url') or ''))
        prior_url = (
            existing_by_url.get(cu)
            if cu and cu.startswith(('http://', 'https://')) and not _is_google_news_opaque_url(cu)
            else None
        )
        if prior_url is not None and prior_url.get('content') and prior_url.get('content_hash') != item.get('content_hash'):
            out.append(
                build_evidence(
                    source_id=item['source_id'],
                    external_id=item['external_id'],
                    url=item.get('url', ''),
                    title=item['title'],
                    content=prior_url['content'],
                    published_at=item['published_at'],
                )
            )
        else:
            out.append(item)
    return out


def enrich_items_with_web_fetch(
    items: list[dict],
    *,
    validate_url,
    build_evidence,
    existing_items: list[dict] | None = None,
    in_run_urls: set[str] | None = None,
    max_fetch: int = 6,
    max_chars: int = _DEFAULT_FETCH_MAX_CHARS,
    allow_jina_fallback: bool = True,
    jina_api_key: str = '',
    force: bool = False,
) -> tuple[list[dict], dict]:
    """Selectively fetch full text ONLY for new unseen items with thin content; preserves stored hash for existing keys."""
    existing_urls: set[str] = set()
    existing_by_key: dict[tuple[str, str], dict] = {}
    for ex in (existing_items or []):
        cu = canonicalize_url(str(ex.get('url') or ''))
        if cu:
            existing_urls.add(cu)
        if ex.get('source_id') and ex.get('external_id'):
            existing_by_key[(str(ex['source_id']), str(ex['external_id']))] = ex

    enriched = _preserve_existing_revisions(list(items), existing_items, build_evidence)
    seen_batch_urls: set[str] = set(in_run_urls or ())
    to_fetch: list[int] = []
    skipped_existing = 0
    for idx, item in enumerate(enriched):
        url = str(item.get('url') or '').strip()
        if not url.startswith(('http://', 'https://')) or _is_google_news_opaque_url(url):
            continue
        cu = canonicalize_url(url)
        key = (str(item.get('source_id') or ''), str(item.get('external_id') or ''))
        if key in existing_by_key or (cu and (cu in existing_urls or cu in seen_batch_urls)):
            skipped_existing += 1
            continue
        content_len = len(clean_text(str(item.get('content') or '')))
        title_len = len(clean_text(str(item.get('title') or '')))
        if not force and content_len >= 480 and content_len > title_len + 180:
            if cu:
                seen_batch_urls.add(cu)
            continue
        if cu:
            seen_batch_urls.add(cu)
        to_fetch.append(idx)
        if len(to_fetch) >= max_fetch:
            break
    if in_run_urls is not None:
        in_run_urls.update(seen_batch_urls)

    stats = {
        'fulltext_attempted': len(to_fetch),
        'fulltext_fetched': 0,
        'fulltext_jina_used': 0,
        'fulltext_skipped_existing': skipped_existing,
        'fulltext_failed': 0,
    }
    if not to_fetch:
        return enriched, stats

    def _job(i: int) -> tuple[int, dict | None]:
        try:
            res = web_fetch(
                enriched[i]['url'],
                validate_url=validate_url,
                max_chars=max_chars,
                allow_jina_fallback=allow_jina_fallback,
                jina_api_key=jina_api_key,
            )
            return i, res
        except Exception:
            return i, None

    with ThreadPoolExecutor(max_workers=min(4, len(to_fetch))) as pool:
        for idx, res in pool.map(_job, to_fetch):
            if res and res.get('ok') and len(res.get('content') or '') >= 180:
                orig = enriched[idx]
                orig_summary = clean_text(str(orig.get('content') or ''))
                fetched_body = res['content']
                merged_content = (
                    f"{orig_summary}\n\n[原文摘录]\n{fetched_body}"
                    if orig_summary and orig_summary not in fetched_body[:300] and len(orig_summary) < 400
                    else fetched_body
                )
                enriched[idx] = build_evidence(
                    source_id=orig['source_id'],
                    external_id=orig['external_id'],
                    url=orig['url'],
                    title=orig['title'] or res.get('title') or orig['url'],
                    content=merged_content[: max_chars + 400],
                    published_at=orig['published_at'],
                )
                stats['fulltext_fetched'] += 1
                if res.get('method') == 'jina_reader':
                    stats['fulltext_jina_used'] += 1
            else:
                stats['fulltext_failed'] += 1
    return enriched, stats


def _extract_bing_real_url(raw_link: str) -> str:
    try:
        parsed = urlsplit(raw_link)
        qs = parse_qs(parsed.query)
        if 'url' in qs and qs['url']:
            candidate = qs['url'][0].strip()
            if candidate.startswith(('http://', 'https://')):
                return candidate
    except ValueError:
        pass
    return raw_link.strip()


_CJK_CHAR_RE = re.compile(r'[\u4e00-\u9fff]')
_JA_KO_SCRIPT_RE = re.compile(r'[\u3040-\u30ff\uac00-\ud7af]')


def _is_non_chinese_query(source_cfg: dict | None) -> bool:
    if not source_cfg:
        return False
    lang = str(source_cfg.get('search_language') or source_cfg.get('language') or '').strip()
    if lang in ('en-US', 'ko', 'ja'):
        return True
    if lang in ('zh-CN', 'zh-HK'):
        return False
    qry = str(source_cfg.get('query') or '').strip()
    if not qry:
        return False
    # Japanese kana or Korean hangul (even mixed with kanji) must route to international/Tavily, not China-only Bocha.
    if _JA_KO_SCRIPT_RE.search(qry):
        return True
    return not bool(_CJK_CHAR_RE.search(qry))


def _resolve_active_search_provider(settings, source_cfg: dict | None = None) -> tuple[str, str]:
    """Return (effective_provider, label) among 'bocha', 'tavily', 'free'."""
    cfg_pref = str((source_cfg or {}).get('search_provider') or '').strip().lower()
    global_pref = str(getattr(settings, 'search_provider', 'auto') or 'auto').strip().lower() if settings else 'auto'
    pref = cfg_pref if cfg_pref in _ALLOWED_SEARCH_PROVIDERS and cfg_pref != 'auto' else global_pref
    has_bocha = bool(settings and str(getattr(settings, 'bocha_api_key', '') or '').strip())
    has_tavily = bool(settings and str(getattr(settings, 'tavily_api_key', '') or '').strip())

    if pref == 'free':
        return 'free', '免 Key 模式（Bing 新闻直链 + Google News + web_fetch）'
    if pref == 'bocha' and has_bocha:
        return 'bocha', '博查全网搜索（Bocha API）'
    if pref == 'tavily' and has_tavily:
        return 'tavily', 'Tavily 全网搜索'
    # Auto mode: route non-Chinese queries to Tavily or international Bing/Google+web_fetch so China-only Bocha doesn't dilute EN/KO/JA recall.
    if source_cfg is not None and _is_non_chinese_query(source_cfg):
        if has_tavily:
            return 'tavily', 'Tavily 全网搜索'
        return 'free', '国际语种自动路由（Bing/Google News + web_fetch）'
    if has_bocha:
        return 'bocha', '博查全网搜索（Bocha API，中文优选 / 外文自动国际路由）'
    if has_tavily:
        return 'tavily', 'Tavily 全网搜索'
    return 'free', '免 Key 模式（Bing 新闻直链 + Google News + web_fetch）'


def _bocha_freshness(after_date: str | None, recency_days: int | None) -> str:
    if after_date:
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if after_date <= today_str:
            return f'{after_date}..{today_str}'
        return 'noLimit'
    if isinstance(recency_days, int) and not isinstance(recency_days, bool):
        if recency_days <= 1:
            return 'oneDay'
        if recency_days <= 7:
            return 'oneWeek'
        if recency_days <= 31:
            return 'oneMonth'
        if recency_days <= 366:
            return 'oneYear'
    return 'noLimit'


def _tavily_time_range(recency_days: int | None) -> str | None:
    if isinstance(recency_days, int) and not isinstance(recency_days, bool):
        if recency_days <= 1:
            return 'day'
        if recency_days <= 7:
            return 'week'
        if recency_days <= 31:
            return 'month'
        if recency_days <= 366:
            return 'year'
    return None


class WebSearchConnector:
    """Dual-mode web_search connector: Bocha / Tavily API when key is configured, or zero-key Bing News RSS
    (direct article URLs) + Google News RSS fallback, paired with selective web_fetch full-text enrichment.
    """

    def __init__(self, *, settings, validate_url, build_evidence, to_utc_dt, rss_connector, gnews_url_builder):
        self.settings = settings
        self.validate_url = validate_url
        self.build_evidence = build_evidence
        self.to_utc_dt = to_utc_dt
        self.rss_connector = rss_connector
        self.gnews_url_builder = gnews_url_builder
        self.last_stats: dict = {}

    def _fetch_bocha(self, query: str, count: int, *, after_date: str | None, recency_days: int | None) -> list[dict]:
        api_key = str(getattr(self.settings, 'bocha_api_key', '') or '').strip()
        base_url = str(getattr(self.settings, 'bocha_base_url', 'https://api.bochaai.com/v1') or 'https://api.bochaai.com/v1').strip().rstrip('/')
        if not valid_model_url(base_url):
            raise AppError('INVALID_SEARCH_CONFIG', '博查搜索 API 地址不合法（仅允许 HTTPS）。', 422)
        endpoint = base_url if base_url.endswith('/web-search') else f'{base_url}/web-search'
        payload = {
            'query': query,
            'freshness': _bocha_freshness(after_date, recency_days),
            'summary': True,
            'count': max(1, min(20, count)),
        }
        local = is_local_model_url(urlsplit(base_url))
        try:
            resp = httpx.post(
                endpoint,
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json=payload,
                timeout=20.0,
                **({'trust_env': False} if local else {}),
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise AppError('SOURCE_UNAVAILABLE', f'博查搜索网络异常：{type(exc).__name__}', 502)
        if resp.status_code in (401, 402, 403):
            raise AppError('SEARCH_AUTH_ERROR', f'博查搜索 API 认证失败或余额不足（HTTP {resp.status_code}），请检查 BOCHA_API_KEY。', 502)
        if resp.is_error:
            raise AppError('SOURCE_HTTP_ERROR', f'博查搜索返回 HTTP {resp.status_code}。', 502)
        try:
            body = resp.json()
        except ValueError:
            raise AppError('SOURCE_PARSE_ERROR', '博查搜索返回非 JSON 响应。', 502)
        if isinstance(body, dict) and isinstance(body.get('code'), int) and body['code'] not in (200, 0):
            raise AppError('SOURCE_HTTP_ERROR', f"博查搜索业务错误（code={body.get('code')}）。", 502)
        data = body.get('data') if isinstance(body, dict) and isinstance(body.get('data'), dict) else body
        web_pages = data.get('webPages') if isinstance(data, dict) else None
        rows = web_pages.get('value') if isinstance(web_pages, dict) else []
        if not isinstance(rows, list):
            return []
        results: list[dict] = []
        for row in rows[:count]:
            if not isinstance(row, dict):
                continue
            url = str(row.get('url') or '').strip()
            title = str(row.get('name') or row.get('title') or '').strip()
            summary = str(row.get('summary') or '').strip()
            snippet = str(row.get('snippet') or '').strip()
            content = summary if len(summary) >= len(snippet) else snippet
            if not (title or content):
                continue
            pub_raw = row.get('datePublished') or row.get('dateLastCrawled') or ''
            pub_iso = parse_published_at(str(pub_raw))
            results.append({'title': title or url, 'url': url, 'content': content or title, 'published_at': pub_iso})
        return results

    def _fetch_tavily(
        self,
        query: str,
        count: int,
        *,
        after_date: str | None,
        recency_days: int | None,
        include_domains: list[str] | None,
    ) -> list[dict]:
        api_key = str(getattr(self.settings, 'tavily_api_key', '') or '').strip()
        base_url = str(getattr(self.settings, 'tavily_base_url', 'https://api.tavily.com') or 'https://api.tavily.com').strip().rstrip('/')
        if not valid_model_url(base_url):
            raise AppError('INVALID_SEARCH_CONFIG', 'Tavily API 地址不合法（仅允许 HTTPS）。', 422)
        endpoint = base_url if base_url.endswith('/search') else f'{base_url}/search'
        payload: dict = {
            'query': query,
            'topic': 'news' if (after_date or (recency_days and recency_days <= 31)) else 'general',
            'max_results': max(1, min(20, count)),
            'include_answer': False,
            'include_raw_content': False,
        }
        if after_date:
            payload['start_date'] = after_date
        else:
            tr = _tavily_time_range(recency_days)
            if tr:
                payload['time_range'] = tr
        if include_domains:
            payload['include_domains'] = include_domains[:10]
        local = is_local_model_url(urlsplit(base_url))
        try:
            resp = httpx.post(
                endpoint,
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json=payload,
                timeout=20.0,
                **({'trust_env': False} if local else {}),
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise AppError('SOURCE_UNAVAILABLE', f'Tavily 搜索网络异常：{type(exc).__name__}', 502)
        if resp.status_code in (401, 402, 403):
            raise AppError('SEARCH_AUTH_ERROR', f'Tavily API 认证失败或配额不足（HTTP {resp.status_code}）。', 502)
        if resp.is_error:
            raise AppError('SOURCE_HTTP_ERROR', f'Tavily 返回 HTTP {resp.status_code}。', 502)
        try:
            body = resp.json()
        except ValueError:
            raise AppError('SOURCE_PARSE_ERROR', 'Tavily 返回非 JSON 响应。', 502)
        rows = body.get('results') if isinstance(body, dict) else []
        if not isinstance(rows, list):
            return []
        results: list[dict] = []
        for row in rows[:count]:
            if not isinstance(row, dict):
                continue
            url = str(row.get('url') or '').strip()
            title = str(row.get('title') or '').strip()
            content = str(row.get('content') or '').strip()
            if not (title or content):
                continue
            pub_iso = parse_published_at(str(row.get('published_date') or ''))
            results.append({'title': title or url, 'url': url, 'content': content or title, 'published_at': pub_iso})
        return results

    def _fetch_free_bing_and_google(
        self,
        source_id: str,
        query: str,
        count: int,
        *,
        language: str | None,
        after_date: str | None,
        after_dt_cutoff: datetime | None,
        cutoff: datetime | None,
        recency_days: int | None,
        as_of: str | None,
        skip_google_news: bool,
    ) -> tuple[list[dict], str]:
        """Zero-key discovery: Bing News RSS (direct article URLs) supplemented with Google News RSS."""
        raw_items: list[dict] = []
        seen_urls: set[str] = set()
        bing_available = False
        provider_used = 'free_bing_rss'
        mkt = _BING_MKT_MAP.get(language or '', '')
        bing_url = f'https://www.bing.com/news/search?q={quote_plus(query)}&format=rss' + (f'&mkt={mkt}' if mkt else '')
        try:
            status, _, xml_text, _ = _stream_bounded_get(
                bing_url,
                headers={'User-Agent': _DEFAULT_USER_AGENT},
                timeout=12.0,
                validate_url=self.validate_url,
            )
            if status == 200 and xml_text.strip():
                root = ET.fromstring(xml_text)
                if root.tag != 'rss':
                    raise ET.ParseError('Bing News did not return RSS XML.')
                bing_available = True
                for entry in root.findall('.//item'):
                    title = (entry.findtext('title') or '').strip()
                    raw_link = (entry.findtext('link') or '').strip()
                    real_url = _extract_bing_real_url(raw_link)
                    desc = (entry.findtext('description') or '').strip()
                    pub_iso = parse_published_at((entry.findtext('pubDate') or '').strip())
                    if not pub_iso or not (title or desc):
                        continue
                    item_dt = self.to_utc_dt(pub_iso)
                    if cutoff and item_dt > cutoff:
                        continue
                    if after_dt_cutoff and item_dt < after_dt_cutoff:
                        continue
                    cu = canonicalize_url(real_url)
                    if cu and cu in seen_urls:
                        continue
                    if cu:
                        seen_urls.add(cu)
                    raw_items.append({'title': title or real_url, 'url': real_url, 'content': desc or title, 'published_at': pub_iso})
                    if len(raw_items) >= count:
                        break
        except (AppError, httpx.HTTPError, ET.ParseError):
            pass

        # Supplement with Google News RSS if post-filtered Bing returned few items or an explicit after_date was requested
        needs_supplement = len(raw_items) < max(3, count // 2) or (after_date and len(raw_items) < count)
        if not skip_google_news and needs_supplement:
            gn_url = self.gnews_url_builder(
                query,
                recency_days=recency_days,
                after_date=after_date,
                locale=language if language in _BING_MKT_MAP else None,
            )
            try:
                gn_evs = self.rss_connector.fetch(
                    {'source_id': source_id, 'endpoint': gn_url, 'config': {'max_items': count}},
                    batch='live',
                    as_of=as_of,
                )
                for ev in gn_evs:
                    if len(raw_items) >= count:
                        break
                    item_dt = self.to_utc_dt(ev['published_at'])
                    if after_dt_cutoff and item_dt < after_dt_cutoff:
                        continue
                    cu = canonicalize_url(ev.get('url', ''))
                    if cu and cu in seen_urls:
                        continue
                    if cu:
                        seen_urls.add(cu)
                    raw_items.append(
                        {
                            'title': ev['title'],
                            'url': ev['url'],
                            'content': ev['content'],
                            'published_at': ev['published_at'],
                        }
                    )
                if not bing_available:
                    provider_used = 'free_google_rss'
                elif gn_evs:
                    provider_used = 'free_bing_and_google_rss'
            except (AppError, httpx.HTTPError):
                if not bing_available:
                    raise
        elif not bing_available:
            raise AppError('SOURCE_UNAVAILABLE', 'Bing News RSS could not be reached.', 502)
        return raw_items, provider_used

    def fetch(self, source: dict, batch: str = 'live', **kwargs) -> list[dict]:
        cfg = source.get('config') or {}
        query = str(cfg.get('query') or source.get('endpoint', '').removeprefix('search://')).strip()
        if not query:
            raise AppError('INVALID_SOURCE', 'Web search source requires a non-empty query.', 422)
        max_items = int(cfg.get('max_items') or 8)
        max_items = max(1, min(20, max_items))
        language = str(cfg.get('search_language') or cfg.get('language') or '').strip() or None
        after_date = str(cfg.get('after_date') or '').strip() or None
        recency_days = cfg.get('recency_days') if isinstance(cfg.get('recency_days'), int) and not isinstance(cfg.get('recency_days'), bool) else None
        include_domains = [d for d in (cfg.get('include_domains') or []) if isinstance(d, str) and _DOMAIN_RE.match(d.strip())]
        fetch_full_text = cfg.get('fetch_full_text', True) is not False
        as_of = kwargs.get('as_of')
        skip_google_news = bool(kwargs.get('skip_google_news'))
        cutoff = self.to_utc_dt(as_of) if as_of else None
        after_dt_cutoff = self.to_utc_dt(f'{after_date}T00:00:00Z') if after_date and re.match(r'^20\d{2}-\d{2}-\d{2}$', after_date) else None

        provider, _ = _resolve_active_search_provider(self.settings, cfg)
        fallback_reason = None
        provider_used = provider

        if provider == 'bocha':
            try:
                raw_items = self._fetch_bocha(query, max_items, after_date=after_date, recency_days=recency_days)
            except AppError as exc:
                if exc.code == 'SEARCH_AUTH_ERROR' or cfg.get('fallback_to_free', True) is False:
                    raise
                fallback_reason = exc.message
                raw_items, provider_used = self._fetch_free_bing_and_google(
                    source['source_id'],
                    query,
                    max_items,
                    language=language,
                    after_date=after_date,
                    after_dt_cutoff=after_dt_cutoff,
                    cutoff=cutoff,
                    recency_days=recency_days,
                    as_of=as_of,
                    skip_google_news=skip_google_news,
                )
        elif provider == 'tavily':
            try:
                raw_items = self._fetch_tavily(
                    query,
                    max_items,
                    after_date=after_date,
                    recency_days=recency_days,
                    include_domains=include_domains or None,
                )
            except AppError as exc:
                if exc.code == 'SEARCH_AUTH_ERROR' or cfg.get('fallback_to_free', True) is False:
                    raise
                fallback_reason = exc.message
                raw_items, provider_used = self._fetch_free_bing_and_google(
                    source['source_id'],
                    query,
                    max_items,
                    language=language,
                    after_date=after_date,
                    after_dt_cutoff=after_dt_cutoff,
                    cutoff=cutoff,
                    recency_days=recency_days,
                    as_of=as_of,
                    skip_google_news=skip_google_news,
                )
        else:
            raw_items, provider_used = self._fetch_free_bing_and_google(
                source['source_id'],
                query,
                max_items,
                language=language,
                after_date=after_date,
                after_dt_cutoff=after_dt_cutoff,
                cutoff=cutoff,
                recency_days=recency_days,
                as_of=as_of,
                skip_google_news=skip_google_news,
            )

        future_filtered = 0
        before_after_filtered = 0
        undated_filtered = 0
        built: list[dict] = []
        seen_ext: set[str] = set()
        for item in raw_items:
            pub_iso = item.get('published_at')
            if not pub_iso:
                if cutoff is not None or after_dt_cutoff is not None:
                    undated_filtered += 1
                    continue
                pub_iso = now()
            item_dt = self.to_utc_dt(pub_iso)
            if cutoff and item_dt > cutoff:
                future_filtered += 1
                continue
            if after_dt_cutoff and item_dt < after_dt_cutoff:
                before_after_filtered += 1
                continue
            cu = canonicalize_url(item.get('url', ''))
            ext_id = cu or hashlib.sha256(f"{item['title']}:{pub_iso}".encode('utf-8')).hexdigest()[:24]
            if ext_id in seen_ext:
                continue
            seen_ext.add(ext_id)
            built.append(
                self.build_evidence(
                    source_id=source['source_id'],
                    external_id=ext_id,
                    url=item.get('url', ''),
                    title=item['title'],
                    content=item['content'],
                    published_at=pub_iso,
                )
            )

        fetch_stats: dict = {}
        existing_items = kwargs.get('existing_items')
        if fetch_full_text and built:
            allow_jina = bool(getattr(self.settings, 'web_fetch_jina_fallback', True)) if self.settings else True
            jina_key = str(getattr(self.settings, 'jina_api_key', '') or '') if self.settings else ''
            built, fetch_stats = enrich_items_with_web_fetch(
                built,
                validate_url=self.validate_url,
                build_evidence=self.build_evidence,
                existing_items=existing_items,
                in_run_urls=kwargs.get('in_run_urls'),
                max_fetch=min(6, max_items),
                allow_jina_fallback=allow_jina,
                jina_api_key=jina_key,
            )
        elif built:
            built = _preserve_existing_revisions(built, existing_items, self.build_evidence)

        self.last_stats = {
            'provider_used': provider_used,
            'returned': len(built),
            'future_filtered': future_filtered,
            **({'before_after_filtered': before_after_filtered} if before_after_filtered else {}),
            **({'undated_filtered': undated_filtered} if undated_filtered else {}),
            **({'fallback_reason': fallback_reason} if fallback_reason else {}),
            **fetch_stats,
        }
        return sorted(built, key=lambda e: (e['published_at'], e['external_id']))


def _is_meaningful_line(line: str) -> bool:
    s = line.strip()
    return len(s) >= 6 or (bool(_CJK_CHAR_RE.search(s)) and len(s) >= 2)


def _compute_webpage_diff(prev_text: str, new_text: str, *, max_chars: int = _DEFAULT_FETCH_MAX_CHARS) -> tuple[bool, str, int, int]:
    """Compute human- and LLM-readable incremental line diff between two cleaned webpage snapshots."""
    prev_lines = [ln.strip() for ln in prev_text.splitlines() if _is_meaningful_line(ln)]
    new_lines = [ln.strip() for ln in new_text.splitlines() if _is_meaningful_line(ln)]
    if prev_lines == new_lines:
        return False, '', 0, 0
    prev_set = set(prev_lines)
    new_set = set(new_lines)
    added = [ln for ln in new_lines if ln not in prev_set]
    removed = [ln for ln in prev_lines if ln not in new_set]
    if not added and not removed:
        return False, '', 0, 0

    parts: list[str] = ['[指定网页内容变更检测]']
    if added:
        parts.append('新增/更新段落：')
        for ln in added[:60]:
            parts.append(f'+ {ln}')
    if removed:
        parts.append('移除/替换旧段落：')
        for ln in removed[:25]:
            parts.append(f'- {ln}')
    diff_text = _truncate_text('\n'.join(parts), max_chars)
    return True, diff_text, len(added), len(removed)


class WebpageMonitorConnector:
    """Monitors a specific URL (official page, promo page, security advisory) via web_fetch + persistent snapshot diff.
    Emits 0 evidence when unchanged; emits incremental diff with transition-unique external_id when changed;
    automatically recovers pending snapshots if a prior run failed before completing assessment.
    """

    def __init__(self, *, settings, snapshot_dir: Path, validate_url, build_evidence, to_utc_dt):
        self.settings = settings
        self.snapshot_dir = snapshot_dir
        self.validate_url = validate_url
        self.build_evidence = build_evidence
        self.to_utc_dt = to_utc_dt
        self.last_stats: dict = {}

    def _snapshot_path(self, topic_id: str, source_id: str) -> Path:
        key = hashlib.sha256(f'{topic_id}:{source_id}'.encode('utf-8')).hexdigest()[:32]
        return self.snapshot_dir / f'{key}.json'

    def _load_snapshot(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _save_snapshot(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.snap-', suffix='.tmp')
        os.close(fd)
        try:
            Path(tmp).write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, path)
        finally:
            if Path(tmp).exists():
                Path(tmp).unlink(missing_ok=True)

    def fetch(self, source: dict, batch: str = 'live', **kwargs) -> list[dict]:
        url = str(source.get('endpoint') or '').strip()
        cfg = source.get('config') or {}
        max_chars = int(cfg.get('max_chars') or _DEFAULT_FETCH_MAX_CHARS)
        max_chars = max(500, min(12000, max_chars))
        allow_jina = cfg.get('allow_jina_fallback', getattr(self.settings, 'web_fetch_jina_fallback', True) if self.settings else True)
        jina_key = str(getattr(self.settings, 'jina_api_key', '') or '') if self.settings else ''

        fetched = web_fetch(
            url,
            validate_url=self.validate_url,
            max_chars=max_chars,
            allow_jina_fallback=bool(allow_jina),
            jina_api_key=jina_key,
        )
        if not fetched.get('ok') or not fetched.get('full_text'):
            raise AppError('SOURCE_UNAVAILABLE', f"网页抓取失败（{fetched.get('error') or 'unknown'}）。", 502)

        full_text = str(fetched['full_text'])[:_MAX_SNAPSHOT_CHARS]
        norm_lines = '\n'.join(ln.strip() for ln in full_text.splitlines() if _is_meaningful_line(ln))
        snap_hash = hashlib.sha256(norm_lines.encode('utf-8')).hexdigest()

        topic_id = str(kwargs.get('topic_id') or '_global')
        snap_path = self._snapshot_path(topic_id, source['source_id'])
        raw_prev = self._load_snapshot(snap_path)
        canon_url = canonicalize_url(url)

        # Reset baseline if source URL was changed to a different site
        if raw_prev and raw_prev.get('url') and raw_prev.get('url') != canon_url:
            raw_prev = None

        # Recover uncommitted snapshot if prior run failed before completing/reviewing last_external_id
        committed_ext_ids: set[tuple[str, str]] | None = kwargs.get('committed_ext_ids')
        prev = raw_prev
        if (
            raw_prev
            and committed_ext_ids is not None
            and raw_prev.get('last_external_id')
            and (source['source_id'], raw_prev['last_external_id']) not in committed_ext_ids
        ):
            prev = raw_prev.get('committed_snapshot') if isinstance(raw_prev.get('committed_snapshot'), dict) else None

        # Check unchanged BEFORE as_of cutoff
        if prev is not None and prev.get('hash') == snap_hash:
            self.last_stats = {'returned': 0, 'mode': 'unchanged', 'changed': False, 'method': fetched['method']}
            return []

        as_of = kwargs.get('as_of')
        cutoff = self.to_utc_dt(as_of) if as_of else None
        raw_ts = fetched.get('modified_at') or fetched.get('published_at') or now()
        if prev and prev.get('last_published_at'):
            try:
                prev_dt = self.to_utc_dt(prev['last_published_at'])
                cur_dt = self.to_utc_dt(raw_ts)
                if cur_dt <= prev_dt:
                    bumped = max(datetime.now(timezone.utc), prev_dt + timedelta(seconds=1))
                    raw_ts = bumped.isoformat()
            except ValueError:
                raw_ts = now()
        if cutoff and self.to_utc_dt(raw_ts) > cutoff:
            self.last_stats = {'returned': 0, 'future_filtered': 1, 'method': fetched['method'], 'changed': False}
            return []

        if prev is None:
            content = _truncate_text(full_text, max_chars)
            title = fetched.get('title') or source.get('name') or url
            ext_id = f'webpage-rev-init-{snap_hash[:16]}'
            self._save_snapshot(
                snap_path,
                {
                    'url': canon_url,
                    'hash': snap_hash,
                    'text': full_text,
                    'last_published_at': raw_ts,
                    'last_external_id': ext_id,
                    'committed_snapshot': None,
                    'updated_at': now(),
                },
            )
            ev = self.build_evidence(
                source_id=source['source_id'],
                external_id=ext_id,
                url=fetched.get('url') or url,
                title=title,
                content=content,
                published_at=raw_ts,
            )
            self.last_stats = {'returned': 1, 'mode': 'initial_snapshot', 'changed': True, 'method': fetched['method']}
            return [ev]

        changed, diff_body, added_cnt, removed_cnt = _compute_webpage_diff(str(prev.get('text') or ''), full_text, max_chars=max_chars)
        if not changed:
            self._save_snapshot(
                snap_path,
                {
                    'url': canon_url,
                    'hash': snap_hash,
                    'text': full_text,
                    'last_published_at': prev.get('last_published_at', raw_ts),
                    'last_external_id': prev.get('last_external_id'),
                    'committed_snapshot': {
                        'url': prev.get('url', canon_url),
                        'hash': prev.get('hash'),
                        'text': prev.get('text', ''),
                        'last_published_at': prev.get('last_published_at', raw_ts),
                        'last_external_id': prev.get('last_external_id'),
                    },
                    'updated_at': now(),
                },
            )
            self.last_stats = {'returned': 0, 'mode': 'noise_only_unchanged', 'changed': False, 'method': fetched['method']}
            return []

        prev_hash = str(prev.get('hash') or '')
        rev_digest = hashlib.sha256(f'{prev_hash}:{snap_hash}:{diff_body}'.encode('utf-8')).hexdigest()[:16]
        ext_id = f'webpage-rev-diff-{rev_digest}'
        committed_snap = {
            'url': prev.get('url', canon_url),
            'hash': prev_hash,
            'text': str(prev.get('text') or ''),
            'last_published_at': prev.get('last_published_at', raw_ts),
            'last_external_id': prev.get('last_external_id'),
        }
        self._save_snapshot(
            snap_path,
            {
                'url': canon_url,
                'hash': snap_hash,
                'text': full_text,
                'last_published_at': raw_ts,
                'last_external_id': ext_id,
                'committed_snapshot': committed_snap,
                'updated_at': now(),
            },
        )
        base_title = fetched.get('title') or source.get('name') or url
        ev = self.build_evidence(
            source_id=source['source_id'],
            external_id=ext_id,
            url=fetched.get('url') or url,
            title=f'{base_title}（页面更新 +{added_cnt} 段）',
            content=diff_body,
            published_at=raw_ts,
        )
        self.last_stats = {
            'returned': 1,
            'mode': 'diff_revision',
            'changed': True,
            'added_lines': added_cnt,
            'removed_lines': removed_cnt,
            'method': fetched['method'],
        }
        return [ev]


def search_settings_status(settings) -> dict:
    """Return safe search configuration summary (NEVER leaks API keys or key fragments)."""
    active_provider, active_label = _resolve_active_search_provider(settings)
    return {
        'provider': str(getattr(settings, 'search_provider', 'auto') or 'auto'),
        'active_provider': active_provider,
        'active_label': active_label,
        'bocha_configured': bool(str(getattr(settings, 'bocha_api_key', '') or '').strip()),
        'tavily_configured': bool(str(getattr(settings, 'tavily_api_key', '') or '').strip()),
        'jina_configured': bool(str(getattr(settings, 'jina_api_key', '') or '').strip()),
        'web_fetch_jina_fallback': bool(getattr(settings, 'web_fetch_jina_fallback', True)),
    }


def save_search_settings(settings, body) -> dict:
    """Persist optional search provider settings to local .env atomically; mutate in-memory settings ONLY after write succeeds."""
    provider = str(getattr(body, 'provider', None) or settings.search_provider or 'auto').strip().lower()
    if provider not in _ALLOWED_SEARCH_PROVIDERS:
        raise AppError('INVALID_SEARCH_CONFIG', f'Unsupported search provider: {provider}', 422)

    updates: dict[str, str] = {'SEARCH_PROVIDER': provider}
    pending_attrs: dict[str, object] = {'search_provider': provider}

    for attr, env_name in (
        ('bocha_api_key', 'BOCHA_API_KEY'),
        ('tavily_api_key', 'TAVILY_API_KEY'),
        ('jina_api_key', 'JINA_API_KEY'),
    ):
        val = getattr(body, attr, None)
        if val is not None:
            cleaned = val.strip()
            if cleaned and not all(0x21 <= ord(c) <= 0x7E for c in cleaned):
                raise AppError('INVALID_SEARCH_CONFIG', f'{env_name} must contain printable ASCII characters only.', 422)
            pending_attrs[attr] = cleaned
            updates[env_name] = cleaned

    fb = getattr(body, 'web_fetch_jina_fallback', None)
    if fb is not None:
        pending_attrs['web_fetch_jina_fallback'] = bool(fb)
        updates['WEB_FETCH_JINA_FALLBACK'] = 'true' if fb else 'false'

    target = Path(settings.env_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.search-', suffix='.tmp')
    os.close(fd)
    try:
        Path(temporary).write_text(target.read_text(encoding='utf-8') if target.exists() else '', encoding='utf-8')
        for k, v in updates.items():
            set_key(temporary, k, v)
        os.replace(temporary, target)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink(missing_ok=True)

    for attr, val in pending_attrs.items():
        setattr(settings, attr, val)
    return search_settings_status(settings)
