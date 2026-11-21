import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import Settings
from app.main import create_app
from test_connectors_and_pipeline import _wait_run


CASE = json.loads((Path(__file__).resolve().parents[2] /
                   'evaluation/cases/mcd_hk_campaigns_v1/case.json').read_text(encoding='utf-8'))


def mcd_decisions(evidence_id):
    claims = [
        ('麥麥慳套票', '換購券可用至 2026-11-09，套票停售日未明。'),
        ('$3 茉莉綠茶', '茶飲優惠截至 2026-10-02。'),
    ]
    return [
        dict(association=dict(type='new_event', title=title, reason='Distinct offer'),
             novelty='new_fact', update_kind='event_created', reason_codes=['offer_announced'],
             supported_claims=[dict(fact=claim, evidence_ids=[evidence_id])],
             proposed_state=dict(current_status='announced', sources=[evidence_id], key_facts=[], timeline=[]))
        for title, claim in claims
    ]


def start_mcd(client, stage_index=2):
    source = CASE['runtime_source']
    assert client.post('/api/sources', json=source).status_code == 201
    topic = client.post('/api/topics', json=CASE['topic']).json()['topic_id']
    assert client.put(f'/api/topics/{topic}/sources', json={'approved_source_ids': [source['source_id']]}).status_code == 200
    response = client.post(f'/api/topics/{topic}/runs', json={
        'fixture_batch_id': 'all', 'provider': 'qwen',
        'source_ids': [source['source_id']], 'as_of': CASE['stages'][stage_index]['as_of'],
    })
    assert response.status_code == 202
    return topic, _wait_run(client, response.json()['run_id'])


def provider_stub():
    calls = []
    failures = {}

    def respond(request):
        request = json.loads(request.content)
        evidence = json.loads(request['messages'][1]['content'])['evidence']
        item_id = evidence['external_id'].rsplit(':', 1)[-1]
        calls.append(item_id)
        if item_id in failures:
            return httpx.Response(failures[item_id])
        evidence_id = evidence['evidence_id']
        decisions = mcd_decisions(evidence_id) if item_id == 'MCD-03' else []
        result = dict(evidence_id=evidence_id, is_relevant=bool(decisions), event_decisions=decisions)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})

    return httpx.MockTransport(respond), calls, failures


def model_settings(tmp_path):
    return Settings(database_path=str(tmp_path / 'tracker.db'), base_url='https://provider.example/v1',
                    api_key='test', model='test-model', max_attempts=2)


@pytest.mark.parametrize('repair', [True, False])
def test_mcd02_wrong_evidence_id_cannot_commit(tmp_path, monkeypatch, repair):
    evidence_ids = {}
    attempts = 0

    def respond(request):
        nonlocal attempts
        messages = json.loads(request.content)['messages']
        evidence = json.loads(messages[1]['content'])['evidence']
        item_id = evidence['external_id'].rsplit(':', 1)[-1]
        current = evidence['evidence_id']
        evidence_ids[item_id] = current
        reply = dict(is_relevant=False, event_decisions=[])
        if item_id == 'MCD-02':
            attempts += 1
            decision = dict(association=dict(type='new_event', title=evidence['title'], reason='First report'),
                            novelty='new_fact', reason_codes=['offer_announced'], update_kind='event_created',
                            supported_claims=[dict(fact='試食價 $39。', evidence_ids=[current])],
                            proposed_state=dict(current_status='announced', sources=[current], key_facts=[], timeline=[]))
            reply = dict(is_relevant=True, event_decisions=[decision])
            if attempts == 2:
                assert 'evidence_id must equal the supplied input evidence_id' in messages[-1]['content']
            if attempts == 1 or not repair:
                reply['evidence_id'] = evidence_ids['MCD-01']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as model_client:
        monkeypatch.setattr(httpx, 'post', model_client.post)
        with TestClient(create_app(model_settings(tmp_path))) as client:
            topic, detail = start_mcd(client, stage_index=1)
            outcome = next(row for row in detail['evidence'] if row['external_id'].endswith('MCD-02'))
            events = client.get(f'/api/topics/{topic}/dashboard').json()['events']

    assert outcome['evidence_id'] == evidence_ids['MCD-02']
    assert outcome['attempts'] == 2
    if repair:
        assert outcome['status'] == 'committed'
        event, = events
        assert event['state']['sources'] == [evidence_ids['MCD-02']]
    else:
        assert outcome['status'] == 'failed'
        assert outcome['error']['code'] == 'PROVIDER_SCHEMA'
        assert events == []


@pytest.mark.parametrize('failure,attempts,mcd03_status', [(503, 2, 'committed'), (401, 1, 'not_attempted')])
def test_mcd_fault_report_and_resume(tmp_path, monkeypatch, failure, attempts, mcd03_status):
    transport, calls, failures = provider_stub()
    failures['MCD-04'] = failure
    with httpx.Client(transport=transport) as model_client, TestClient(create_app(model_settings(tmp_path))) as client:
        monkeypatch.setattr(httpx, 'post', model_client.post)
        topic, original = start_mcd(client)
        rows = {item['external_id'].rsplit(':', 1)[-1]: item for item in original['evidence']}
        assert original['run']['status'] == 'completed_with_errors'
        assert [rows['MCD-04']['attempts'], rows['MCD-03']['status']] == [attempts, mcd03_status]
        assert rows['MCD-04']['status'] == ('failed_retryable' if failure == 503 else 'failed')
        assert calls == ['MCD-01', 'MCD-02'] + ['MCD-04'] * attempts + (['MCD-03'] if failure == 503 else [])
        if failure == 401:
            assert rows['MCD-03']['attempts'] == 0
            assert rows['MCD-03']['error']['code'] == 'PROVIDER_AUTH'

        calls.clear()
        failures.clear()
        original_id = original['run']['run_id']
        response = client.post(f'/api/runs/{original_id}/resume', headers={'Idempotency-Key': 'resume-test'})
        assert response.status_code == 202, response.text
        resumed = _wait_run(client, response.json()['run_id'])
        assert resumed['run']['status'] == 'completed'
        assert resumed['run']['snapshot']['options'] == original['run']['snapshot']['options']
        assert resumed['run']['snapshot']['resume_of'] == original_id
        assert calls == (['MCD-04'] if failure == 503 else ['MCD-04', 'MCD-03'])
        assert not any(step['step_name'] == 'fetch_source' for step in resumed['steps'])
        retry = client.post(f'/api/runs/{original_id}/resume', headers={'Idempotency-Key': 'resume-test'})
        assert retry.json()['run_id'] == resumed['run']['run_id']
        assert client.post(f'/api/runs/{original_id}/resume').status_code == 409

        dashboard = client.get(f'/api/topics/{topic}/dashboard').json()
        assert len(dashboard['events']) == 2
        assert sum(dashboard['notification_counts'].values()) == 2
        stored = client.get(f'/api/topics/{topic}/evidence').json()
        mcd03, = [row for row in stored if row['evidence']['external_id'].endswith('MCD-03')]
        evidence_id = mcd03['evidence']['evidence_id']
        assert evidence_id == rows['MCD-03']['evidence_id']
        for event, decision in zip(dashboard['events'], mcd_decisions(evidence_id), strict=True):
            detail = client.get(f"/api/events/{event['event_id']}").json()
            assert event['title'] == decision['association']['title']
            assert event['state']['key_facts'] == decision['supported_claims']
            assert event['state']['sources'] == [evidence_id]
            assert [row['evidence_id'] for row in detail['evidence']] == [evidence_id]
            assert [transition['evidence_ids'] for transition in detail['history']] == [[evidence_id]]

        repeated = client.post(f'/api/topics/{topic}/runs', json={
            'fixture_batch_id': 'all', 'provider': 'qwen',
            'source_ids': [CASE['runtime_source']['source_id']], 'as_of': CASE['stages'][2]['as_of'],
        })
        assert repeated.status_code == 202
        replay = _wait_run(client, repeated.json()['run_id'])
        assert replay['run']['counts']['committed'] == 0
        assert len(client.app.state.repo.events(topic)) == 2
        assert len(client.app.state.repo.notifications(topic)) == 2


def test_mcd_second_event_failure_rolls_back_both_updates(tmp_path, monkeypatch):
    transport, _, _ = provider_stub()
    app = create_app(model_settings(tmp_path))
    with httpx.Client(transport=transport) as model_client, TestClient(app) as client:
        monkeypatch.setattr(httpx, 'post', model_client.post)
        commits = 0

        def fail_second_commit():
            nonlocal commits
            commits += 1
            if commits == 2:
                raise RuntimeError('Second write failed')

        app.state.manager.state_manager.commit_fault = fail_second_commit
        topic, original = start_mcd(client)
        assert commits == 2
        assert original['run']['counts']['errors'] == 1
        assert not app.state.repo.events(topic)
        assert not app.state.repo.notifications(topic)
        app.state.manager.state_manager.commit_fault = None
        original_id = original['run']['run_id']
        response = client.post(f'/api/runs/{original_id}/resume')
        assert response.status_code == 202
        resumed = _wait_run(client, response.json()['run_id'])
        assert resumed['run']['counts']['committed'] == 2
        assert len(app.state.repo.events(topic)) == 2
