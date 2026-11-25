from datetime import datetime, timedelta, timezone
import time

from fastapi.testclient import TestClient

from app.core import Settings
from app.domain import ScheduleInput
from app.main import create_app
from app.repositories import utc_stamp
from test_connectors_and_pipeline import _wait_run
from test_workflow import topic


def settings(tmp_path):
    return Settings(database_path=str(tmp_path / 'tracker.db'),
                    api_key='test-only', model='test-model', base_url='https://provider.example')


def approve_live(client, identity):
    source = {'source_id': 'schedule_rss', 'type': 'rss', 'name': 'Schedule RSS',
              'endpoint': 'https://news.example/feed.xml', 'config': {}}
    assert client.post('/api/sources', json=source).status_code == 201
    approved = client.put(f'/api/topics/{identity}/sources', json={'approved_source_ids': ['schedule_rss']})
    assert approved.status_code == 200


def test_start_now_runs_on_the_backend_without_a_client_trigger(tmp_path, monkeypatch):
    config = settings(tmp_path)
    app = create_app(config)
    fetched = []

    def fetch(source, batch, **options):
        fetched.append(source['source_id'])
        return []

    monkeypatch.setattr(app.state.manager.connector, 'fetch', fetch)
    with TestClient(app) as client:
        identity = topic(client, approve=False)
        path = f'/api/topics/{identity}/schedule'
        request = {'mode': 'interval', 'interval_minutes': 60, 'agent_mode': True}
        assert client.put(path, json=request).status_code == 422
        approve_live(client, identity)
        response = client.put(path, json=request)
        assert response.status_code == 200
        assert response.json()['last_run_id'] is None
        assert client.put(path, json={'mode': 'dates', 'times': ['2000-01-01T00:00:00Z']}).status_code == 422

        give_up = time.monotonic() + 8
        while time.monotonic() < give_up:
            plan, = client.get('/api/workspace-updates').json()['schedules']
            if plan['last_run_id']:
                break
            time.sleep(.1)
        assert plan['last_run_id']
        detail = _wait_run(client, plan['last_run_id'])['run']
        assert detail['status'] == 'completed' and detail['trigger_type'] == 'scheduled'
        assert detail['snapshot']['options']['agent_mode'] is True
        assert fetched == ['schedule_rss']
        assert datetime.fromisoformat(plan['next_at']) - datetime.fromisoformat(detail['scheduled_for']) == timedelta(hours=1)
        assert client.delete(path).status_code == 204
        assert client.get('/api/schedules').json() == []
        assert client.get(f"/api/runs/{detail['run_id']}").status_code == 200


def test_due_dates_are_atomic_and_catch_up_after_restart(tmp_path, monkeypatch):
    config = settings(tmp_path)
    app = create_app(config)
    identity = ''
    current = datetime.now(timezone.utc)
    times = [current + timedelta(minutes=minute) for minute in (1, 2, 3)]
    with TestClient(app) as client:
        identity = topic(client, approve=False)
        approve_live(client, identity)
        repo = app.state.repo
        repo.save_schedule(identity, ScheduleInput(mode='dates', times=times), current)
        overdue = current + timedelta(minutes=2, seconds=10)
        assert repo.enqueue_due_schedule(identity, overdue, model=config.model, model_ready=False) is None
        assert repo.schedules()[0]['error'] == 'QWEN_NOT_CONFIGURED'
        first_id = repo.enqueue_due_schedule(identity, overdue, model=config.model, model_ready=True)
        assert repo.get('runs', first_id)['scheduled_for'] == utc_stamp(times[1])
        assert repo.schedules()[0]['next_at'] == utc_stamp(times[2])
        assert repo.enqueue_due_schedule(identity, times[2], model=config.model, model_ready=True) is None

    restarted = create_app(config)
    monkeypatch.setattr(restarted.state.manager.connector, 'fetch', lambda *args, **kwargs: [])
    with TestClient(restarted) as client:
        assert _wait_run(client, first_id)['run']['status'] == 'completed'
        repo = restarted.state.repo
        final_id = repo.enqueue_due_schedule(identity, times[2], model=config.model, model_ready=True)
        assert final_id
        assert repo.schedules()[0]['next_at'] is None
        assert repo.enqueue_due_schedule(identity, times[2], model=config.model, model_ready=True) is None
        assert repo.get('runs', final_id)['snapshot']['options']['source_ids'] == ['schedule_rss']
