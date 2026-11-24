import logging
from threading import RLock
from dataclasses import replace
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import Body, FastAPI, Header, Request
from pydantic import Field
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from .core import Settings, AppError
from .domain import Model, TopicInput, TopicPatch, Topic, Source, SourceApproval, SourceRegisterInput, RunInput, ReviewResolutionInput, ModelSettingsInput, ModelDiscoveryInput, uid, now
from .repositories import Repository
from .connectors import SOURCES, ConnectorRouter, recommend_sources, validate_source_definition
from .llm import get_provider
from .orchestration import RunManager
from .model_settings import model_status, save_model_settings,discover_models
from .replay_catalog import replay_catalog
from .web_connectors import save_search_settings, search_settings_status
from .translation import TranslationInput, translate

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


class SourceRecommendationInput(Model):
    llm_summary: bool | None = None
    search_languages: list[Literal['zh-CN', 'zh-HK', 'en-US', 'ko', 'ja']] | None = Field(default=None, max_length=5)


class SearchSettingsInput(Model):
    provider: Literal['auto', 'bocha', 'tavily', 'free'] | None = None
    bocha_api_key: str | None = Field(default=None, max_length=300, repr=False)
    tavily_api_key: str | None = Field(default=None, max_length=300, repr=False)
    jina_api_key: str | None = Field(default=None, max_length=300, repr=False)
    web_fetch_jina_fallback: bool | None = None


def create_app(settings=None):
    config = settings or Settings()
    repo = Repository(config.database_path)
    manager = RunManager(repo, config)
    config_lock = RLock()

    @asynccontextmanager
    async def lifespan(app):
        repo.initialize(SOURCES)
        manager.start()
        yield
        manager.stop()

    app = FastAPI(title='TraceRadar local tracker', lifespan=lifespan)
    app.state.repo, app.state.manager = repo, manager

    @app.middleware('http')
    async def local_model_configuration(request: Request, call_next):
        origin=request.headers.get('origin')
        provider_route = (
            request.url.path.startswith('/api/settings/model')
            or request.url.path.startswith('/api/settings/search')
            or request.url.path == '/api/translation'
        )
        if origin and provider_route and request.method in ('POST','PUT'):
            try:
                host=urlsplit(origin).hostname
            except ValueError:
                host=None
            if not host or (host!=request.url.hostname and not {host,request.url.hostname}<={'127.0.0.1','localhost','::1'}):
                return JSONResponse(status_code=403,content={'error':{'code':'LOCAL_SETTINGS_ONLY','message':'Configure models from the local workspace.'}})
        return await call_next(request)

    def error(code, message, status, details=None):
        return JSONResponse(status_code=status, content={'error': dict(code=code, message=message, details=details or {}, run_id=None)})

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error('VALIDATION_ERROR', 'Check the request fields.', 422,
                     {'fields': [{'location': list(e['loc']), 'message': e['msg']} for e in exc.errors()]})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error('HTTP_ERROR', str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logging.error('Request failed: %s', type(exc).__name__)
        return error('INTERNAL_ERROR', 'Request failed. Check backend logs.', 500)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'default_provider': config.provider, 'data_mode': 'registered_sources'}

    @app.get('/api/settings/model')
    def read_model_settings():
        return model_status(config)

    @app.put('/api/settings/model')
    def update_model_settings(body: ModelSettingsInput):
        with config_lock:
            if repo.active_runs():
                raise AppError('RUN_ACTIVE','Wait for all runs to finish before changing the model.')
            return save_model_settings(config,body)

    @app.post('/api/settings/model/models')
    def available_models(body: ModelDiscoveryInput):
        return discover_models(config,body)

    @app.post('/api/settings/model/check')
    def check_model():
        with config_lock:
            provider=get_provider('qwen',replace(config,max_attempts=1))
            evidence_id=uid()
            assessment=provider.assess({'title':'Connection check','description':'No substantive event is being tracked.'},[],
                {'evidence_id':evidence_id,'title':'Connection check','content':'This is a connection test, not news.',
                 'published_at':now(),'source_id':'connection_check'},[])
            if assessment.evidence_id!=evidence_id:
                raise AppError('PROVIDER_SCHEMA','The model returned an incorrect evidence reference.',502)
            return {'status':'ok','model':config.model,'attempts':provider.attempt}

    @app.get('/api/settings/search')
    def read_search_settings():
        with config_lock:
            return search_settings_status(config)

    @app.put('/api/settings/search')
    def update_search_settings(body: SearchSettingsInput):
        with config_lock:
            if repo.active_runs():
                raise AppError('RUN_ACTIVE', 'Wait for all runs to finish before changing search settings.')
            return save_search_settings(config, body)

    @app.post('/api/settings/search/check')
    def check_search():
        with config_lock:
            snap = replace(config)
        status = search_settings_status(snap)
        router = ConnectorRouter(snap.fixture_path, snap.corpus_root, settings=snap)
        items = router.fetch(
            {
                'source_id': 'search_check',
                'name': 'Search Check',
                'type': 'web_search',
                'endpoint': 'search://check',
                'config': {
                    'query': '人工智能 最新进展' if status['active_provider'] == 'bocha' else 'OpenAI',
                    'search_provider': status['active_provider'],
                    'max_items': 2,
                    'fetch_full_text': False,
                    'fallback_to_free': False,
                },
            },
            'live',
        )
        return {
            'status': 'ok',
            'active_provider': status['active_provider'],
            'active_label': status['active_label'],
            'provider_used': router.last_fetch_stats.get('provider_used'),
            'items': len(items),
        }

    @app.get('/api/replay/corpora')
    def corpora():
        return replay_catalog(config)

    @app.post('/api/translation')
    def reference_translation(body: TranslationInput):
        with config_lock:
            settings = replace(config)
        return translate(settings, body)

    @app.get('/api/radar')
    def radar():
        return repo.radar()

    @app.post('/api/topics', status_code=201, response_model=Topic)
    def create_topic(body: TopicInput):
        stamp = now()
        return repo.save_topic(dict(**body.model_dump(), topic_id=uid(), status='draft', created_at=stamp, updated_at=stamp))

    @app.get('/api/topics', response_model=list[Topic])
    def topics():
        return repo.list_topics()

    @app.get('/api/topics/{identity}')
    def topic(identity: str):
        return dict(topic=repo.get('topics', identity), approved_sources=repo.sources(identity))

    @app.patch('/api/topics/{identity}', response_model=Topic)
    def patch_topic(identity: str, body: TopicPatch):
        return repo.patch_topic(identity, body.model_dump(exclude_none=True))

    @app.delete('/api/topics/{identity}')
    def delete_topic(identity: str):
        repo.delete_topic(identity)
        return {'topic_id': identity}

    @app.post('/api/topics/{identity}/source-recommendations')
    def recommendations(
        identity: str,
        llm_summary: bool = False,
        body: SourceRecommendationInput | None = Body(default=None),
    ):
        item = repo.get('topics', identity)
        want_llm = body.llm_summary if (body is not None and body.llm_summary is not None) else llm_summary
        search_languages = body.search_languages if body is not None else None
        approved_ids = {s['source_id'] for s in repo.sources(identity)}
        with config_lock:
            settings_snapshot = replace(config)
        result = recommend_sources(
            item,
            repo.sources(),
            current_events=repo.events(identity),
            evidence_decisions=repo.evidence_decisions(identity),
            notifications=repo.notifications(identity),
            corpus_root=settings_snapshot.corpus_root,
            approved_source_ids=approved_ids,
            llm_summary=want_llm,
            settings=settings_snapshot,
            search_languages=search_languages,
        )
        persisted = []
        for src in result['sources']:
            # Persist anchors/channels so direct approval keeps working, but never pre-persist unapproved branch candidates.
            if not src.get('registered') and src.get('role') != 'branch':
                clean_src = {k: src[k] for k in ('source_id', 'name', 'type', 'endpoint', 'config')}
                validated = validate_source_definition(clean_src, settings_snapshot.corpus_root)
                repo.register_source(validated)
                src = dict(src, registered=True)
            persisted.append(src)
        result['sources'] = persisted
        return result

    @app.post('/api/sources', status_code=201, response_model=Source)
    def register_source(body: SourceRegisterInput):
        validated = validate_source_definition(body.model_dump(mode='json'), config.corpus_root)
        return repo.register_source(validated)

    @app.put('/api/topics/{identity}/sources')
    def approve(identity: str, body: SourceApproval):
        return {'approved_sources': repo.approve(identity, body.approved_source_ids)}

    @app.post('/api/topics/{identity}/runs', status_code=202)
    def run(identity: str, body: RunInput, idempotency_key: str | None = Header(default=None, max_length=200)):
        repo.get('topics', identity)
        provider = body.provider or config.provider
        request = dict(fixture_batch_id=body.fixture_batch_id, provider=provider)
        options = {}
        if body.source_ids is not None:
            options['source_ids']=body.source_ids
        if body.as_of is not None:
            options['as_of'] = body.as_of.isoformat()
        if body.prefilter_enabled:
            options['prefilter_enabled'] = True
        if not body.near_duplicate_enabled:
            options['near_duplicate_enabled'] = False
        if body.manual_items:
            options['manual_items'] = [item.model_dump(mode='json') for item in body.manual_items]
        with config_lock:
            get_provider(provider, config)  # Configuration errors must not create pending runs.
            record, created = repo.create_run(identity, request, idempotency_key, config.model if provider == 'qwen' else None, options=options or None)
        if created:
            manager.submit(record['run_id'])
        return {'run_id': record['run_id'], 'status': record['status']}

    @app.get('/api/topics/{identity}/runs')
    def runs(identity: str):
        repo.get('topics', identity)
        return repo.runs(identity)

    @app.get('/api/runs/{identity}')
    def run_detail(identity: str):
        run = repo.get('runs', identity)
        steps = repo.steps(identity)
        run_error = run.get('error') or {}
        blocking_error = run_error.get('blocking_error')
        if run_error.get('code') in ('interrupted', 'WORKFLOW_ERROR'):
            blocking_error = run_error
        attempts = {step['evidence_id']: step['attempt'] for step in steps if step['step_name'] == 'assess_evidence'}
        evidence = []
        for _, raw, outcome in repo.run_inputs(identity):
            if outcome is None:
                evidence_id = raw['evidence_id']
                attempted = attempts.get(evidence_id, 0)
                status = 'not_attempted'
                if attempted:
                    status = 'processing' if run['status'] == 'running' else 'failed'
                outcome = dict(status=status, attempts=attempted, error=blocking_error,
                               evidence_id=evidence_id, event_outcomes=[])
            evidence.append(dict(source_id=raw['source_id'], external_id=raw['external_id'], title=raw['title'], **outcome))
        return dict(run=run, steps=steps, evidence=evidence)

    @app.post('/api/runs/{identity}/resume', status_code=202)
    def resume(identity: str, idempotency_key: str | None = Header(default=None, max_length=200)):
        original = repo.get('runs', identity)
        if original['status'] not in ('failed', 'completed_with_errors'):
            raise AppError('RUN_NOT_RESUMABLE', 'Only a finished run with errors can be resumed.')
        provider = original['provider']
        with config_lock:
            get_provider(provider, config)
            record, created = repo.resume_run(original, config.model if provider == 'qwen' else None, idempotency_key)
        if created:
            manager.submit(record['run_id'])
        return {'run_id': record['run_id'], 'status': record['status']}

    @app.get('/api/topics/{identity}/dashboard')
    def dashboard(identity: str):
        return dict(topic=repo.get('topics', identity), approved_sources=repo.sources(identity), events=repo.events(identity),
                    recent_runs=repo.runs(identity), reviews=repo.reviews(identity), review_resolutions=repo.review_resolutions(identity),
                    notification_counts={a: len(repo.notifications(identity, a)) for a in ('immediate', 'digest', 'record_only')})

    @app.get('/api/events/{identity}')
    def event(identity: str):
        return repo.event_detail(identity)

    @app.get('/api/topics/{identity}/evidence')
    def evidence_decisions(identity: str):
        repo.get('topics',identity)
        return repo.evidence_decisions(identity)

    @app.post('/api/topics/{identity}/reviews/{evidence_id}/resolve')
    def resolve_review(identity: str, evidence_id: str, body: ReviewResolutionInput):
        return manager.state_manager.resolve_review(repo.get('topics',identity),evidence_id,body)

    @app.get('/api/topics/{identity}/notifications')
    def notifications(identity: str, action: Literal['immediate', 'digest', 'record_only'] | None = None):
        repo.get('topics', identity)
        return repo.notifications(identity, action)

    return app


app = create_app()
