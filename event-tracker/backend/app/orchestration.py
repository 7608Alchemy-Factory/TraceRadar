import logging
import queue
import threading
from .core import AppError
from .domain import Assessment, uid, now
from .connectors import FixtureConnector
from .llm import get_provider
from .services import StateManager

logger = logging.getLogger(__name__)


def public_error(exc):
    if isinstance(exc, AppError):
        return {'code': exc.code, 'message': exc.message}
    return {'code': 'PROCESSING_ERROR', 'message': 'Processing failed; retry this run or check backend logs.'}


class RunManager:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings
        self.connector = FixtureConnector(settings.fixture_path)
        self.state_manager = StateManager(repo)
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self.worker, name='topic-worker', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.queue.put(None)
        self.thread.join(timeout=65)

    def submit(self, run_id):
        self.queue.put(run_id)

    def worker(self):
        while True:
            identity = self.queue.get()
            try:
                if identity is None:
                    return
                self.execute(identity)
            except Exception:
                logger.exception('Worker failed for run %s', identity)
                self.repo.update_run(identity, status='failed', completed_at=now(),
                                     error={'code': 'WORKFLOW_ERROR', 'message': 'Workflow failed. Retry safely.'})
            finally:
                self.queue.task_done()

    def execute(self, identity):
        run = self.repo.get('runs', identity)
        self.repo.update_run(identity, status='running', started_at=now())
        topic = run['snapshot']['topic']
        sequence = 0
        counts = {'fetched': 0, 'committed': 0, 'skipped': 0, 'review': 0, 'irrelevant': 0, 'no_change': 0, 'errors': 0}
        errors, usable = [], 0

        def step(name, evidence_id=None):
            nonlocal sequence
            sequence += 1
            value = dict(step_id=uid(), run_id=identity, sequence=sequence, step_name=name,
                         evidence_id=evidence_id, status='running', attempt=1,
                         input_refs=[evidence_id] if evidence_id else [], output_refs=[],
                         decision={}, error=None, started_at=now(), completed_at=None)
            self.repo.save_step(value)
            return value

        def end(value, status='completed', decision=None, error=None):
            value.update(status=status, decision=decision or {}, error=error, completed_at=now())
            self.repo.save_step(value)

        provider = get_provider(run['provider'], self.settings)
        inputs = []
        for source in run['snapshot']['sources']:
            trace = step('fetch_source')
            trace['input_refs'] = [source['source_id']]
            try:
                batch = self.connector.fetch(source, run['fixture_batch_id'])
                inputs.extend(batch)
                counts['fetched'] += len(batch)
                end(trace, decision={'source_id': source['source_id'], 'items': len(batch)})
                usable += 1
            except Exception as exc:
                error = dict(public_error(exc), code='source_error')
                errors.append(error)
                counts['errors'] += 1
                end(trace, 'failed', error=error)
        for raw in sorted(inputs, key=lambda e: (e['published_at'], e['external_id'], e['source_id'])):
            evidence = None
            trace = step('normalize_and_deduplicate', raw['evidence_id'])
            try:
                evidence = self.repo.ingest(topic['topic_id'], raw)
                trace['evidence_id'] = evidence['evidence_id']
                trace['output_refs'] = [evidence['evidence_id']]
                canonical = evidence['canonical_evidence_id']
                status = self.repo.processing(topic['topic_id'], evidence['evidence_id'])
                canonical_done = self.repo.processing(topic['topic_id'], canonical) in ('completed', 'review')
                if status in ('completed', 'review') or (canonical != evidence['evidence_id'] and canonical_done):
                    if status not in ('completed', 'review'):
                        event_id = next((e['event_id'] for e in self.repo.events(topic['topic_id']) if canonical in e['state']['sources']), None)
                        self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed',
                                                  event_id=event_id, relation='duplicate')
                    counts['skipped'] += 1
                    end(trace, 'skipped', {'reason': 'already_processed_or_exact_duplicate', 'canonical_evidence_id': canonical})
                    continue
                end(trace, decision={'canonical_evidence_id': canonical})
                trace = step('assess_evidence', evidence['evidence_id'])
                assessment = Assessment.model_validate(provider.assess(topic, self.repo.events(topic['topic_id']), evidence, run['snapshot']['sources']))
                trace['attempt'] = getattr(provider, 'attempt', 1)
                end(trace, decision={'assessment': assessment.model_dump(mode='json'), 'usage': getattr(provider, 'usage', None)})
                trace = step('validate_and_commit', evidence['evidence_id'])
                outcome = self.state_manager.apply(topic, identity, evidence, assessment)
                counts[outcome if outcome in counts else 'skipped'] += 1
                usable += 1
                end(trace, decision={'outcome': outcome})
            except Exception as exc:
                logger.warning('Evidence processing failed: %s', type(exc).__name__)
                error = public_error(exc)
                errors.append(error)
                counts['errors'] += 1
                trace['attempt'] = getattr(provider, 'attempt', 1)
                if evidence and self.repo.processing(topic['topic_id'], evidence['evidence_id']) not in ('completed', 'review'):
                    self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'failed', error=error)
                end(trace, 'failed', error=error)
        # Successful fetches alone do not make a completely failed processing run partial success.
        outcomes = sum(counts[k] for k in ('committed', 'skipped', 'review', 'irrelevant', 'no_change'))
        status = ('completed_with_errors' if outcomes or (not inputs and usable) else 'failed') if errors else 'completed'
        self.repo.update_run(identity, status=status, completed_at=now(), counts=counts,
                             error={'code': 'RUN_ERRORS', 'message': 'One or more steps failed.', 'details': errors} if errors else None)
