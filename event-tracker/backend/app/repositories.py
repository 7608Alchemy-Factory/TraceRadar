"""All SQL lives here. Connections are short-lived and never shared across threads."""
import json
import sqlite3
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core import AppError
from .domain import uid, now, unique_topic_tags
from .llm import PROMPT_VERSION

LIVE_SOURCE_TYPES = {'rss', 'web_search', 'webpage'}


def utc_stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def pending_review_decisions(assessment):
    if 'event_decisions' not in assessment:
        return [assessment]
    return [decision for decision in assessment['event_decisions'] if decision['requires_review']
            or decision['conflicts'] or decision['novelty'] == 'conflict' or decision['association']['type'] == 'unresolved']


class Repository:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, sources):
        with self.connection() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_versions VALUES(1);
            CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS topic_sources(
              topic_id TEXT REFERENCES topics(id), source_id TEXT REFERENCES sources(id),
              approved_at TEXT NOT NULL, PRIMARY KEY(topic_id,source_id));
            CREATE TABLE IF NOT EXISTS evidence(
              id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id), external_id TEXT,
              hash TEXT NOT NULL, canonical_id TEXT REFERENCES evidence(id), data TEXT NOT NULL,
              UNIQUE(source_id,external_id));
            CREATE INDEX IF NOT EXISTS evidence_hash ON evidence(hash);
            CREATE TABLE IF NOT EXISTS topic_evidence(
              topic_id TEXT REFERENCES topics(id), evidence_id TEXT REFERENCES evidence(id),
              status TEXT NOT NULL, assessment TEXT, error TEXT,
              PRIMARY KEY(topic_id,evidence_id));
            CREATE TABLE IF NOT EXISTS events(
              id TEXT PRIMARY KEY, topic_id TEXT REFERENCES topics(id), version INTEGER NOT NULL,
              data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY, topic_id TEXT REFERENCES topics(id), status TEXT NOT NULL,
              request_key TEXT, request_body TEXT, data TEXT NOT NULL,
              UNIQUE(topic_id,request_key));
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(topic_id)
              WHERE status IN ('pending','running');
            CREATE TABLE IF NOT EXISTS transitions(
              id TEXT PRIMARY KEY, event_id TEXT REFERENCES events(id), run_id TEXT REFERENCES runs(id),
              idempotency_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_evidence(
              event_id TEXT REFERENCES events(id), evidence_id TEXT REFERENCES evidence(id),
              relation TEXT CHECK(relation IN ('supporting','conflicting','duplicate')),
              PRIMARY KEY(event_id,evidence_id));
            CREATE TABLE IF NOT EXISTS notifications(
              id TEXT PRIMARY KEY, topic_id TEXT REFERENCES topics(id),
              event_id TEXT REFERENCES events(id), transition_id TEXT UNIQUE REFERENCES transitions(id),
              data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS steps(
              id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), sequence INTEGER,
              data TEXT NOT NULL, UNIQUE(run_id,sequence));
            CREATE TABLE IF NOT EXISTS review_resolutions(
              topic_id TEXT, evidence_id TEXT, data TEXT NOT NULL,
              PRIMARY KEY(topic_id,evidence_id),
              FOREIGN KEY(topic_id,evidence_id) REFERENCES topic_evidence(topic_id,evidence_id));
            INSERT OR IGNORE INTO schema_versions VALUES(2);
            CREATE TABLE IF NOT EXISTS run_inputs(
              run_id TEXT REFERENCES runs(id), position INTEGER, evidence TEXT NOT NULL, outcome TEXT,
              PRIMARY KEY(run_id,position));
            INSERT OR IGNORE INTO schema_versions VALUES(3);
            CREATE TABLE IF NOT EXISTS topic_schedules(
              topic_id TEXT PRIMARY KEY REFERENCES topics(id) ON DELETE CASCADE,
              next_at TEXT, last_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
              error TEXT, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS schedules_due ON topic_schedules(next_at);
            CREATE TABLE IF NOT EXISTS notification_reads(
              notification_id TEXT PRIMARY KEY REFERENCES notifications(id) ON DELETE CASCADE,
              read_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(4);
            ''')
            for source in sources:
                db.execute('INSERT OR IGNORE INTO sources VALUES(?,?)', (source['source_id'], dump(source)))
        for run in self.active_runs():
            if run['trigger_type'] == 'scheduled' and run['status'] == 'pending':
                continue
            self.update_run(run['run_id'], status='failed', completed_at=now(),
                            error={'code': 'interrupted', 'message': 'Process stopped; run again safely.'})

    def get(self, table, identity):
        assert table in {'topics', 'sources', 'events', 'runs', 'evidence'}
        with self.connection() as db:
            row = db.execute(f'SELECT data FROM {table} WHERE id=?', (identity,)).fetchone()
        if not row:
            raise AppError('NOT_FOUND', f'{table} resource not found.', 404)
        return json.loads(row['data'])

    def list_topics(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM topics ORDER BY rowid')]

    def save_topic(self, topic):
        with self.connection(True) as db:
            db.execute('INSERT INTO topics VALUES(?,?)', (topic['topic_id'], dump(topic)))
        return topic

    def patch_topic(self, identity, changes):
        with self.connection(True) as db:
            row = db.execute('SELECT data FROM topics WHERE id=?', (identity,)).fetchone()
            if not row:
                raise AppError('NOT_FOUND', 'Topic not found.', 404)
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (identity,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before changing the tracking goal.')
            topic = json.loads(row[0])
            topic.update(changes, updated_at=now())
            db.execute('UPDATE topics SET data=? WHERE id=?', (dump(topic), identity))
        return topic

    def delete_topic(self, identity):
        """Delete topic-owned state in one transaction; sources and evidence can be shared."""
        with self.connection(True) as db:
            if not db.execute('SELECT 1 FROM topics WHERE id=?', (identity,)).fetchone():
                raise AppError('NOT_FOUND', 'Topic not found.', 404)
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (identity,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before deleting the topic.')
            db.execute('DELETE FROM review_resolutions WHERE topic_id=?', (identity,))
            db.execute('DELETE FROM notifications WHERE topic_id=?', (identity,))
            db.execute('DELETE FROM transitions WHERE event_id IN (SELECT id FROM events WHERE topic_id=?)', (identity,))
            db.execute('DELETE FROM event_evidence WHERE event_id IN (SELECT id FROM events WHERE topic_id=?)', (identity,))
            db.execute('DELETE FROM events WHERE topic_id=?', (identity,))
            db.execute('DELETE FROM steps WHERE run_id IN (SELECT id FROM runs WHERE topic_id=?)', (identity,))
            db.execute('DELETE FROM run_inputs WHERE run_id IN (SELECT id FROM runs WHERE topic_id=?)', (identity,))
            db.execute('DELETE FROM runs WHERE topic_id=?', (identity,))
            db.execute('DELETE FROM topic_evidence WHERE topic_id=?', (identity,))
            db.execute('DELETE FROM topic_sources WHERE topic_id=?', (identity,))
            db.execute('DELETE FROM topics WHERE id=?', (identity,))

    def sources(self, topic_id=None):
        with self.connection() as db:
            if topic_id:
                rows = db.execute('''SELECT s.data FROM sources s JOIN topic_sources t
                    ON s.id=t.source_id WHERE t.topic_id=? ORDER BY s.id''', (topic_id,))
            else:
                rows = db.execute('SELECT data FROM sources ORDER BY id')
            return [json.loads(r[0]) for r in rows]

    def register_source(self, source):
        with self.connection(True) as db:
            db.execute('INSERT INTO sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (source['source_id'], dump(source)))
        return source

    def approve(self, topic_id, ids):
        with self.connection(True) as db:
            row = db.execute('SELECT data FROM topics WHERE id=?', (topic_id,)).fetchone()
            if not row:
                raise AppError('NOT_FOUND', 'Topic not found.', 404)
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before changing sources.')
            known = {r[0] for r in db.execute('SELECT id FROM sources')}
            if not set(ids) <= known:
                raise AppError('INVALID_SOURCE', 'Only registered fixture sources can be approved.', 422)
            db.execute('DELETE FROM topic_sources WHERE topic_id=?', (topic_id,))
            db.executemany('INSERT INTO topic_sources VALUES(?,?,?)', [(topic_id, s, now()) for s in sorted(set(ids))])
            topic = json.loads(row[0])
            topic.update(status='active' if ids else 'draft', updated_at=now())
            db.execute('UPDATE topics SET data=? WHERE id=?', (dump(topic), topic_id))
        return self.sources(topic_id)

    def create_run(self, topic_id, request, key, model, options=None):
        with self.connection(True) as db:
            return self._create_run(db, topic_id, request, key, model, options)

    def _create_run(self, db, topic_id, request, key, model, options=None, *, scheduled_for=None):
        effective_request = dict(request)
        if options:
            effective_request['options'] = options
        if key:
            row = db.execute('SELECT request_body,data FROM runs WHERE topic_id=? AND request_key=?', (topic_id, key)).fetchone()
            if row:
                if row[0] != dump(effective_request):
                    raise AppError('IDEMPOTENCY_CONFLICT', 'This key was used with a different request.')
                return json.loads(row[1]), False
        if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
            raise AppError('TOPIC_RUN_ACTIVE', 'This topic already has an active run.')
        topic_row = db.execute('SELECT data FROM topics WHERE id=?', (topic_id,)).fetchone()
        if not topic_row:
            raise AppError('NOT_FOUND', 'Topic not found.', 404)
        topic = json.loads(topic_row[0])
        sources = [json.loads(r[0]) for r in db.execute('''SELECT s.data FROM sources s JOIN topic_sources ts
            ON s.id=ts.source_id WHERE ts.topic_id=? ORDER BY s.id''', (topic_id,))]
        if not sources:
            raise AppError('NO_APPROVED_SOURCE', 'Approve a source before running.')
        if options and options.get('source_ids') is not None:
            selected=set(options['source_ids'])
            if not selected or not selected<={s['source_id'] for s in sources}:
                raise AppError('INVALID_RUN_SOURCES','本次运行只能选择该主题已批准的来源。',422)
            sources=[s for s in sources if s['source_id'] in selected]
        snapshot = {'topic': topic, 'sources': sources}
        if options:
            snapshot['options'] = options
        run = dict(run_id=uid(), topic_id=topic_id, trigger_type='scheduled' if scheduled_for else 'manual', status='pending',
                   **request, model=model, prompt_version=PROMPT_VERSION if request['provider'] == 'qwen' else 'mock-demo-v1', started_at=None,
                   completed_at=None, counts={}, error=None, snapshot=snapshot)
        if scheduled_for:
            run['scheduled_for'] = scheduled_for
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run['run_id'], topic_id, 'pending', key, dump(effective_request), dump(run)))
        return run, True

    def save_schedule(self, topic_id, body, current):
        stamp = utc_stamp(current)
        times = sorted(utc_stamp(value) for value in body.times)
        starts_at = utc_stamp(body.starts_at) if body.starts_at else stamp
        if (times and times[0] <= stamp) or (body.starts_at and starts_at <= stamp):
            raise AppError('SCHEDULE_DATE_PAST', 'Choose future dates, or select start now.', 422)
        with self.connection(True) as db:
            if not db.execute('SELECT 1 FROM topics WHERE id=?', (topic_id,)).fetchone():
                raise AppError('NOT_FOUND', 'Topic not found.', 404)
            if not self._live_source_ids(db, topic_id):
                raise AppError('NO_LIVE_SOURCE', 'Approve an RSS, web search, or monitored webpage source.', 422)
            plan = dict(mode=body.mode, times=times, agent_mode=body.agent_mode,
                        interval_minutes=body.interval_minutes, starts_at=starts_at if body.mode == 'interval' else None)
            db.execute('''INSERT INTO topic_schedules(topic_id,next_at,data) VALUES(?,?,?)
                ON CONFLICT(topic_id) DO UPDATE SET next_at=excluded.next_at, data=excluded.data, error=NULL''',
                (topic_id, times[0] if times else starts_at, dump(plan)))

    def clear_schedule(self, topic_id):
        with self.connection(True) as db:
            db.execute('DELETE FROM topic_schedules WHERE topic_id=?', (topic_id,))

    def schedules(self):
        with self.connection() as db:
            rows = db.execute('''SELECT s.*, r.status AS last_run_status
                FROM topic_schedules s LEFT JOIN runs r ON r.id=s.last_run_id
                ORDER BY s.next_at IS NULL, s.next_at, s.topic_id''')
            return [
                dict(json.loads(row['data']), topic_id=row['topic_id'], next_at=row['next_at'],
                     last_run_id=row['last_run_id'], last_run_status=row['last_run_status'], error=row['error'])
                for row in rows
            ]

    def due_schedule_ids(self, current):
        with self.connection() as db:
            return [row[0] for row in db.execute(
                'SELECT topic_id FROM topic_schedules WHERE next_at<=? ORDER BY next_at', (utc_stamp(current),))]

    @staticmethod
    def _live_source_ids(db, topic_id):
        return [row['id'] for row in db.execute('''SELECT s.id, json_extract(s.data, '$.type') AS type
            FROM sources s JOIN topic_sources t ON t.source_id=s.id WHERE t.topic_id=? ORDER BY s.id''',
            (topic_id,)) if row['type'] in LIVE_SOURCE_TYPES]

    def enqueue_due_schedule(self, topic_id, current, *, model, model_ready):
        stamp = utc_stamp(current)
        with self.connection(True) as db:
            row = db.execute('SELECT * FROM topic_schedules WHERE topic_id=? AND next_at<=?', (topic_id, stamp)).fetchone()
            if not row:
                return None
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                return None
            source_ids = self._live_source_ids(db, topic_id)
            error = 'NO_LIVE_SOURCE' if not source_ids else 'QWEN_NOT_CONFIGURED' if not model_ready else None
            if error:
                if error != row['error']:
                    db.execute('UPDATE topic_schedules SET error=? WHERE topic_id=?', (error, topic_id))
                return None
            plan = json.loads(row['data'])
            due = row['next_at']
            if plan['mode'] == 'interval':
                interval = timedelta(minutes=plan['interval_minutes'])
                original = datetime.fromisoformat(due)
                offset = (current - original) // interval
                scheduled_for = utc_stamp(original + offset * interval)
                next_at = utc_stamp(original + (offset + 1) * interval)
            else:
                scheduled_for = max(value for value in plan['times'] if value <= stamp)
                next_at = next((value for value in plan['times'] if value > stamp), None)
            options = {'source_ids': source_ids}
            if plan['agent_mode']:
                options['agent_mode'] = True
            run, _ = self._create_run(
                db, topic_id, {'fixture_batch_id': 'all', 'provider': 'qwen'}, None, model, options,
                scheduled_for=scheduled_for,
            )
            db.execute('UPDATE topic_schedules SET next_at=?, last_run_id=?, error=NULL WHERE topic_id=?',
                       (next_at, run['run_id'], topic_id))
            return run['run_id']

    def resume_run(self, original, model, key=None):
        topic_id, original_id = original['topic_id'], original['run_id']
        request = {'resume_of': original_id}
        with self.connection(True) as db:
            if not db.execute('SELECT 1 FROM runs WHERE id=?', (original_id,)).fetchone():
                raise AppError('NOT_FOUND', 'Run not found.', 404)
            if key:
                prior = db.execute('SELECT request_body,data FROM runs WHERE topic_id=? AND request_key=?',
                                   (topic_id, key)).fetchone()
                if prior:
                    if prior[0] != dump(request):
                        raise AppError('IDEMPOTENCY_CONFLICT', 'This key was used with a different request.')
                    return json.loads(prior[1]), False
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'This topic already has an active run.')
            rows = db.execute('''SELECT i.position,i.evidence,i.outcome,t.status AS topic_status
                FROM run_inputs i LEFT JOIN evidence e
                  ON e.source_id=json_extract(i.evidence,'$.source_id') AND e.external_id=json_extract(i.evidence,'$.external_id')
                LEFT JOIN topic_evidence t ON t.topic_id=? AND t.evidence_id=e.id
                WHERE i.run_id=? ORDER BY i.position''', (topic_id, original_id))
            work = [(row['position'], row['evidence']) for row in rows
                    if row['topic_status'] not in ('completed', 'review')
                    and (not row['outcome'] or json.loads(row['outcome'])['status'] in ('failed', 'failed_retryable'))]
            if not work:
                raise AppError('NO_RESUMABLE_EVIDENCE', 'There is no failed or unattempted evidence to resume.')
            approved = {row[0] for row in db.execute('SELECT source_id FROM topic_sources WHERE topic_id=?', (topic_id,))}
            if any(json.loads(raw)['source_id'] not in approved for _, raw in work):
                raise AppError('UNAPPROVED_SOURCE', 'Approve the original sources before resuming.')
            run = dict(original, run_id=uid(), trigger_type='resume', status='pending', model=model,
                       prompt_version=PROMPT_VERSION if original['provider'] == 'qwen' else original['prompt_version'],
                       started_at=None, completed_at=None, counts={}, error=None,
                       snapshot=dict(original['snapshot'], resume_of=original_id))
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run['run_id'], topic_id, 'pending', key, dump(request), dump(run)))
            db.executemany('INSERT INTO run_inputs VALUES(?,?,?,NULL)', [(run['run_id'], *row) for row in work])
        return run, True

    def save_run_inputs(self, run_id, evidence_items):
        with self.connection(True) as db:
            db.executemany('INSERT INTO run_inputs VALUES(?,?,?,NULL)',
                           [(run_id, position, dump(evidence)) for position, evidence in enumerate(evidence_items)])

    def run_inputs(self, run_id):
        with self.connection() as db:
            return [(row[0], json.loads(row[1]), json.loads(row[2]) if row[2] else None) for row in db.execute(
                'SELECT position,evidence,outcome FROM run_inputs WHERE run_id=? ORDER BY position', (run_id,))]

    def record_run_input(self, run_id, position, outcome):
        with self.connection(True) as db:
            db.execute('UPDATE run_inputs SET outcome=? WHERE run_id=? AND position=?', (dump(outcome), run_id, position))

    def active_runs(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM runs WHERE status IN ('pending','running')")]

    def update_run(self, identity, **changes):
        with self.connection(True) as db:
            data = json.loads(db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()[0])
            data.update(changes)
            db.execute('UPDATE runs SET status=?,data=? WHERE id=?', (data['status'], dump(data), identity))

    def runs(self, topic_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM runs WHERE topic_id=? ORDER BY rowid DESC LIMIT 30', (topic_id,))]

    def steps(self, run_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM steps WHERE run_id=? ORDER BY sequence', (run_id,))]

    def save_step(self, step):
        with self.connection(True) as db:
            db.execute('INSERT INTO steps VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (step['step_id'], step['run_id'], step['sequence'], dump(step)))

    def events(self, topic_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM events WHERE topic_id=? ORDER BY rowid', (topic_id,))]

    def delete_event(self, identity):
        with self.connection(True) as db:
            event = db.execute('SELECT topic_id FROM events WHERE id=?', (identity,)).fetchone()
            if not event:
                raise AppError('NOT_FOUND', 'Event not found.', 404)
            topic_id = event['topic_id']
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before deleting the event.')

            db.execute('DELETE FROM notifications WHERE event_id=?', (identity,))
            db.execute('DELETE FROM transitions WHERE event_id=?', (identity,))
            db.execute('DELETE FROM event_evidence WHERE event_id=?', (identity,))
            db.execute('DELETE FROM events WHERE id=?', (identity,))

            # Keep the original assessment for evidence history. Close a review only when
            # deleting this event leaves it without any live or unassigned decisions.
            remaining = {row[0] for row in db.execute('SELECT id FROM events WHERE topic_id=?', (topic_id,))}
            pending = db.execute("SELECT evidence_id,assessment FROM topic_evidence WHERE topic_id=? AND status='review'", (topic_id,))
            for row in pending.fetchall():
                ids = {decision['association']['event_id'] for decision in pending_review_decisions(json.loads(row['assessment']))}
                if identity in ids and None not in ids and not ids.intersection(remaining):
                    db.execute("UPDATE topic_evidence SET status='completed' WHERE topic_id=? AND evidence_id=?",
                               (topic_id, row['evidence_id']))

    def topic_evidence_items(self, topic_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('''SELECT e.data FROM evidence e
                JOIN topic_evidence t ON t.evidence_id=e.id WHERE t.topic_id=? ORDER BY e.rowid''', (topic_id,))]

    def evidence_decisions(self, topic_id):
        with self.connection() as db:
            return [dict(evidence=json.loads(r[0]),status=r[1],assessment=json.loads(r[2]) if r[2] else None,
                         error=json.loads(r[3]) if r[3] else None, prefilter_excluded=bool(r[4])) for r in db.execute('''
                WITH excluded_inputs AS (
                    SELECT DISTINCT json_extract(i.outcome, '$.evidence_id') AS evidence_id
                    FROM run_inputs i JOIN runs r ON r.id=i.run_id
                    WHERE r.topic_id=? AND json_extract(i.outcome, '$.status')='irrelevant'
                )
                SELECT e.data,t.status,t.assessment,t.error,
                       t.assessment IS NULL AND e.id IN (SELECT evidence_id FROM excluded_inputs)
                FROM evidence e JOIN topic_evidence t ON t.evidence_id=e.id WHERE t.topic_id=? ORDER BY e.rowid''',
                (topic_id, topic_id))]

    def review_item(self, topic_id, evidence_id):
        with self.connection() as db:
            row=db.execute('SELECT status,assessment FROM topic_evidence WHERE topic_id=? AND evidence_id=?',(topic_id,evidence_id)).fetchone()
        if not row:
            raise AppError('NOT_FOUND','Review evidence not found in this topic.',404)
        return dict(status=row[0],assessment=json.loads(row[1]) if row[1] else None)

    def review_resolution(self,topic_id,evidence_id):
        with self.connection() as db:
            row=db.execute('SELECT data FROM review_resolutions WHERE topic_id=? AND evidence_id=?',(topic_id,evidence_id)).fetchone()
        return json.loads(row[0]) if row else None

    def commit_review(self, event, transition, notification, run, step, resolution, expected_version, fault=None):
        topic_id,evidence_id=event['topic_id'],resolution['evidence_id']
        with self.connection(True) as db:
            prior=db.execute('SELECT data FROM review_resolutions WHERE topic_id=? AND evidence_id=?',(topic_id,evidence_id)).fetchone()
            if prior:
                result=json.loads(prior[0])
                if result.get('request')!=resolution['request']:
                    raise AppError('REVIEW_ALREADY_RESOLVED','This review already has an audit record.')
                return result
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')",(topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE','Wait for the active run before resolving reviews.')
            row=db.execute('SELECT status FROM topic_evidence WHERE topic_id=? AND evidence_id=?',(topic_id,evidence_id)).fetchone()
            if not row or row[0]!='review':
                raise AppError('NOT_PENDING_REVIEW','Evidence is not awaiting review.')
            changed=db.execute('UPDATE events SET version=?,data=? WHERE id=? AND topic_id=? AND version=?',
                (event['version'],dump(event),event['event_id'],topic_id,expected_version))
            if changed.rowcount!=1:
                raise AppError('VERSION_CONFLICT','Event changed; reopen the review with current state.')
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(run['run_id'],topic_id,'completed',None,None,dump(run)))
            db.execute('INSERT INTO transitions VALUES(?,?,?,?,?)',(transition['transition_id'],event['event_id'],run['run_id'],transition['idempotency_key'],dump(transition)))
            if fault:
                fault()
            db.execute('INSERT INTO notifications VALUES(?,?,?,?,?)',(notification['notification_id'],topic_id,event['event_id'],transition['transition_id'],dump(notification)))
            db.execute('INSERT INTO steps VALUES(?,?,?,?)',(step['step_id'],run['run_id'],1,dump(step)))
            db.execute('INSERT INTO review_resolutions VALUES(?,?,?)',(topic_id,evidence_id,dump(resolution)))
            db.execute("INSERT INTO event_evidence VALUES(?,?,'supporting') ON CONFLICT(event_id,evidence_id) DO UPDATE SET relation='supporting'",(event['event_id'],evidence_id))
            db.execute("UPDATE topic_evidence SET status='completed',error=NULL WHERE topic_id=? AND evidence_id=?",(topic_id,evidence_id))
        return resolution

    def ingest(self, topic_id, evidence, canonical_override=None):
        with self.connection(True) as db:
            row = db.execute('SELECT data FROM evidence WHERE source_id=? AND external_id=?',
                             (evidence['source_id'], evidence['external_id'])).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved['content_hash'] != evidence['content_hash']:
                    raise AppError('SOURCE_REVISION', 'A source item changed; use a new external revision ID.', 422)
                saved['fetched_at'] = evidence['fetched_at']
                evidence = saved
                db.execute('UPDATE evidence SET data=? WHERE id=?', (dump(saved), saved['evidence_id']))
            else:
                canonical = db.execute('SELECT canonical_id FROM evidence WHERE hash=? LIMIT 1', (evidence['content_hash'],)).fetchone()
                if canonical:
                    evidence['canonical_evidence_id'] = canonical[0]
                elif canonical_override and db.execute('SELECT 1 FROM evidence WHERE id=?', (canonical_override,)).fetchone():
                    evidence['canonical_evidence_id'] = canonical_override
                else:
                    evidence['canonical_evidence_id'] = evidence['evidence_id']
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?)',
                           (evidence['evidence_id'], evidence['source_id'], evidence['external_id'],
                            evidence['content_hash'], evidence['canonical_evidence_id'], dump(evidence)))
            db.execute("INSERT OR IGNORE INTO topic_evidence VALUES(?,?,'pending',NULL,NULL)", (topic_id, evidence['evidence_id']))
        return evidence

    def processing(self, topic_id, evidence_id):
        with self.connection() as db:
            row = db.execute('SELECT status FROM topic_evidence WHERE topic_id=? AND evidence_id=?', (topic_id, evidence_id)).fetchone()
            return row[0] if row else None

    def finish_evidence(self, topic_id, evidence_id, status, error=None, links=()):
        with self.connection(True) as db:
            db.execute('UPDATE topic_evidence SET status=?,assessment=?,error=? WHERE topic_id=? AND evidence_id=?',
                       (status, None, dump(error) if error else None, topic_id, evidence_id))
            db.executemany('INSERT OR IGNORE INTO event_evidence VALUES(?,?,?)',
                           [(event_id, evidence_id, relation) for event_id, relation in links])

    def linked_events(self, topic_id, evidence_id):
        with self.connection() as db:
            return [row[0] for row in db.execute('''SELECT ee.event_id FROM event_evidence ee JOIN events e ON e.id=ee.event_id
                WHERE e.topic_id=? AND ee.evidence_id=?''', (topic_id, evidence_id))]

    def available_evidence(self, topic_id):
        with self.connection() as db:
            return {r[0]: json.loads(r[1]) for r in db.execute('''SELECT e.id,e.data FROM evidence e
                JOIN topic_evidence t ON t.evidence_id=e.id WHERE t.topic_id=?''', (topic_id,))}

    def event_evidence_as_of(self, topic_id, event_id, published_at):
        with self.connection() as db:
            rows = db.execute('''SELECT e.data, ee.relation FROM evidence e
                JOIN event_evidence ee ON ee.evidence_id=e.id
                JOIN events ev ON ev.id=ee.event_id
                JOIN topic_evidence te ON te.evidence_id=e.id AND te.topic_id=ev.topic_id
                WHERE ev.topic_id=? AND ev.id=? AND julianday(json_extract(e.data, '$.published_at')) <= julianday(?)
                ORDER BY julianday(json_extract(e.data, '$.published_at')) DESC, e.rowid DESC LIMIT 5''',
                (topic_id, event_id, published_at))
            return [dict(json.loads(row[0]), relation=row[1]) for row in rows]

    def get_event_history(self, topic_id, event_id, published_at, keyword=None):
        changes = deque(maxlen=3)
        records = 0
        keyword = keyword.casefold().strip() if keyword else ''
        with self.connection() as db:
            rows = db.execute('''
                SELECT json_extract(t.data, '$.before_state.current_status') AS before_status,
                       json_extract(t.data, '$.after_state.current_status') AS after_status,
                       json_extract(t.data, '$.evidence_ids') AS evidence_ids,
                       strftime('%Y-%m-%dT%H:%M:%fZ', MAX(julianday(json_extract(e.data, '$.published_at')))) AS published_at,
                       COUNT(*) = COUNT(julianday(json_extract(e.data, '$.published_at')))
                           AND MAX(julianday(json_extract(e.data, '$.published_at'))) <= julianday(?) AS visible
                FROM transitions t
                JOIN events ev ON ev.id=t.event_id
                LEFT JOIN json_each(t.data, '$.evidence_ids') ref ON 1
                LEFT JOIN evidence e ON e.id=ref.value
                WHERE ev.topic_id=? AND ev.id=?
                GROUP BY t.rowid ORDER BY t.rowid
            ''', (published_at, topic_id, event_id))
            for row in rows:
                # Later changes may carry a future transition's state in their before_status.
                if not row['visible']:
                    break
                records += 1
                before, after = row['before_status'], row['after_status']
                if before == after or (keyword and keyword not in (before or '').casefold() and keyword not in after.casefold()):
                    continue
                changes.append(dict(before_status=before[:240] if before is not None else None,
                                    after_status=after[:240], published_at=row['published_at'],
                                    evidence_ids=json.loads(row['evidence_ids'])))
        return {'event_id': event_id, 'record_count': records, 'changes': list(changes)}

    def commit_assessment(self, topic_id, evidence_id, assessment, changes, run_id, position, attempts, fault=None):
        results = []
        with self.connection(True) as db:
            for change in changes:
                event_id = change['event_id']
                result = dict(outcome=change['outcome'], event_id=event_id)
                if 'event' in change:
                    event, transition, notification = change['event'], change['transition'], change['notification']
                    prior = db.execute('SELECT 1 FROM transitions WHERE event_id=? AND idempotency_key IN (?,?)',
                                       (event_id, transition['idempotency_key'], change['legacy_key'])).fetchone()
                    if prior:
                        result['outcome'] = 'duplicate'
                    else:
                        if transition['before_state'] is None:
                            db.execute('INSERT INTO events VALUES(?,?,?,?)', (event_id, topic_id, event['version'], dump(event)))
                        else:
                            updated = db.execute('UPDATE events SET version=?,data=? WHERE id=? AND topic_id=? AND version=?',
                                                 (event['version'], dump(event), event_id, topic_id, change['expected_version']))
                            if updated.rowcount != 1:
                                raise AppError('VERSION_CONFLICT', 'Event changed; retry with current state.')
                        db.execute('INSERT INTO transitions VALUES(?,?,?,?,?)',
                                   (transition['transition_id'], event_id, transition['run_id'], transition['idempotency_key'], dump(transition)))
                        if fault:
                            fault()
                        db.execute('INSERT INTO notifications VALUES(?,?,?,?,?)',
                                   (notification['notification_id'], topic_id, event_id, transition['transition_id'], dump(notification)))
                        result['transition_id'] = transition['transition_id']
                if event_id and change['relation']:
                    db.execute('INSERT OR IGNORE INTO event_evidence VALUES(?,?,?)', (event_id, evidence_id, change['relation']))
                results.append(result)
            outcomes = {result['outcome'] for result in results}
            outcome = next((name for name in ('review', 'committed', 'no_change', 'duplicate') if name in outcomes), 'irrelevant')
            status = 'review' if outcome == 'review' else 'completed'
            db.execute('UPDATE topic_evidence SET status=?,assessment=?,error=NULL WHERE topic_id=? AND evidence_id=?',
                       (status, dump(assessment), topic_id, evidence_id))
            report = dict(status=outcome, evidence_id=evidence_id, attempts=attempts, error=None, event_outcomes=results)
            db.execute('UPDATE run_inputs SET outcome=? WHERE run_id=? AND position=?', (dump(report), run_id, position))
        return report

    def notifications(self, topic_id, action=None):
        with self.connection() as db:
            items = [json.loads(r[0]) for r in db.execute('SELECT data FROM notifications WHERE topic_id=? ORDER BY rowid DESC', (topic_id,))]
        return [n for n in items if not action or n['action'] == action]

    def inbox(self, topic_id=None, limit=60):
        with self.connection() as db:
            eligible = "json_extract(n.data, '$.action') IN ('immediate', 'digest')"
            params = ()
            if topic_id is not None:
                eligible += ' AND n.topic_id=?'
                params = (topic_id,)
            rows = list(db.execute(f'''SELECT n.data, r.read_at, json_extract(t.data, '$.title') AS topic_title,
                    json_extract(e.data, '$.title') AS event_title,
                    json_extract(x.data, '$.after_state.current_status') AS summary
                FROM notifications n JOIN topics t ON t.id=n.topic_id JOIN events e ON e.id=n.event_id
                JOIN transitions x ON x.id=n.transition_id
                LEFT JOIN notification_reads r ON r.notification_id=n.id
                WHERE {eligible} ORDER BY n.rowid DESC LIMIT ?''', (*params, limit)))
            unread = db.execute(f'''SELECT count(*) FROM notifications n
                WHERE {eligible} AND NOT EXISTS(SELECT 1 FROM notification_reads r WHERE r.notification_id=n.id)''',
                params).fetchone()[0]
            newest = db.execute(f'''SELECT n.id FROM notifications n WHERE {eligible}
                AND json_extract(n.data, '$.action')='immediate' ORDER BY n.rowid DESC LIMIT 1''', params).fetchone()
            items = [dict(json.loads(row['data']), read_at=row['read_at'], topic_title=row['topic_title'],
                          event_title=row['event_title'], summary=row['summary']) for row in rows]
            return dict(items=items, unread=unread, latest_immediate_id=newest[0] if newest else None)

    def read_inbox(self, notification_id=None):
        with self.connection(True) as db:
            eligible = "json_extract(data, '$.action') IN ('immediate', 'digest')"
            if notification_id is None:
                db.execute(f'''INSERT OR IGNORE INTO notification_reads
                    SELECT id, ? FROM notifications WHERE {eligible}''', (now(),))
            else:
                if not db.execute(f'SELECT 1 FROM notifications WHERE id=? AND {eligible}', (notification_id,)).fetchone():
                    raise AppError('NOT_FOUND', 'Notification not found.', 404)
                db.execute('INSERT OR IGNORE INTO notification_reads VALUES(?,?)', (notification_id, now()))

    def radar(self):
        with self.connection() as db:
            rows = db.execute("""
                SELECT data FROM events
                ORDER BY json_extract(data, '$.updated_at') DESC, rowid DESC LIMIT 9
            """)
            return [
                {key: event[key] for key in ('event_id', 'topic_id', 'title', 'updated_at')}
                for event in (json.loads(row[0]) for row in rows)
            ]

    def event_detail(self, identity):
        event = self.get('events', identity)
        with self.connection() as db:
            history = [json.loads(r[0]) for r in db.execute('SELECT data FROM transitions WHERE event_id=? ORDER BY rowid', (identity,))]
            evidence = [dict(json.loads(r[0]), relation=r[1]) for r in db.execute('''SELECT e.data,ee.relation FROM evidence e
                JOIN event_evidence ee ON e.id=ee.evidence_id WHERE ee.event_id=? ORDER BY e.rowid''', (identity,))]
            updates = [dict(json.loads(row['notice']), provider=json.loads(row['run'])['provider'])
                       for row in db.execute('''SELECT n.data AS notice, r.data AS run
                           FROM notifications n JOIN transitions t ON t.id=n.transition_id
                           JOIN runs r ON r.id=t.run_id WHERE n.event_id=?''', (identity,))]
        return dict(event=event, history=history, evidence=evidence, updates=updates, reviews=self.reviews(event['topic_id'], identity))

    def patch_event(self, identity, changes):
        with self.connection(True) as db:
            row = db.execute('SELECT data FROM events WHERE id=?', (identity,)).fetchone()
            if not row:
                raise AppError('NOT_FOUND', 'Event not found.', 404)
            existing = json.loads(row[0])
            topic_id = existing['topic_id']
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before editing events.')
            topic_row = db.execute('SELECT data FROM topics WHERE id=?', (topic_id,)).fetchone()
            topic = json.loads(topic_row[0]) if topic_row else {'topic_id': topic_id}
            before_title = existing['title']
            new_title = changes['title'].strip() if isinstance(changes.get('title'), str) else before_title
            if not new_title:
                raise AppError('INVALID_TITLE', 'Event title cannot be empty.', 422)
            title_changed = new_title != before_title
            if changes.get('title_locked') is not None:
                title_locked = bool(changes['title_locked'])
            elif title_changed:
                title_locked = True
            else:
                title_locked = bool(existing.get('title_locked'))
            stamp = now()
            updated = dict(
                existing,
                title=new_title,
                title_locked=title_locked,
                version=existing['version'] + (1 if title_changed else 0),
                updated_at=stamp if title_changed else existing['updated_at'],
            )
            db.execute('UPDATE events SET version=?,data=? WHERE id=?', (updated['version'], dump(updated), identity))
            if title_changed:
                run_id, transition_id = uid(), uid()
                run = dict(
                    run_id=run_id, topic_id=topic_id, trigger_type='human_edit', provider='human', status='completed',
                    fixture_batch_id='edit', model=None, prompt_version='human-edit-v1', started_at=stamp, completed_at=stamp,
                    counts={'committed': 1}, error=None, snapshot={'topic': topic, 'sources': []},
                )
                transition = dict(
                    transition_id=transition_id, event_id=identity, run_id=run_id,
                    before_state=existing['state'], after_state=existing['state'],
                    before_title=before_title, after_title=new_title,
                    evidence_ids=[], idempotency_key=f'human-title:{identity}:{transition_id}',
                    impact_reason=f'人工调整事件标题：{before_title} → {new_title}'[:280],
                    committed_at=stamp,
                )
                notification = dict(
                    notification_id=uid(), topic_id=topic_id, event_id=identity, transition_id=transition_id,
                    priority='low', action='record_only', reason_codes=['title_updated', 'human_edit'], created_at=stamp,
                )
                step = dict(
                    step_id=uid(), run_id=run_id, sequence=1, step_name='human_edit_event', evidence_id=None,
                    status='completed', attempt=1, input_refs=[identity], output_refs=[transition_id],
                    decision={'before_title': before_title, 'after_title': new_title, 'title_locked': title_locked},
                    error=None, started_at=stamp, completed_at=stamp,
                )
                db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run_id, topic_id, 'completed', None, None, dump(run)))
                db.execute('INSERT INTO transitions VALUES(?,?,?,?,?)',
                           (transition_id, identity, run_id, transition['idempotency_key'], dump(transition)))
                db.execute('INSERT INTO notifications VALUES(?,?,?,?,?)',
                           (notification['notification_id'], topic_id, identity, transition_id, dump(notification)))
                db.execute('INSERT INTO steps VALUES(?,?,?,?)', (step['step_id'], run_id, 1, dump(step)))
        return self.event_detail(identity)

    def dismiss_event(self, identity, *, disposition='irrelevant', reason=None, add_exclusion=True):
        """Remove an off-topic/unwanted event, suppress its orphaned evidence from re-ingesting, and optionally save an exclusion."""
        with self.connection(True) as db:
            row = db.execute('SELECT data FROM events WHERE id=?', (identity,)).fetchone()
            if not row:
                raise AppError('NOT_FOUND', 'Event not found.', 404)
            event = json.loads(row[0])
            topic_id = event['topic_id']
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before removing events.')
            linked_ids = {
                r[0] for r in db.execute('SELECT evidence_id FROM event_evidence WHERE event_id=?', (identity,))
            } | set((event.get('state') or {}).get('sources') or [])
            db.execute('DELETE FROM notifications WHERE event_id=?', (identity,))
            db.execute('DELETE FROM transitions WHERE event_id=?', (identity,))
            db.execute('DELETE FROM event_evidence WHERE event_id=?', (identity,))
            db.execute('DELETE FROM events WHERE id=?', (identity,))

            clean_reason = (reason or '').strip()[:160]
            suppressed = 0
            for eid in linked_ids:
                still_linked = db.execute(
                    'SELECT 1 FROM event_evidence ee JOIN events e ON e.id=ee.event_id WHERE e.topic_id=? AND ee.evidence_id=? LIMIT 1',
                    (topic_id, eid),
                ).fetchone()
                if still_linked:
                    continue
                dismissed_assessment = {
                    'evidence_id': eid,
                    'is_relevant': False,
                    'event_decisions': [],
                    'dismissed_by_user': True,
                    'dismissed_disposition': disposition,
                    'dismissed_reason': clean_reason or event['title'],
                }
                changed = db.execute(
                    "UPDATE topic_evidence SET status='completed', assessment=?, error=NULL WHERE topic_id=? AND evidence_id=?",
                    (dump(dismissed_assessment), topic_id, eid),
                )
                if changed.rowcount:
                    suppressed += 1

            # Also close any pending review rows whose decisions only referenced this removed event.
            remaining = {r[0] for r in db.execute('SELECT id FROM events WHERE topic_id=?', (topic_id,))}
            pending = db.execute("SELECT evidence_id,assessment FROM topic_evidence WHERE topic_id=? AND status='review'", (topic_id,))
            for prow in pending.fetchall():
                ids = {decision['association']['event_id'] for decision in pending_review_decisions(json.loads(prow['assessment']))}
                if identity in ids and None not in ids and not ids.intersection(remaining):
                    db.execute("UPDATE topic_evidence SET status='completed' WHERE topic_id=? AND evidence_id=?",
                               (topic_id, prow['evidence_id']))

            topic_row = db.execute('SELECT data FROM topics WHERE id=?', (topic_id,)).fetchone()
            topic = json.loads(topic_row[0])
            added_rule = None
            if add_exclusion:
                rule_text = clean_reason or (
                    f"排除与「{event['title']}」无关内容" if disposition == 'irrelevant' else f"不再追踪「{event['title']}」"
                )
                rule_text = rule_text.strip()[:160]
                if rule_text:
                    exclusions = list(topic.get('exclusions') or [])
                    merged = unique_topic_tags([*exclusions, rule_text])[:20]
                    if merged != exclusions:
                        topic['exclusions'] = merged
                        topic['updated_at'] = now()
                        db.execute('UPDATE topics SET data=? WHERE id=?', (dump(topic), topic_id))
                        added_rule = rule_text
        return {
            'event_id': identity,
            'topic_id': topic_id,
            'disposition': disposition,
            'dismissed_evidence_count': suppressed,
            'added_exclusion': added_rule,
            'topic': topic,
        }

    def merge_event(self, source_id, target_id, *, new_title=None, reason=None):
        """Merge source event's grounded facts, timeline, and evidence into target event with full audit trail."""
        if source_id == target_id:
            raise AppError('INVALID_MERGE', 'Choose a different target event to merge into.', 422)
        with self.connection(True) as db:
            src_row = db.execute('SELECT data FROM events WHERE id=?', (source_id,)).fetchone()
            tgt_row = db.execute('SELECT data FROM events WHERE id=?', (target_id,)).fetchone()
            if not src_row or not tgt_row:
                raise AppError('NOT_FOUND', 'Event not found.', 404)
            source = json.loads(src_row[0])
            target = json.loads(tgt_row[0])
            if source['topic_id'] != target['topic_id']:
                raise AppError('CROSS_TOPIC_EVENT', 'Cannot merge events from different topics.', 422)
            topic_id = target['topic_id']
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before merging events.')
            topic_row = db.execute('SELECT data FROM topics WHERE id=?', (topic_id,)).fetchone()
            topic = json.loads(topic_row[0]) if topic_row else {'topic_id': topic_id}

            t_state = target['state']
            s_state = source['state']
            merged_facts = list(t_state['key_facts'])
            seen_facts = {(f['fact'], tuple(sorted(f['evidence_ids']))) for f in merged_facts}
            for fact in s_state['key_facts']:
                key = (fact['fact'], tuple(sorted(fact['evidence_ids'])))
                if key not in seen_facts:
                    seen_facts.add(key)
                    merged_facts.append(fact)

            merged_timeline = list(t_state['timeline'])
            seen_tl = {(item['occurred_at'], item['description'], tuple(sorted(item['evidence_ids']))) for item in merged_timeline}
            for item in s_state['timeline']:
                key = (item['occurred_at'], item['description'], tuple(sorted(item['evidence_ids'])))
                if key not in seen_tl:
                    seen_tl.add(key)
                    merged_timeline.append(item)
            merged_timeline.sort(key=lambda item: datetime.fromisoformat(item['occurred_at'].replace('Z', '+00:00')))

            merged_sources = list(dict.fromkeys([*t_state['sources'], *s_state['sources']]))
            merged_state = {
                'current_status': t_state['current_status'],
                'timeline': merged_timeline,
                'key_facts': merged_facts,
                'sources': merged_sources,
            }

            before_title = target['title']
            clean_title = new_title.strip() if isinstance(new_title, str) and new_title.strip() else before_title
            title_changed = clean_title != before_title
            stamp, run_id, transition_id = now(), uid(), uid()
            updated_target = dict(
                target,
                title=clean_title,
                title_locked=True if title_changed else bool(target.get('title_locked')),
                state=merged_state,
                version=target['version'] + 1,
                updated_at=stamp,
            )

            # Re-link source evidence to target before deleting source event
            for row in db.execute('SELECT evidence_id, relation FROM event_evidence WHERE event_id=?', (source_id,)).fetchall():
                db.execute('INSERT OR IGNORE INTO event_evidence VALUES(?,?,?)', (target_id, row[0], row[1]))
            db.execute('DELETE FROM notifications WHERE event_id=?', (source_id,))
            db.execute('DELETE FROM transitions WHERE event_id=?', (source_id,))
            db.execute('DELETE FROM event_evidence WHERE event_id=?', (source_id,))
            db.execute('DELETE FROM events WHERE id=?', (source_id,))

            db.execute('UPDATE events SET version=?,data=? WHERE id=?',
                       (updated_target['version'], dump(updated_target), target_id))
            merge_note = (reason or '').strip() or f"人工合并事件「{source['title']}」"
            run = dict(
                run_id=run_id, topic_id=topic_id, trigger_type='human_merge', provider='human', status='completed',
                fixture_batch_id='merge', model=None, prompt_version='human-merge-v1', started_at=stamp, completed_at=stamp,
                counts={'committed': 1}, error=None, snapshot={'topic': topic, 'sources': []},
            )
            transition = dict(
                transition_id=transition_id, event_id=target_id, run_id=run_id,
                before_state=t_state, after_state=merged_state,
                evidence_ids=list(dict.fromkeys(s_state['sources'])),
                idempotency_key=f'human-merge:{source_id}:{target_id}:{transition_id}',
                impact_reason=merge_note[:280],
                merged_from_event_id=source_id,
                merged_from_title=source['title'],
                committed_at=stamp,
                **({'before_title': before_title, 'after_title': clean_title} if title_changed else {}),
            )
            notification = dict(
                notification_id=uid(), topic_id=topic_id, event_id=target_id, transition_id=transition_id,
                priority='low', action='record_only', reason_codes=['event_merged', 'human_merge'], created_at=stamp,
            )
            step = dict(
                step_id=uid(), run_id=run_id, sequence=1, step_name='human_merge_event', evidence_id=None,
                status='completed', attempt=1, input_refs=[source_id, target_id], output_refs=[transition_id],
                decision={'merged_from': source_id, 'merged_title': source['title'], 'reason': merge_note},
                error=None, started_at=stamp, completed_at=stamp,
            )
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run_id, topic_id, 'completed', None, None, dump(run)))
            db.execute('INSERT INTO transitions VALUES(?,?,?,?,?)',
                       (transition_id, target_id, run_id, transition['idempotency_key'], dump(transition)))
            db.execute('INSERT INTO notifications VALUES(?,?,?,?,?)',
                       (notification['notification_id'], topic_id, target_id, transition_id, dump(notification)))
            db.execute('INSERT INTO steps VALUES(?,?,?,?)', (step['step_id'], run_id, 1, dump(step)))
        return self.event_detail(target_id)

    def reviews(self, topic_id, event_id=None):
        with self.connection() as db:
            items = [dict(evidence=json.loads(r[0]), assessment=json.loads(r[1])) for r in db.execute('''SELECT e.data,t.assessment
                FROM topic_evidence t JOIN evidence e ON e.id=t.evidence_id WHERE t.topic_id=? AND t.status='review' ORDER BY e.rowid''', (topic_id,))]
            live_events = {row[0] for row in db.execute('SELECT id FROM events WHERE topic_id=?', (topic_id,))}
        reviews = []
        for item in items:
            assessment = item['assessment']
            pending = [decision for decision in pending_review_decisions(assessment)
                       if decision['association']['event_id'] is None or decision['association']['event_id'] in live_events]
            if event_id:
                pending = [decision for decision in pending if decision['association']['event_id'] == event_id]
            if not pending:
                continue
            if 'event_decisions' not in assessment:
                reviews.append(item)
                continue
            reviews.append(dict(evidence=item['evidence'], event_decisions=pending,
                                assessment=dict(pending[0], evidence_id=assessment['evidence_id'], is_relevant=True)))
        return reviews

    def resolve_review(self, topic_id, evidence_id, decision, note):
        with self.connection(True) as db:
            row = db.execute('SELECT status,assessment FROM topic_evidence WHERE topic_id=? AND evidence_id=?',
                             (topic_id, evidence_id)).fetchone()
            if not row:
                raise AppError('NOT_FOUND', 'Review evidence not found in this topic.', 404)
            prior = db.execute('SELECT data FROM review_resolutions WHERE topic_id=? AND evidence_id=?',
                               (topic_id, evidence_id)).fetchone()
            if prior:
                result = json.loads(prior[0])
                if (result['decision'], result['note']) != (decision, note):
                    raise AppError('REVIEW_ALREADY_RESOLVED', 'This review already has an audit record.')
                return result
            if db.execute("SELECT 1 FROM runs WHERE topic_id=? AND status IN ('pending','running')", (topic_id,)).fetchone():
                raise AppError('TOPIC_RUN_ACTIVE', 'Wait for the active run before resolving reviews.')
            if row['status'] != 'review':
                raise AppError('NOT_PENDING_REVIEW', 'Evidence is not awaiting review.')
            result = dict(resolution_id=uid(), topic_id=topic_id, evidence_id=evidence_id,
                          decision=decision, note=note, actor='local_user', resolved_at=now(),
                          original_assessment=json.loads(row['assessment']))
            db.execute('INSERT INTO review_resolutions VALUES(?,?,?)', (topic_id, evidence_id, dump(result)))
            db.execute("UPDATE topic_evidence SET status='completed' WHERE topic_id=? AND evidence_id=?", (topic_id, evidence_id))
        return result

    def review_resolutions(self, topic_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM review_resolutions WHERE topic_id=? ORDER BY rowid DESC', (topic_id,))]
