import logging
import queue
import threading
from .core import AppError
from .domain import Assessment, EvidenceAssessment, uid, now
from .connectors import ConnectorRouter
from .evidence_processing import find_duplicate_match, score_relevance
from .llm import RETRYABLE_PROVIDER_ERRORS, get_provider
from .services import StateManager

logger = logging.getLogger(__name__)

PROVIDER_ERRORS_THAT_STOP_RUN = {
    'PROVIDER_AUTH', 'PROVIDER_MODEL', 'PROVIDER_BALANCE', 'PROVIDER_REQUEST',
}


def public_error(exc):
    if isinstance(exc, AppError):
        return {'code': exc.code, 'message': exc.message}
    return {'code': 'PROCESSING_ERROR', 'message': 'Processing failed; retry this run or check backend logs.'}


class RunManager:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings
        self.connector = ConnectorRouter(settings.fixture_path, settings.corpus_root, settings=settings)
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
        counts = {'fetched': 0, 'committed': 0, 'skipped': 0, 'review': 0, 'irrelevant': 0, 'no_change': 0,
                  'errors': 0, 'unprocessed': 0}
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
        options = run['snapshot'].get('options', {})
        inputs = []
        sources = [] if run['trigger_type'] == 'resume' else run['snapshot']['sources']
        source_types = {s['source_id']: s.get('type') for s in run['snapshot']['sources']}
        needs_web_ctx = any(s.get('type') in ('web_search', 'webpage', 'rss') for s in sources)
        existing_for_fetch = self.repo.topic_evidence_items(topic['topic_id']) if needs_web_ctx else None
        committed_ext_ids = (
            {
                (row['evidence']['source_id'], row['evidence']['external_id'])
                for row in self.repo.evidence_decisions(topic['topic_id'])
                if row.get('status') in ('completed', 'review')
            }
            if any(s.get('type') == 'webpage' for s in sources)
            else None
        )
        in_run_urls: set[str] = set()
        for source in sources:
            trace = step('fetch_source')
            trace['input_refs'] = [source['source_id']]
            try:
                fetch_kwargs = {}
                if options.get('as_of') is not None:
                    fetch_kwargs['as_of'] = options['as_of']
                if options.get('manual_items'):
                    fetch_kwargs['manual_items'] = options['manual_items']
                if source.get('type') in ('web_search', 'webpage', 'rss'):
                    fetch_kwargs['existing_items'] = existing_for_fetch
                    fetch_kwargs['in_run_urls'] = in_run_urls
                    fetch_kwargs['topic_id'] = topic['topic_id']
                if source.get('type') == 'webpage':
                    fetch_kwargs['committed_ext_ids'] = committed_ext_ids
                batch = self.connector.fetch(source, run['fixture_batch_id'], **fetch_kwargs) if fetch_kwargs else self.connector.fetch(source, run['fixture_batch_id'])
                inputs.extend(batch)
                decision = {'source_id': source['source_id'], 'items': len(batch)}
                fetch_stats = getattr(self.connector, 'last_fetch_stats', None)
                if fetch_stats:
                    decision['stats'] = dict(fetch_stats)
                end(trace, decision=decision)
                usable += 1
            except Exception as exc:
                error = dict(public_error(exc), code='source_error')
                errors.append(error)
                counts['errors'] += 1
                end(trace, 'failed', error=error)
        if run['trigger_type'] != 'resume':
            self.repo.save_run_inputs(identity, sorted(inputs, key=lambda e: (e['published_at'], e['external_id'], e['source_id'])))
        work = self.repo.run_inputs(identity)
        counts['fetched'] = len(work)
        blocking_error = None
        for index, (position, raw, _) in enumerate(work):
            evidence = None
            attempts = 0
            trace = step('normalize_and_deduplicate', raw['evidence_id'])

            def record(status, error=None):
                self.repo.record_run_input(identity, position, dict(
                    status=status, attempts=attempts, error=error,
                    evidence_id=(evidence or raw)['evidence_id'], event_outcomes=[]))

            try:
                existing_items = self.repo.topic_evidence_items(topic['topic_id'])
                same_external = any(
                    item.get('source_id') == raw['source_id'] and item.get('external_id') == raw['external_id']
                    for item in existing_items
                )
                dup_match = None
                if not same_external:
                    dup_match = find_duplicate_match(
                        raw,
                        existing_items,
                        enable_near_duplicate=options.get('near_duplicate_enabled', True) and source_types.get(raw['source_id']) != 'webpage',
                    )
                evidence = self.repo.ingest(
                    topic['topic_id'],
                    raw,
                    canonical_override=dup_match['canonical_evidence_id'] if dup_match else None,
                )
                trace['evidence_id'] = evidence['evidence_id']
                trace['output_refs'] = [evidence['evidence_id']]
                canonical = evidence['canonical_evidence_id']
                status = self.repo.processing(topic['topic_id'], evidence['evidence_id'])
                canonical_done = self.repo.processing(topic['topic_id'], canonical) in ('completed', 'review')
                if status in ('completed', 'review') or (canonical != evidence['evidence_id'] and canonical_done):
                    if status not in ('completed', 'review'):
                        links = [(event_id, 'duplicate') for event_id in self.repo.linked_events(topic['topic_id'], canonical)]
                        self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed',
                                                  links=links)
                    counts['skipped'] += 1
                    dedup_decision = {'reason': dup_match['reason'] if dup_match else 'already_processed_or_exact_duplicate', 'canonical_evidence_id': canonical}
                    if dup_match and 'similarity' in dup_match:
                        dedup_decision['similarity'] = dup_match['similarity']
                    end(trace, 'skipped', dedup_decision)
                    record('skipped')
                    continue
                end(trace, decision={'canonical_evidence_id': canonical})
                if options.get('prefilter_enabled', False):
                    prefilter = score_relevance(topic, evidence)
                    if not prefilter['passed']:
                        trace = step('prefilter_relevance', evidence['evidence_id'])
                        self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed')
                        counts['irrelevant'] += 1
                        usable += 1
                        end(trace, 'skipped', {'reason': 'prefilter_low_relevance', **prefilter})
                        record('irrelevant')
                        continue
                trace = step('assess_evidence', evidence['evidence_id'])
                try:
                    assessment = provider.assess(topic, self.repo.events(topic['topic_id']), evidence, run['snapshot']['sources'])
                finally:
                    attempts = getattr(provider, 'attempt', 1)
                    trace['attempt'] = attempts
                if not isinstance(assessment, (EvidenceAssessment, Assessment)):
                    schema = EvidenceAssessment if isinstance(assessment, dict) and 'event_decisions' in assessment else Assessment
                    assessment = schema.model_validate(assessment)
                end(trace, decision={'assessment': assessment.model_dump(mode='json'), 'usage': getattr(provider, 'usage', None),'state_assembly':getattr(provider,'state_assembly',None)})
                trace = step('validate_and_commit', evidence['evidence_id'])
                report = self.state_manager.apply(topic, identity, evidence, assessment, position, attempts)
                outcome, results = report['status'], report['event_outcomes']
                if outcome != 'committed':
                    counts['skipped' if outcome == 'duplicate' else outcome] += 1
                counts['committed'] += sum(item['outcome'] == 'committed' for item in results)
                usable += 1
                trace['output_refs'] = [item['transition_id'] for item in results if 'transition_id' in item]
                end(trace, decision={'outcome': outcome, 'event_outcomes': results})
            except Exception as exc:
                logger.warning('Evidence processing failed: %s', type(exc).__name__)
                error = public_error(exc)
                errors.append(error)
                counts['errors'] += 1
                retryable = error['code'] in RETRYABLE_PROVIDER_ERRORS
                failure = 'failed_retryable' if retryable else 'failed'
                if evidence and self.repo.processing(topic['topic_id'], evidence['evidence_id']) not in ('completed', 'review'):
                    self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], failure, error=error)
                end(trace, 'failed', error=error)
                record(failure, error)
                if error['code'] in PROVIDER_ERRORS_THAT_STOP_RUN:
                    blocking_error = error
                    counts['unprocessed'] = len(work) - index - 1
                    break
        # Successful fetches alone do not make a completely failed processing run partial success.
        outcomes = sum(counts[k] for k in ('committed', 'skipped', 'review', 'irrelevant', 'no_change'))
        status = ('completed_with_errors' if outcomes or (not work and usable) else 'failed') if errors else 'completed'
        error = {'code': 'RUN_ERRORS', 'message': 'One or more steps failed.', 'details': errors} if errors else None
        if blocking_error:
            error['blocking_error'] = blocking_error
        self.repo.update_run(identity, status=status, completed_at=now(), counts=counts,
                             error=error)
