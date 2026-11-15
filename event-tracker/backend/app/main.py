import logging
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from .core import Settings, AppError
from .domain import TopicInput, TopicPatch, Topic, SourceApproval, RunInput, uid, now
from .repositories import Repository
from .connectors import SOURCES
from .llm import get_provider
from .orchestration import RunManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


def create_app(settings=None):
    config = settings or Settings()
    repo = Repository(config.database_path)
    manager = RunManager(repo, config)

    @asynccontextmanager
    async def lifespan(app):
        repo.initialize(SOURCES)
        manager.start()
        yield
        manager.stop()

    app = FastAPI(title='TraceRadar local tracker', lifespan=lifespan)
    app.state.repo, app.state.manager = repo, manager

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
        return {'status': 'ok', 'default_provider': config.provider, 'data_mode': 'simulated_fixture'}

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
        repo.get('topics', identity)
        return {'recommendation_mode': 'static', 'sources': [dict(s, reason='Registered simulated cybersecurity demo source.') for s in repo.sources()]}

    @app.put('/api/topics/{identity}/sources')
    def approve(identity: str, body: SourceApproval):
        return {'approved_sources': repo.approve(identity, body.approved_source_ids)}

    @app.post('/api/topics/{identity}/runs', status_code=202)
    def run(identity: str, body: RunInput, idempotency_key: str | None = Header(default=None, max_length=200)):
        repo.get('topics', identity)
        provider = body.provider or config.provider
        get_provider(provider, config)  # Configuration errors must not create pending runs.
        request = dict(fixture_batch_id=body.fixture_batch_id, provider=provider)
        record, created = repo.create_run(identity, request, idempotency_key, config.model if provider == 'qwen' else None)
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
                    recent_runs=repo.runs(identity), reviews=repo.reviews(identity),
                    notification_counts={a: len(repo.notifications(identity, a)) for a in ('immediate', 'digest', 'record_only')})

    @app.get('/api/events/{identity}')
    def event(identity: str):
        return repo.event_detail(identity)

    @app.get('/api/topics/{identity}/notifications')
    def notifications(identity: str, action: Literal['immediate', 'digest', 'record_only'] | None = None):
        repo.get('topics', identity)
        return repo.notifications(identity, action)

    return app


app = create_app()
