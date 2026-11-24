import json
import httpx
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.core import Settings,AppError
from app.domain import ModelDiscoveryInput,ModelSettingsInput,Assessment,Association
from app.llm import MODEL_DECISION,QwenProvider,MockProvider,get_provider,provider_error
from app.model_settings import discover_models,save_model_settings,validate_address
from app.main import create_app
from app.services import StateManager
from test_workflow import topic,run
from test_connectors_and_pipeline import _wait_run


@pytest.mark.parametrize('status,body,expected',[
    (400,{'message':'The supported API model names are model-a, but you passed BrandName'},'PROVIDER_MODEL'),
    (401,{'message':'bad key secret-value'},'PROVIDER_AUTH'),
    (402,{'message':'no balance'},'PROVIDER_BALANCE'),
    (400,{'message':'unsupported response format secret-value'},'PROVIDER_REQUEST')])
def test_provider_errors_are_actionable_and_do_not_echo_secrets(status,body,expected):
    error=provider_error(httpx.Response(status,json={'error':body}))
    assert error.code==expected
    assert 'secret-value' not in error.message


def test_discover_actual_model_ids_without_returning_key(tmp_path):
    settings=Settings(env_path=tmp_path/'.env',api_key='test-key',base_url='https://provider.example',model='bad-label')
    def handler(request):
        assert request.url.path=='/models'
        assert request.headers['authorization']=='Bearer test-key'
        return httpx.Response(200,json={'data':[{'id':'actual-api-id','name':'Display label'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=discover_models(settings,ModelDiscoveryInput(base_url=settings.base_url),client)
    assert result=={'models':[{'id':'actual-api-id','name':'Display label'}]}
    assert 'test-key' not in json.dumps(result)
    assert 'test-key' not in repr(settings)
    assert not settings.env_path.exists()


@pytest.mark.parametrize('failure,code,message', [
    (httpx.ReadTimeout, 'PROVIDER_TIMEOUT', '模型服务响应超时，请稍后重试。'),
    (httpx.ConnectError, 'PROVIDER_NETWORK', '无法连接模型服务，请检查网络和 API 地址。'),
    (408, 'PROVIDER_TIMEOUT', '模型服务响应超时，请稍后重试。'),
    (429, 'PROVIDER_RATE_LIMIT', '请求过于频繁，请稍后重试。'),
    (503, 'PROVIDER_SERVER_ERROR', '模型服务暂时出错，请稍后重试。'),
])
def test_provider_failures_across_model_settings_and_run(tmp_path, monkeypatch, failure, code, message):
    settings = Settings(database_path=str(tmp_path / 'tracker.db'), api_key='secret-value',
                        base_url='https://provider.example/v1', model='test-model', max_attempts=2)
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if isinstance(failure, int):
            return httpx.Response(failure, json={'error': {'param': 'model', 'message': 'secret-value'}})
        raise failure('secret-value', request=request)

    expected_error = {'code': code, 'message': message}
    completions = ('POST', '/v1/chat/completions')
    with httpx.Client(transport=httpx.MockTransport(respond)) as model_client:
        monkeypatch.setattr(httpx, 'get', model_client.get)
        monkeypatch.setattr(httpx, 'post', model_client.post)
        with TestClient(create_app(settings)) as client:
            models = client.post('/api/settings/model/models', json={'base_url': settings.base_url})
            check = client.post('/api/settings/model/check', json={})
            for response in (models, check):
                assert response.status_code == 502
                assert response.json()['error'] == {**expected_error, 'details': {}, 'run_id': None}
            assert calls == [('GET', '/v1/models'), completions]

            identity = topic(client)
            queued = client.post(f'/api/topics/{identity}/runs', json={
                'provider': 'qwen', 'fixture_batch_id': '1',
            })
            assert queued.status_code == 202, queued.text
            result = _wait_run(client, queued.json()['run_id'])
            assert calls == [('GET', '/v1/models')] + [completions] * 5
            assert result['run']['status'] == 'failed'
            assert result['run']['counts']['errors'] == 2
            assert result['run']['counts']['unprocessed'] == 0
            assert result['run']['error']['details'] == [expected_error] * 2
            assessment_steps = [step for step in result['steps'] if step['step_name'] == 'assess_evidence']
            assert [step['attempt'] for step in assessment_steps] == [2, 2]
            assert [step['error'] for step in assessment_steps] == [expected_error] * 2
            assert [row['status'] for row in result['evidence']] == ['failed_retryable'] * 2
            evidence = client.get(f'/api/topics/{identity}/evidence').json()
            assert [row['status'] for row in evidence] == ['failed_retryable'] * 2


def test_assessment_recovers_after_rate_limit():
    settings = Settings(base_url='https://provider.example', max_attempts=2)
    reply = Assessment(evidence_id='ev', is_relevant=False, novelty='no_change', reason_codes=['irrelevant'],
                       association=Association(type='unresolved', reason='Off topic'))
    replies = iter([
        httpx.Response(429),
        httpx.Response(200, json={'choices': [{'message': {'content': reply.model_dump_json()}}]}),
    ])
    with httpx.Client(transport=httpx.MockTransport(lambda request: next(replies))) as client:
        provider = QwenProvider(settings, client)
        assessment = provider.assess({}, [], {'evidence_id': 'ev'}, [])
    assert provider.attempt == 2
    assert assessment.is_relevant is False


def test_deepseek_service_label_is_rejected_before_save(tmp_path):
    settings=Settings(env_path=tmp_path/'.env',api_key='test',base_url='',model='')
    with pytest.raises(AppError) as caught:
        save_model_settings(settings,ModelSettingsInput(base_url='https://api.deepseek.com',model='Deepseek'))
    assert caught.value.code=='INVALID_MODEL_CONFIG'
    assert not settings.env_path.exists()


@pytest.mark.parametrize('address', ['http://localhost:11434/v1', 'http://127.0.0.1:11434/v1'])
def test_local_ollama_loopback_is_allowed(address):
    assert validate_address(address) == address
    settings = Settings(api_key='ollama', base_url=address, model='qwen3:4b')
    assert isinstance(get_provider('qwen', settings), QwenProvider)


@pytest.mark.parametrize('address', ['http://example.com/v1', 'http://localhost.example.com/v1',
                                      'http://user:pass@localhost:11434/v1'])
def test_nonlocal_or_credentialed_http_model_url_is_rejected(address):
    with pytest.raises(AppError) as caught:
        validate_address(address)
    assert caught.value.code == 'INVALID_MODEL_CONFIG'


@pytest.mark.parametrize('novelty', ['confirmation', 'no_change'])
@pytest.mark.parametrize('extra', [
    {'supported_claims': [{'fact': 'New patch is available', 'evidence_ids': ['ev']}]},
    {'update_kind': 'fact_added'},
    {'proposed_state': {'current_status': 'patched', 'key_facts': [], 'timeline': [], 'sources': ['ev']}},
])
def test_no_change_cannot_silently_discard_update(novelty, extra):
    assessment = Assessment(evidence_id='ev', is_relevant=True,
                            association=Association(type='new_event', title='Example', reason='Test'),
                            novelty=novelty, reason_codes=['test'], **extra)
    evidence = {'evidence_id': 'ev'}
    with pytest.raises(ValidationError):
        MODEL_DECISION.validate_python(assessment.model_dump(exclude={'evidence_id', 'is_relevant'}))
    with pytest.raises(AppError) as caught:
        StateManager(None).prepare_decision({'topic_id': 'topic'}, 'run', evidence, assessment, {}, {})
    assert caught.value.code == 'INVALID_PROPOSAL'


def test_clean_confirmation_still_records_supporting_evidence():
    assessment = Assessment(evidence_id='ev', is_relevant=True,
                            association=Association(type='existing_event', event_id='event', reason='Same fact'),
                            novelty='confirmation', reason_codes=['corroboration'])
    MODEL_DECISION.validate_python(assessment.model_dump(exclude={'evidence_id', 'is_relevant'}))
    result = StateManager(None).prepare_decision(
        {'topic_id': 'topic'}, 'run', {'evidence_id': 'ev'}, assessment,
        {'event': {'event_id': 'event'}}, {},
    )
    assert result == {'outcome': 'no_change', 'event_id': 'event', 'relation': 'supporting'}


def test_invalid_references_get_targeted_repair_instead_of_weakened_validation():
    settings=Settings(api_key='test',base_url='https://provider.example/v1',model='test-model')
    fact={'fact':'Reported agreement','evidence_ids':['ev']}
    good=dict(evidence_id='ev',is_relevant=True,association={'type':'new_event','title':'Transfer','reason':'Initial report'},novelty='new_fact',
              reason_codes=['report'],update_kind='event_created',supported_claims=[fact],
              proposed_state={'current_status':'Reported','key_facts':[fact],
                  'timeline':[{'occurred_at':'2026-07-01T00:00:00Z','description':'Report','evidence_ids':['ev']}],'sources':['ev']})
    bad={**good,'supported_claims':[{'fact':'Reported agreement','evidence_ids':['invented-id']}]}
    calls=[]
    def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        if len(calls)==2:
            assert 'invented IDs' in payload['messages'][-1]['content']
            assert payload['messages'][-2]['role']=='assistant'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(bad if len(calls)==1 else good)}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider=QwenProvider(settings,client)
        result=provider.assess({},[],{'evidence_id':'ev','published_at':'2026-07-01T00:00:00Z','title':'Report'},[])
    assert provider.attempt==2 and len(calls)==2
    assert result.supported_claims==result.proposed_state.key_facts


def test_model_delta_assembly_preserves_history_and_uses_canonical_claims():
    from app.llm import assemble_model_state
    old={'fact':'Original fact','evidence_ids':['old']}
    prior={'event_id':'event','version':1,'state':{'current_status':'pending','key_facts':[old],
        'timeline':[{'occurred_at':'2026-07-01T00:00:00Z','description':'Original','evidence_ids':['old']}],'sources':['old']}}
    a=Assessment.model_validate(dict(evidence_id='new',is_relevant=True,association={'type':'existing_event','event_id':'event','reason':'Same matter'},
        expected_version=1,novelty='new_fact',update_kind='fact_added',reason_codes=['new'],supported_claims=[{'fact':'Canonical new fact','evidence_ids':['new']}],
        proposed_state={'current_status':'pending','key_facts':[{'fact':'Different phrasing','evidence_ids':['new']}],'timeline':[],'sources':['new']}))
    result=assemble_model_state(a,[prior],{'evidence_id':'new','title':'Input headline','published_at':'2026-07-02T00:00:00Z'})
    state=result.proposed_state.model_dump(mode='json')
    assert state['key_facts']==[old,{'fact':'Canonical new fact','evidence_ids':['new']}]
    assert state['timeline'][0]==prior['state']['timeline'][0]
    assert state['timeline'][1]['description']=='Input headline'
    assert state['sources']==['old','new']


@pytest.mark.parametrize('update_kind,expected_status', [
    ('event_created', 'announced'),
    ('fact_added', 'investigating'),
    ('minor_update', 'investigating'),
])
def test_qwen_assembles_updates_with_explicit_state(update_kind, expected_status):
    settings = Settings(base_url='https://provider.example', max_attempts=1)
    old_fact = {'fact': 'The vendor is investigating.', 'evidence_ids': ['old']}
    old_entry = {'occurred_at': '2026-04-01T09:00:00Z', 'description': 'Investigation', 'evidence_ids': ['old']}
    prior = {'event_id': 'event', 'version': 1, 'state': {
        'current_status': 'investigating', 'key_facts': [old_fact], 'timeline': [old_entry], 'sources': ['old'],
    }}
    creating = update_kind == 'event_created'
    evidence = {'evidence_id': 'ev', 'title': 'Vendor update', 'published_at': '2026-04-02T09:00:00Z'}
    fact = {'fact': 'The vendor published an update.', 'evidence_ids': ['ev']}
    association = ({'type': 'new_event', 'title': 'CedarDesk', 'reason': 'First report'} if creating else
                   {'type': 'existing_event', 'event_id': 'event', 'reason': 'Same matter'})
    decision = dict(novelty='new_fact', reason_codes=['report'], association=association,
                    expected_version=None if creating else 1, supported_claims=[fact], update_kind=update_kind,
                    proposed_state=dict(current_status=expected_status, timeline=[], key_facts=[], sources=['ev']))
    reply = dict(is_relevant=True, event_decisions=[decision])
    response = httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply)}}]})

    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        provider = QwenProvider(settings, client)
        result = provider.assess({}, [] if creating else [prior], evidence, [])

    assert result.evidence_id == 'ev'
    state = result.event_decisions[0].proposed_state.model_dump(mode='json')
    assert state['current_status'] == expected_status
    assert state['key_facts'] == ([fact] if creating else [old_fact, fact])
    assert state['sources'] == (['ev'] if creating else ['old', 'ev'])
    entry = {'occurred_at': evidence['published_at'], 'description': evidence['title'], 'evidence_ids': ['ev']}
    assert state['timeline'] == ([entry] if creating else [old_entry, entry])


@pytest.mark.parametrize('novelty,field,value', [
    ('new_fact', 'proposed_state', None),
    ('new_fact', 'proposed_state', {'current_status': '  ', 'timeline': [], 'key_facts': [], 'sources': ['ev']}),
    ('new_fact', 'update_kind', None),
    ('new_fact', 'supported_claims', []),
    ('new_fact', 'supported_claims', [{'fact': '  ', 'evidence_ids': ['ev']}]),
    ('no_change', 'update_kind', 'event_created'),
    ('no_change', 'proposed_state', {'current_status': 'announced', 'timeline': [], 'key_facts': [], 'sources': ['ev']}),
    ('confirmation', 'supported_claims', [{'fact': 'Update published.', 'evidence_ids': ['ev']}]),
])
def test_qwen_requires_fields_based_on_novelty(novelty, field, value):
    settings = Settings(base_url='https://provider.example', max_attempts=1)
    decision = dict(novelty=novelty, reason_codes=['report'],
                    association={'type': 'new_event', 'title': 'New Event', 'reason': 'First report'})
    if novelty == 'new_fact':
        decision.update(update_kind='event_created',
                        supported_claims=[{'fact': 'Update published.', 'evidence_ids': ['ev']}],
                        proposed_state=dict(current_status='announced', timeline=[], key_facts=[], sources=['ev']))
    decision[field] = value
    reply = dict(is_relevant=True, event_decisions=[decision])
    response = httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply)}}]})

    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(AppError) as caught:
            QwenProvider(settings, client).assess({}, [], {'evidence_id': 'ev'}, [])

    assert caught.value.code == 'PROVIDER_SCHEMA'
    assert field in caught.value.message


@pytest.mark.parametrize('novelty,requires_review', [
    ('new_fact', False), ('no_change', False), ('confirmation', True), ('conflict', True),
])
def test_qwen_repairs_unknown_existing_event_for_any_novelty(novelty, requires_review):
    settings = Settings(base_url='https://provider.example', max_attempts=2)
    prior = dict(event_id='existing', version=2, state=dict(current_status='active', key_facts=[], timeline=[], sources=[]))
    evidence = dict(evidence_id='ev', title='Report', published_at='2026-04-02T09:00:00Z')
    decision = dict(novelty=novelty, requires_review=requires_review, reason_codes=['report'],
                    association=dict(type='existing_event', event_id='invented', reason='Same matter'))
    if novelty == 'new_fact':
        decision.update(expected_version=2, update_kind='fact_added',
                        supported_claims=[{'fact': 'The offer started.', 'evidence_ids': ['ev']}],
                        proposed_state=dict(current_status='active', timeline=[], key_facts=[], sources=['ev']))
    calls = []

    def respond(request):
        messages = json.loads(request.content)['messages']
        calls.append(messages)
        if len(calls) == 2:
            assert 'from current_events for every existing_event decision' in messages[-1]['content']
            decision['association']['event_id'] = 'existing'
        reply = dict(is_relevant=True, event_decisions=[decision])
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = QwenProvider(settings, client).assess({}, [prior], evidence, [])

    assert len(calls) == 2
    assert result.event_decisions[0].association.event_id == 'existing'


def test_qwen_explicit_proposed_state_reaches_commit_and_trace(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / 'tracker.db'), api_key='test',
                        base_url='https://provider.example', model='test-model', max_attempts=1)

    def respond(request):
        messages = json.loads(request.content)['messages']
        inputs = json.loads(messages[1]['content'])
        evidence = inputs['evidence']
        reply = dict(evidence_id=evidence['evidence_id'], is_relevant=True, novelty='new_fact',
                     reason_codes=['vendor_report'], supported_claims=[{
                         'fact': evidence['title'], 'evidence_ids': [evidence['evidence_id']],
                     }], proposed_state=dict(current_status='reported', timeline=[], key_facts=[],
                                             sources=[evidence['evidence_id']]))
        if inputs['current_events']:
            event = inputs['current_events'][0]
            reply.update(association={'type': 'existing_event', 'event_id': event['event_id'], 'reason': 'Same matter'},
                         expected_version=event['version'], update_kind='fact_added')
        else:
            reply.update(association={'type': 'new_event', 'title': evidence['title'], 'reason': 'Initial report'},
                         update_kind='event_created')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reply)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as model_client:
        monkeypatch.setattr('app.orchestration.get_provider', lambda name, config: QwenProvider(config, model_client))
        with TestClient(create_app(settings)) as client:
            root = f'/api/topics/{topic(client)}'
            for batch, version in [('1', 1), ('2', 2)]:
                queued = client.post(f'{root}/runs', json={
                    'provider': 'qwen', 'fixture_batch_id': batch, 'source_ids': ['vendor'],
                })
                assert queued.status_code == 202, queued.text
                detail = _wait_run(client, queued.json()['run_id'])
                assert detail['run']['status'] == 'completed', detail['run']['error']
                assert detail['run']['counts']['committed'] == 1
                event, = client.get(f'{root}/dashboard').json()['events']
                assessed, = [step['decision']['assessment'] for step in detail['steps'] if step['step_name'] == 'assess_evidence']
                assert event['version'] == version
                assert event['state']['current_status'] == 'reported'
                assert len(event['state']['key_facts']) == len(event['state']['timeline']) == version
                assert assessed['proposed_state'] == event['state']
                row = next(row for row in client.get(f'{root}/evidence').json()
                           if row['evidence']['evidence_id'] == assessed['evidence_id'])
                assert row['assessment'] == assessed
                assert row['status'] == 'completed'


@pytest.mark.parametrize('host,expected',[('api.deepseek.com',True),('provider.example',False)])
def test_deepseek_uses_non_thinking_structured_output(host,expected):
    settings=Settings(api_key='test',base_url='https://'+host,model='actual-api-id')
    reply=Assessment(evidence_id='ev',is_relevant=False,association=Association(type='unresolved',reason='Off topic'),novelty='no_change',reason_codes=['irrelevant'])
    def handler(request):
        payload=json.loads(request.content)
        assert ('reasoning_effort' in payload)==expected
        if expected:assert payload['reasoning_effort']=='none' and payload['max_tokens']==16384
        return httpx.Response(200,json={'choices':[{'message':{'content':reply.model_dump_json()}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert QwenProvider(settings,client).assess({},[],{'evidence_id':'ev'},[]).is_relevant is False


def test_fatal_model_configuration_error_stops_batch(tmp_path,monkeypatch):
    settings=Settings(database_path=str(tmp_path/'tracker.db'),api_key='',base_url='',model='')
    calls=[]
    def fail(*args):
        calls.append(1)
        raise AppError('PROVIDER_MODEL','Invalid model ID',502)
    monkeypatch.setattr(MockProvider,'assess',fail)
    with TestClient(create_app(settings)) as c:
        identity=topic(c)
        result=run(c,identity,'1')
        assert result['run']['status']=='failed'
        assert result['run']['counts']['errors']==1
        assert result['run']['counts']['unprocessed']==1
        assert len(calls)==1
        assert c.app.state.repo.events(identity)==[]


def test_replay_source_selection_excludes_fixtures_and_unapproved_sources(tmp_path):
    settings=Settings(database_path=str(tmp_path/'tracker.db'),api_key='',base_url='',model='')
    with TestClient(create_app(settings)) as c:
        identity=topic(c)
        source=dict(source_id='test_replay',name='NBA replay',type='replay',endpoint='replay://nba_2026_multi_event/all.json',config={'corpus_path':'nba_2026_multi_event/all.json'})
        assert c.post('/api/sources',json=source).status_code==201
        c.put(f'/api/topics/{identity}/sources',json={'approved_source_ids':['vendor','research','test_replay']})
        body={'provider':'mock','fixture_batch_id':'all','source_ids':['test_replay']}
        response=c.post(f'/api/topics/{identity}/runs',json=body)
        assert response.status_code==202,response.text
        result=_wait_run(c,response.json()['run_id'])
        assert result['run']['status']=='completed'
        assert result['run']['counts']['fetched']==18
        assert [s['source_id'] for s in result['run']['snapshot']['sources']]==['test_replay']
        assert not [s for s in result['steps'] if s.get('error')]
        assert c.post(f'/api/topics/{identity}/runs',json={**body,'source_ids':['unknown']}).status_code==422
        assert c.post(f'/api/topics/{identity}/runs',json={**body,'source_ids':[]}).status_code==422
