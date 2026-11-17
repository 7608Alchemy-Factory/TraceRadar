import logging
from threading import RLock
from dataclasses import replace
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from .core import Settings, AppError
from .domain import TopicInput, TopicPatch, Topic, Source, SourceApproval, SourceRegisterInput, RunInput, ReviewResolutionInput, ModelSettingsInput, ModelDiscoveryInput, uid, now
from .repositories import Repository
from .connectors import SOURCES, recommend_sources, validate_source_definition
from .llm import get_provider
from .orchestration import RunManager
from .model_settings import model_status, save_model_settings,discover_models
from .replay_catalog import replay_catalog

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


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
        if origin and request.url.path.startswith('/api/settings/model') and request.method in ('POST','PUT'):
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

    @app.get('/api/replay/corpora')
    def corpora():
        return replay_catalog(config)

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

    @app.post('/api/topics/{identity}/source-recommendations')
    def recommendations(identity: str):
        item = repo.get('topics', identity)
        result = recommend_sources(item, repo.sources())
        persisted = []
        for src in result['sources']:
            if not src.get('registered'):
                clean_src = {k: src[k] for k in ('source_id', 'name', 'type', 'endpoint', 'config')}
                validated = validate_source_definition(clean_src, config.corpus_root)
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
        return dict(run=repo.get('runs', identity), steps=repo.steps(identity))

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
