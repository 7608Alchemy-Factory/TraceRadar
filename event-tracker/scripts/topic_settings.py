"""Export and merge TraceRadar topic settings, without copying evidence or run history."""

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

FORMAT = 'traceradar-topic-settings-v1'
ROOT = Path(__file__).resolve().parents[1]
TOPIC_FIELDS = ('topic_id', 'title', 'description', 'tags', 'exclusions', 'notification_preferences')
SOURCE_FIELDS = ('source_id', 'name', 'type', 'endpoint', 'config')


def connect(path, mode):
    db = sqlite3.connect(f'{path.resolve().as_uri()}?mode={mode}', uri=True, timeout=5)
    db.execute('PRAGMA foreign_keys = ON')
    return db


def export_settings(database, output):
    topics = []
    with closing(connect(database, 'ro')) as db:
        db.execute('BEGIN')
        for identity, raw in db.execute('SELECT id, data FROM topics ORDER BY rowid'):
            topic = json.loads(raw)
            settings = {key: topic[key] for key in TOPIC_FIELDS if key in topic}
            settings['approved_sources'] = [
                {key: source[key] for key in SOURCE_FIELDS if key in source}
                for source in (
                    json.loads(row[0]) for row in db.execute(
                        'SELECT s.data FROM sources s JOIN topic_sources ts ON s.id = ts.source_id '
                        'WHERE ts.topic_id = ? ORDER BY s.id', (identity,)
                    )
                )
            ]
            topics.append(settings)
    with output.open('x', encoding='utf-8') as file:
        json.dump({'format': FORMAT, 'topics': topics}, file, ensure_ascii=False, indent=2)
        file.write('\n')
    print(f'Exported {len(topics)} topic settings to {output.resolve()}')


def import_settings(database, input_file, dry_run):
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.connectors import validate_source_definition
    from app.core import AppError
    from app.domain import Source, Topic, now
    from pydantic import ValidationError

    bundle = json.loads(input_file.read_text(encoding='utf-8-sig'))
    if not isinstance(bundle, dict) or bundle.get('format') != FORMAT or not isinstance(bundle.get('topics'), list):
        raise ValueError('Unsupported topic settings file.')

    added = []
    skipped = []
    with closing(connect(database, 'rw')) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for index, record in enumerate(bundle['topics'], 1):
            if not isinstance(record, dict) or not isinstance(record.get('approved_sources', []), list):
                raise ValueError(f'Invalid topic entry at position {index}.')
            stamp = now()
            try:
                topic = Topic.model_validate({
                    **{key: record[key] for key in TOPIC_FIELDS if key in record},
                    'status': 'active' if record.get('approved_sources') else 'draft',
                    'created_at': stamp, 'updated_at': stamp,
                }).model_dump(mode='json')
            except ValidationError as exc:
                fields = ', '.join('.'.join(map(str, issue['loc'])) for issue in exc.errors(include_input=False))
                raise ValueError(f'Invalid fields in topic entry {index}: {fields}') from None

            identity = topic['topic_id']
            if not identity or db.execute('SELECT 1 FROM topics WHERE id = ?', (identity,)).fetchone():
                if not identity:
                    raise ValueError(f'Topic entry {index} has an empty topic ID.')
                skipped.append(topic['title'])
                continue

            try:
                sources = [
                    validate_source_definition(Source.model_validate(source).model_dump(mode='json'), ROOT / 'fixtures')
                    for source in record.get('approved_sources', [])
                ]
            except (AppError, ValidationError) as exc:
                message = exc.message if isinstance(exc, AppError) else 'Invalid source configuration.'
                raise ValueError(f'Topic entry {index}: {message}') from None

            db.execute('INSERT INTO topics (id, data) VALUES (?, ?)', (identity, json.dumps(topic, ensure_ascii=False)))
            for source in sources:
                source_id = source['source_id']
                existing = db.execute('SELECT data FROM sources WHERE id = ?', (source_id,)).fetchone()
                if existing and json.loads(existing[0]) != source:
                    if source['type'] == 'fixture':
                        raise ValueError(f'Bundled fixture source {source_id} differs from the target registry.')
                    digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode('utf-8')).hexdigest()[:20]
                    source_id = f'{source_id[:36]}_import_{digest}'
                    source['source_id'] = source_id
                    existing = db.execute('SELECT data FROM sources WHERE id = ?', (source_id,)).fetchone()
                    if existing and json.loads(existing[0]) != source:
                        raise ValueError(f'Imported source ID collision: {source_id}')
                if not existing:
                    db.execute('INSERT INTO sources (id, data) VALUES (?, ?)',
                               (source_id, json.dumps(source, ensure_ascii=False)))
                db.execute('INSERT OR IGNORE INTO topic_sources (topic_id, source_id, approved_at) VALUES (?, ?, ?)',
                           (identity, source_id, stamp))
            added.append(topic['title'])
        if dry_run:
            db.rollback()

    print(f'{"Would add" if dry_run else "Added"} {len(added)} topics. Existing topic IDs skipped: {len(skipped)}.')
    for title in added:
        print(f'  + {title}')
    for title in skipped:
        print(f'  = {title}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    export = commands.add_parser('export')
    export.add_argument('--database', required=True, type=Path)
    export.add_argument('--output', required=True, type=Path)
    merge = commands.add_parser('import')
    merge.add_argument('--database', required=True, type=Path)
    merge.add_argument('--input', required=True, type=Path)
    merge.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'export':
            export_settings(args.database, args.output)
        else:
            import_settings(args.database, args.input, args.dry_run)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
