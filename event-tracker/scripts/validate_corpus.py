"""Validate benchmark / replay news corpus JSON or JSONL files for all team members."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.evidence_processing import (  # noqa: E402
    canonicalize_url,
    clean_text,
    compute_content_hash,
    find_duplicate_match,
    parse_published_at,
)


def load_records(path: Path) -> list[dict]:
    text = path.read_text(encoding='utf-8')
    if path.suffix.lower() == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f'{path}: root JSON value must be a list of evidence objects.')
    return data


def validate_file(path: Path) -> tuple[list[str], dict]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()
    accepted: list[dict] = []
    near_dups = 0
    exact_dups = 0

    records = load_records(path)
    for idx, item in enumerate(records):
        prefix = f'{path.name}[{idx}]'
        if not isinstance(item, dict):
            errors.append(f'{prefix}: record must be an object')
            continue
        source_id = str(item.get('source_id', '')).strip()
        external_id = str(item.get('external_id', '')).strip()
        title = clean_text(str(item.get('title', '')))
        content = clean_text(str(item.get('content', '')))
        pub = parse_published_at(item.get('published_at'))
        url = canonicalize_url(str(item.get('url', '')))

        if not source_id:
            errors.append(f'{prefix}: missing source_id')
        if not external_id:
            errors.append(f'{prefix}: missing external_id')
        if not title:
            errors.append(f'{prefix}: missing title')
        if not content:
            errors.append(f'{prefix}: missing content')
        if not pub:
            errors.append(f'{prefix}: invalid or missing published_at ({item.get("published_at")!r})')
        if source_id and external_id:
            key = (source_id, external_id)
            if key in seen_keys:
                errors.append(f'{prefix}: duplicate (source_id, external_id) = {key}')
            seen_keys.add(key)

        candidate = {
            'evidence_id': f'{path.stem}-{idx}',
            'source_id': source_id,
            'external_id': external_id,
            'url': url,
            'title': title,
            'content': content,
            'published_at': pub or '',
            'content_hash': compute_content_hash(content),
        }
        dup = find_duplicate_match(candidate, accepted, enable_near_duplicate=True)
        if dup:
            if dup['reason'] == 'exact_hash':
                exact_dups += 1
            else:
                near_dups += 1
        accepted.append(candidate)

    stats = {
        'file': str(path),
        'total_records': len(records),
        'exact_duplicates': exact_dups,
        'near_duplicates': near_dups,
        'error_count': len(errors),
    }
    return errors, stats


def main():
    parser = argparse.ArgumentParser(description='Validate raw news corpus JSON/JSONL files.')
    parser.add_argument('paths', nargs='+', help='Files or directories to validate')
    args = parser.parse_args()

    targets: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            targets.extend(sorted([*p.rglob('*.json'), *p.rglob('*.jsonl')]))
        elif p.exists():
            targets.append(p)
        else:
            raise SystemExit(f'Path not found: {p}')

    all_errors: list[str] = []
    for target in targets:
        errs, stats = validate_file(target)
        print(json.dumps(stats, ensure_ascii=False))
        all_errors.extend(errs)

    if all_errors:
        print('VALIDATION FAILED:', file=sys.stderr)
        for err in all_errors:
            print(' -', err, file=sys.stderr)
        raise SystemExit(1)
    print(f'PASS: validated {len(targets)} file(s) with 0 errors.')


if __name__ == '__main__':
    main()
