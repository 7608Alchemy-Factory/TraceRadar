"""All SQL lives here. Connections are short-lived and never shared across threads."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .core import AppError
from .domain import uid, now
from .llm import PROMPT_VERSION


def dump(value):
    return json.dumps(value, ensure_ascii=False)


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
            ''')
            for source in sources:
                db.execute('INSERT OR IGNORE INTO sources VALUES(?,?)', (source['source_id'], dump(source)))
        for run in self.active_runs():
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
            topic = json.loads(db.execute('SELECT data FROM topics WHERE id=?', (topic_id,)).fetchone()[0])
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
            run = dict(run_id=uid(), topic_id=topic_id, trigger_type='manual', status='pending',
                       **request, model=model, prompt_version=PROMPT_VERSION if request['provider'] == 'qwen' else 'mock-demo-v1', started_at=None,
                       completed_at=None, counts={}, error=None, snapshot=snapshot)
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run['run_id'], topic_id, 'pending', key, dump(effective_request), dump(run)))
        return run, True

    def resume_run(self, original, model, key=None):
        topic_id, original_id = original['topic_id'], original['run_id']
        request = {'resume_of': original_id}
        with self.connection(True) as db:
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

    def topic_evidence_items(self, topic_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('''SELECT e.data FROM evidence e
                JOIN topic_evidence t ON t.evidence_id=e.id WHERE t.topic_id=? ORDER BY e.rowid''', (topic_id,))]

    def evidence_decisions(self, topic_id):
        with self.connection() as db:
            return [dict(evidence=json.loads(r[0]),status=r[1],assessment=json.loads(r[2]) if r[2] else None,
                         error=json.loads(r[3]) if r[3] else None) for r in db.execute('''SELECT e.data,t.status,t.assessment,t.error
                FROM evidence e JOIN topic_evidence t ON t.evidence_id=e.id WHERE t.topic_id=? ORDER BY e.rowid''',(topic_id,))]

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

    def event_detail(self, identity):
        event = self.get('events', identity)
        with self.connection() as db:
            history = [json.loads(r[0]) for r in db.execute('SELECT data FROM transitions WHERE event_id=? ORDER BY rowid', (identity,))]
            evidence = [dict(json.loads(r[0]), relation=r[1]) for r in db.execute('''SELECT e.data,ee.relation FROM evidence e
                JOIN event_evidence ee ON e.id=ee.evidence_id WHERE ee.event_id=? ORDER BY e.rowid''', (identity,))]
        return dict(event=event, history=history, evidence=evidence, reviews=self.reviews(event['topic_id'], identity))

    def reviews(self, topic_id, event_id=None):
        with self.connection() as db:
            items = [dict(evidence=json.loads(r[0]), assessment=json.loads(r[1])) for r in db.execute('''SELECT e.data,t.assessment
                FROM topic_evidence t JOIN evidence e ON e.id=t.evidence_id WHERE t.topic_id=? AND t.status='review' ORDER BY e.rowid''', (topic_id,))]
        reviews = []
        for item in items:
            assessment = item['assessment']
            if 'event_decisions' not in assessment:
                if not event_id or assessment['association']['event_id'] == event_id:
                    reviews.append(item)
                continue
            pending = [decision for decision in assessment['event_decisions'] if decision['requires_review']
                       or decision['conflicts'] or decision['novelty'] == 'conflict' or decision['association']['type'] == 'unresolved']
            if event_id:
                pending = [decision for decision in pending if decision['association']['event_id'] == event_id]
            if pending:
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
