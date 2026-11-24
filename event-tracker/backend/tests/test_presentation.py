import json

import httpx
from fastapi.testclient import TestClient

from app.core import Settings
from app.main import create_app
from test_workflow import run, topic


def test_radar_and_event_detail_use_recorded_updates(tmp_path):
    settings = Settings(database_path=str(tmp_path / 'test.db'), api_key='', base_url='', model='')
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/radar').json() == []
        identity = topic(client)
        for batch in ('1', '2', '3'):
            run(client, identity, batch)
        signals = client.get('/api/radar').json()
        assert len(signals) == 2
        assert all(signal['topic_id'] == identity for signal in signals)
        main_event = next(signal for signal in signals if signal['title'].endswith('A'))
        detail = client.get(f"/api/events/{main_event['event_id']}").json()
        assert {entry['transition_id'] for entry in detail['updates']} == {
            transition['transition_id'] for transition in detail['history']
        }
        assert {entry['provider'] for entry in detail['updates']} == {'mock'}
        assert {entry['priority'] for entry in detail['updates']} >= {'routine', 'breaking'}


def test_reference_translation_validates_provider_response(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / 'test.db'), api_key='test-key',
                        base_url='https://translate.example.com/v1', model='test-model')

    def translate_request(url, **kwargs):
        assert url == 'https://translate.example.com/v1/chat/completions'
        assert json.loads(kwargs['json']['messages'][1]['content']) == ['原文']
        translations = ['Original text'] if valid else []
        return httpx.Response(200, json={'choices': [{'message': {
            'content': json.dumps({'translations': translations})
        }}]})

    monkeypatch.setattr('app.translation.httpx.post', translate_request)
    with TestClient(create_app(settings)) as client:
        valid = True
        payload = {'language': 'en', 'items': ['原文']}
        response = client.post('/api/translation', json=payload)
        assert response.json() == {'translations': ['Original text']}
        valid = False
        response = client.post('/api/translation', json=payload)
        assert response.status_code == 502
        assert response.json()['error']['code'] == 'TRANSLATION_FORMAT'

def test_topic_tags_and_deletion_isolate_shared_records(tmp_path):
    settings = Settings(database_path=str(tmp_path / 'test.db'), api_key='', base_url='', model='')
    with TestClient(create_app(settings)) as client:
        first = topic(client)
        second = topic(client)
        for identity in (first, second):
            run(client, identity, '1')
        saved = client.patch(f'/api/topics/{first}', json={'tags': ['tech', '  Local news  ', 'TECH']})
        assert saved.json()['tags'] == ['tech', 'Local news']
        assert client.get('/api/topics').json()[0]['tags'] == ['tech', 'Local news']
        removed_event = client.get(f'/api/topics/{first}/dashboard').json()['events'][0]
        original = client.get(f'/api/topics/{second}/dashboard').json()
        assert client.delete(f'/api/topics/{first}').status_code == 200
        assert client.get(f'/api/topics/{first}').status_code == 404
        assert client.get(f"/api/events/{removed_event['event_id']}").status_code == 404
        assert client.get(f'/api/topics/{second}/dashboard').json() == original
        assert client.get(f'/api/topics/{second}/evidence').json()


def test_delete_refuses_queued_run(tmp_path):
    settings = Settings(database_path=str(tmp_path / 'test.db'), api_key='', base_url='', model='')
    app = create_app(settings)
    with TestClient(app) as client:
        identity = topic(client)
        record, _ = app.state.repo.create_run(
            identity, {'fixture_batch_id': '1', 'provider': 'mock'}, None, None)
        blocked = client.delete(f'/api/topics/{identity}')
        assert blocked.status_code == 409
        assert blocked.json()['error']['code'] == 'TOPIC_RUN_ACTIVE'
        assert client.get(f"/api/runs/{record['run_id']}").json()['run']['status'] == 'pending'
