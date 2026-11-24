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


def test_topic_tag_order_limit_and_existing_records(tmp_path):
    with TestClient(create_app(Settings(database_path=str(tmp_path / 'tags.db')))) as client:
        body = {'title': 'Research', 'description': 'Track funding and new studies'}
        tags = ['science', 'education', 'business', 'custom']
        assert client.post('/api/topics', json={**body, 'tags': tags}).status_code == 422
        result = client.post('/api/topics', json={**body, 'tags': tags[:3]}).json()
        assert result['tags'] == tags[:3]
        identity = result['topic_id']
        url = f'/api/topics/{identity}'
        assert client.patch(url, json={'tags': tags}).status_code == 422
        client.app.state.repo.patch_topic(identity, {'tags': tags})
        assert client.get('/api/topics').json()[0]['tags'] == tags
        assert client.patch(url, json={'title': 'Research news'}).json()['tags'] == tags
        assert client.patch(url, json={'tags': tags[1:]}).json()['tags'] == tags[1:]


def test_event_deletion_keeps_other_events_articles_and_runs(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / 'delete-event.db')))
    with TestClient(app) as client:
        identity, other = topic(client), topic(client)
        url = f'/api/topics/{identity}'
        for batch in ('1', '2', '3'):
            run(client, identity, batch)
        run(client, other, '1')
        before = client.get(url + '/dashboard').json()
        other_before = client.get(f'/api/topics/{other}/dashboard').json()
        evidence_before = client.get(url + '/evidence').json()
        event = next(item for item in before['events'] if item['title'].endswith('A'))
        event_path = f"/api/events/{event['event_id']}"
        detail = client.get(event_path).json()
        assert detail['history'] and detail['evidence'] and detail['reviews'] and detail['updates']

        pending, _ = app.state.repo.create_run(identity, {'fixture_batch_id': 'empty', 'provider': 'mock'}, None, None)
        assert client.delete(event_path).status_code == 409
        app.state.manager.submit(pending['run_id'])
        app.state.manager.queue.join()
        runs = client.get(url + '/runs').json()

        assert client.delete(event_path).json() == {'event_id': event['event_id']}
        assert client.get(event_path).status_code == 404
        assert client.delete(event_path).status_code == 404
        after = client.get(url + '/dashboard').json()
        assert after['events'] == [item for item in before['events'] if item != event]
        assert all(item['event_id'] != event['event_id'] for item in client.get('/api/radar').json())
        assert all(item['event_id'] != event['event_id'] for item in client.get(url + '/notifications').json())
        assert client.get(url + '/runs').json() == runs
        assert client.get(f'/api/topics/{other}/dashboard').json() == other_before
        assert [item['evidence'] for item in client.get(url + '/evidence').json()] == [
            item['evidence'] for item in evidence_before
        ]
        assert not any(item['assessment']['association']['event_id'] == event['event_id'] for item in after['reviews'])
        run(client, identity, '1')
        assert client.get(url + '/dashboard').json()['events'] == after['events']
