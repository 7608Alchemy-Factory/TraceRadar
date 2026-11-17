"""Evidence cleaning, timestamp parsing, URL normalization, deduplication and lexical pre-filtering."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    'fbclid', 'gclid', 'yclid', 'dclid', 'msclkid', 'mc_cid', 'mc_eid',
    'igshid', 'si', 'spm', 'ref', 'ref_src', 'source', 'ved', 'usg', 'oc',
}

STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'have',
    'in', 'is', 'it', 'its', 'of', 'on', 'or', 'that', 'the', 'to', 'was', 'were',
    'will', 'with', 'this', 'these', 'those', 'about', 'after', 'before', 'into',
}

_SCRIPT_STYLE_RE = re.compile(r'<(script|style)\b[^>]*>.*?</\1>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')
_WORD_RE = re.compile(r'[a-z0-9]+|[\u4e00-\u9fff]')
MAX_CLEAN_TEXT_CHARS = 24000


def clean_text(raw: str | None, max_chars: int = MAX_CLEAN_TEXT_CHARS) -> str:
    """Strip HTML tags/entities, normalize whitespace, and bound extreme payloads."""
    if not raw:
        return ''
    text = _SCRIPT_STYLE_RE.sub(' ', raw)
    text = _TAG_RE.sub(' ', text)
    text = html.unescape(text)
    normalized = ' '.join(text.split())
    if max_chars > 0 and len(normalized) > max_chars:
        return normalized[:max_chars].rstrip()
    return normalized


def canonicalize_url(url: str | None) -> str:
    """Normalize URL by dropping tracking params, fragments, and default ports."""
    if not url:
        return ''
    raw = url.strip()
    if not raw:
        return ''
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == 'http' and netloc.endswith(':80')) or (scheme == 'https' and netloc.endswith(':443')):
        netloc = netloc.rsplit(':', 1)[0]
    filtered_query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith('utm_') and k.lower() not in TRACKING_QUERY_KEYS
    ]
    filtered_query.sort()
    path = parts.path.rstrip('/') if parts.path and parts.path != '/' else (parts.path or '')
    return urlunsplit((scheme, netloc, path, urlencode(filtered_query, doseq=True), ''))


def parse_published_at(value: str | None) -> str | None:
    """Parse ISO-8601 or RFC-2822 timestamps into UTC ISO-8601 strings; return None if invalid."""
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def compute_content_hash(text: str) -> str:
    """Return SHA-256 hex digest of whitespace-normalized text."""
    return hashlib.sha256(clean_text(text).encode('utf-8')).hexdigest()


def tokenize(text: str, drop_stopwords: bool = False) -> list[str]:
    """Tokenize English words/numbers and CJK unigrams + bigrams."""
    cleaned = clean_text(text).lower()
    raw_tokens = _WORD_RE.findall(cleaned)
    tokens: list[str] = []
    cjk_buf: list[str] = []
    for tok in raw_tokens:
        if '\u4e00' <= tok <= '\u9fff':
            cjk_buf.append(tok)
            tokens.append(tok)
            if len(cjk_buf) >= 2:
                tokens.append(cjk_buf[-2] + cjk_buf[-1])
        else:
            cjk_buf.clear()
            if drop_stopwords and tok in STOPWORDS:
                continue
            tokens.append(tok)
    return tokens


def compute_simhash(tokens: list[str]) -> int:
    """Compute deterministic 64-bit SimHash over a token sequence."""
    if not tokens:
        return 0
    weights = [0] * 64
    for tok in tokens:
        digest = hashlib.blake2b(tok.encode('utf-8'), digest_size=8).digest()
        value = int.from_bytes(digest, 'big')
        for bit in range(64):
            weights[bit] += 1 if (value >> bit) & 1 else -1
    fingerprint = 0
    for bit, score in enumerate(weights):
        if score >= 0:
            fingerprint |= 1 << bit
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Return Hamming distance between two 64-bit fingerprints."""
    return (a ^ b).bit_count()


def jaccard_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    return len(tokens_a & tokens_b) / len(union)


def find_duplicate_match(
    candidate: dict,
    existing_items: list[dict],
    *,
    enable_near_duplicate: bool = True,
) -> dict | None:
    """Match exact hash, canonical URL, or near-duplicate text against existing evidence."""
    cand_hash = candidate.get('content_hash') or compute_content_hash(candidate.get('content', ''))
    for item in existing_items:
        if item.get('content_hash') == cand_hash:
            return {
                'canonical_evidence_id': item.get('canonical_evidence_id') or item['evidence_id'],
                'reason': 'exact_hash',
                'similarity': 1.0,
            }
    if not enable_near_duplicate:
        return None

    cand_url = canonicalize_url(candidate.get('url', ''))
    cand_tokens_list = tokenize(f"{candidate.get('title', '')} {candidate.get('content', '')}", drop_stopwords=True)
    cand_tokens = set(cand_tokens_list)
    if len(cand_tokens) < 4:
        return None
    cand_simhash = compute_simhash(cand_tokens_list)

    best_match: dict | None = None
    best_score = 0.0
    for item in existing_items:
        item_tokens_list = tokenize(f"{item.get('title', '')} {item.get('content', '')}", drop_stopwords=True)
        item_tokens = set(item_tokens_list)
        if len(item_tokens) < 4:
            continue
        jaccard = jaccard_similarity(cand_tokens, item_tokens)
        item_url = canonicalize_url(item.get('url', ''))
        if cand_url and item_url and cand_url == item_url and cand_url.startswith(('http://', 'https://')) and jaccard >= 0.45:
            return {
                'canonical_evidence_id': item.get('canonical_evidence_id') or item['evidence_id'],
                'reason': 'canonical_url',
                'similarity': round(jaccard, 4),
            }
        dist = hamming_distance(cand_simhash, compute_simhash(item_tokens_list))
        if jaccard >= 0.75 or (dist <= 5 and jaccard >= 0.62):
            if jaccard > best_score:
                best_score = jaccard
                best_match = {
                    'canonical_evidence_id': item.get('canonical_evidence_id') or item['evidence_id'],
                    'reason': 'near_duplicate',
                    'similarity': round(jaccard, 4),
                    'hamming_distance': dist,
                }
    return best_match


def score_relevance(topic: dict, evidence: dict, threshold: float = 0.08) -> dict:
    """Lightweight lexical overlap pre-filter between topic goal and evidence."""
    topic_tokens = set(tokenize(f"{topic.get('title', '')} {topic.get('description', '')}", drop_stopwords=True))
    ev_tokens = set(tokenize(f"{evidence.get('title', '')} {evidence.get('content', '')}", drop_stopwords=True))
    if not topic_tokens or not ev_tokens:
        return {'score': 0.0, 'matched_terms': [], 'passed': False}
    matched = sorted(topic_tokens & ev_tokens)
    score = len(matched) / len(topic_tokens)
    return {
        'score': round(score, 4),
        'matched_terms': matched[:10],
        'passed': score >= threshold or len(matched) >= 2,
    }
