import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app.core import Settings,AppError
from app.domain import ModelDiscoveryInput,ModelSettingsInput,Assessment,Association
from app.llm import QwenProvider,MockProvider,get_provider,provider_error
from app.model_settings import discover_models,save_model_settings,validate_address
from app.main import create_app
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
