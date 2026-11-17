import json
import time
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from app.core import Settings, AppError
from app.main import create_app
from app.domain import Run, RunStep, Event, Assessment
from app.llm import MockProvider, QwenProvider


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=str(tmp_path / 'tracker.db'), provider='mock', api_key='', base_url='', model='')


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def topic(client, preference='standard', approve=True):
    response = client.post('/api/topics', json=dict(title='ExampleSoft security', description='Track vulnerabilities and patches', notification_preferences=preference))
    assert response.status_code == 201
    identity = response.json()['topic_id']
    if approve:
        assert client.put(f'/api/topics/{identity}/sources', json={'approved_source_ids': ['vendor', 'research']}).status_code == 200
    return identity


def run(client, topic_id, batch, **kwargs):
    response = client.post(f'/api/topics/{topic_id}/runs', json={'fixture_batch_id':batch, 'provider':'mock'}, **kwargs)
    assert response.status_code == 202, response.text
    identity = response.json()['run_id']
    for _ in range(400):
        data = client.get(f'/api/runs/{identity}').json()
        if data['run']['status'] not in ('pending', 'running'):
            Run.model_validate(data['run'])
            for step in data['steps']:
                RunStep.model_validate(step)
            return data
        time.sleep(.01)
    pytest.fail('Run did not finish')


def business_counts(client, identity):
    d = client.get(f'/api/topics/{identity}/dashboard').json()
    transitions = sum(len(client.get(f"/api/events/{e['event_id']}").json()['history']) for e in d['events'])
    return len(d['events']), transitions, d['notification_counts']


def test_three_batches_replay_and_topic_isolation(client):
    expected = json.loads((Path(__file__).resolve().parents[2] / 'evaluation/expected/demo.json').read_text())
    first = topic(client)
    for batch in ('1','2','3'):
        assert run(client, first, batch)['run']['status'] == 'completed'
    baseline = (expected['events'], expected['transitions'], expected['notifications'])
    assert business_counts(client, first) == baseline
    d = client.get(f'/api/topics/{first}/dashboard').json()
    assert len(d['reviews']) == expected['reviews']
    assert d['events'][0]['state']['current_status'] == 'patched'
    for e in d['events']:
        Event.model_validate(e)
    for batch in ('1','2','3'):
        assert run(client, first, batch)['run']['status'] == 'completed'
    assert business_counts(client, first) == baseline
    second = topic(client)
    for batch in ('1','2','3'):
        run(client, second, batch)
    assert business_counts(client, second) == baseline


def test_approval_validation_and_qwen_configuration(client):
    identity = topic(client, approve=False)
    assert client.post(f'/api/topics/{identity}/runs', json={'fixture_batch_id':'1'}).json()['error']['code'] == 'NO_APPROVED_SOURCE'
    assert client.put(f'/api/topics/{identity}/sources', json={'approved_source_ids':['../../secret']}).status_code == 422
    assert client.post('/api/topics', json={'title':''}).json()['error']['code'] == 'VALIDATION_ERROR'
    assert client.get('/api/topics/missing').status_code == 404
    assert client.post(f'/api/topics/{identity}/runs', json={'fixture_batch_id':'1', 'provider':'qwen'}).json()['error']['code'] == 'QWEN_NOT_CONFIGURED'
    assert client.get(f'/api/topics/{identity}/runs').json() == []


def test_source_filter_and_empty_batch(client):
    identity = topic(client, approve=False)
    client.put(f'/api/topics/{identity}/sources',json={'approved_source_ids':['vendor']})
    run(client,identity,'1')
    run(client,identity,'2')
    assert len(client.get(f'/api/topics/{identity}/dashboard').json()['events']) == 1
    assert {e['source_id'] for e in client.app.state.repo.available_evidence(identity).values()} == {'vendor'}
    assert run(client, identity, 'empty')['run']['status'] == 'completed'


def test_atomic_rollback_and_retry(client):
    identity = topic(client)
    manager = client.app.state.manager
    def fail():
        raise RuntimeError('Injected transaction failure')
    manager.state_manager.commit_fault = fail
    failed = run(client, identity, '1')
    assert failed['run']['status'] == 'completed_with_errors'  # unrelated evidence still processed
    assert business_counts(client, identity) == (0, 0, {'immediate':0,'digest':0,'record_only':0})
    manager.state_manager.commit_fault = None
    assert run(client, identity, '1')['run']['status'] == 'completed'
    assert business_counts(client, identity)[0:2] == (1,1)


def test_run_keys_and_active_lock(client):
    identity = topic(client)
    repo = client.app.state.repo
    body = {'fixture_batch_id':'1','provider':'mock'}
    pending, created = repo.create_run(identity, body, 'request-1', None)
    assert created
    response = client.post(f'/api/topics/{identity}/runs',json=body,headers={'Idempotency-Key':'request-1'})
    assert response.json()['run_id'] == pending['run_id']
    assert client.post(f'/api/topics/{identity}/runs',json=body).json()['error']['code'] == 'TOPIC_RUN_ACTIVE'
    assert client.put(f'/api/topics/{identity}/sources',json={'approved_source_ids':[]}).status_code == 409
    assert client.post(f'/api/topics/{identity}/runs',json={'fixture_batch_id':'2','provider':'mock'},headers={'Idempotency-Key':'request-1'}).status_code == 409
    client.app.state.manager.submit(pending['run_id'])
    client.app.state.manager.queue.join()


def test_restart_keeps_data_and_marks_interrupted(settings):
    with TestClient(create_app(settings)) as c:
        identity = topic(c)
        run(c, identity, '1')
        pending, _ = c.app.state.repo.create_run(identity, {'fixture_batch_id':'2','provider':'mock'}, None, None)
    with TestClient(create_app(settings)) as c:
        assert business_counts(c, identity)[0:2] == (1,1)
        assert len(c.get(f'/api/topics/{identity}').json()['approved_sources']) == 2
        assert c.get(f"/api/runs/{pending['run_id']}").json()['run']['error']['code'] == 'interrupted'
        assert run(c, identity, '2')['run']['status'] == 'completed'


def test_digest_only_and_low_update(client, monkeypatch):
    identity = topic(client,'digest_only')
    for batch in ('1','2','3'):
        run(client,identity,batch)
    assert business_counts(client,identity)[2] == {'immediate':0,'digest':4,'record_only':0}
    manager = client.app.state.manager
    original = manager.connector.fetch
    def extra(source,batch):
        data = original(source,batch)
        if source['source_id']=='vendor' and batch=='empty':
            sample = original(source,'3')[0]
            sample.update(external_id='editorial',title='Editorial note',content='SIMULATED: ExampleSoft vulnerability A editorial clarification.')
            import hashlib
            sample['content_hash'] = hashlib.sha256(sample['content'].encode()).hexdigest()
            return [sample]
        return data
    monkeypatch.setattr(manager.connector,'fetch',extra)
    assert run(client,identity,'empty')['run']['status']=='completed'
    assert business_counts(client,identity)[2]['record_only']==1


@pytest.mark.parametrize('corrupt', ['evidence', 'cross_topic', 'reference', 'version'])
def test_invalid_provider_outputs_never_commit(client, monkeypatch, corrupt):
    other = topic(client)
    run(client, other, '1')
    foreign = client.get(f'/api/topics/{other}/dashboard').json()['events'][0]
    identity = topic(client)
    original = MockProvider.assess
    def invalid(self,topic,events,evidence,sources):
        a = original(self,topic,events,evidence,sources)
        if not a.is_relevant:
            return a
        if corrupt=='evidence': a.evidence_id='missing'
        if corrupt=='cross_topic':
            a.association.type='existing_event'
            a.association.event_id=foreign['event_id']
        if corrupt=='reference': a.proposed_state.key_facts[0].evidence_ids=['missing']
        if corrupt=='version':
            # Force a stale update in an existing event below.
            a.expected_version=999
        return a
    if corrupt=='version':
        run(client,identity,'1')
    monkeypatch.setattr(MockProvider,'assess',invalid)
    baseline = business_counts(client,identity)
    result = run(client,identity,'2' if corrupt=='version' else '1')
    assert result['run']['counts']['errors'] > 0
    # In batch 2 B is a valid new event, so isolate vendor for the version case.
    if corrupt!='version': assert business_counts(client,identity)==baseline
    else:
        a = client.get(f'/api/topics/{identity}/dashboard').json()['events'][0]
        assert a['version']==1


def test_source_failure_is_not_empty_success(client, monkeypatch):
    identity=topic(client)
    def fail(*args): raise OSError('Fixture unavailable')
    monkeypatch.setattr(client.app.state.manager.connector,'fetch',fail)
    result=run(client,identity,'empty')
    assert result['run']['status']=='failed'
    assert all(s['error']['code']=='source_error' for s in result['steps'])


def test_qwen_bounded_repair_auth_and_timeout(settings):
    settings.api_key='test';settings.base_url='https://example.invalid/v1';settings.model='test-model'
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'invalid JSON'}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider=QwenProvider(settings,client)
        with pytest.raises(AppError, match='Assessment schema'): provider.assess({},[],{},[])
        assert len(calls)==2
    calls.clear()
    def auth(request):
        calls.append(request)
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(auth)) as client:
        with pytest.raises(AppError) as caught: QwenProvider(settings,client).assess({},[],{},[])
        assert caught.value.code=='PROVIDER_AUTH'
        assert len(calls)==1
    calls.clear()
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout('test')
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(AppError, match='timed out'): QwenProvider(settings,client).assess({},[],{},[])
        assert len(calls)==2
