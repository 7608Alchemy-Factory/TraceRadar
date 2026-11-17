import json
import pytest
from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
from app.domain import Run,RunStep
from test_workflow import topic,run,business_counts


@pytest.fixture
def client(tmp_path):
    settings=Settings(database_path=str(tmp_path/'test.db'),env_path=tmp_path/'.env',api_key='',base_url='',model='')
    with TestClient(create_app(settings),base_url='http://127.0.0.1',raise_server_exceptions=False) as c:
        yield c,settings


def pending_conflict(c):
    identity=topic(c)
    for batch in ('1','2','3'):run(c,identity,batch)
    d=c.get(f'/api/topics/{identity}/dashboard').json()
    review=next(r for r in d['reviews'] if r['assessment']['novelty']=='conflict')
    event=next(e for e in d['events'] if e['event_id']==review['assessment']['association']['event_id'])
    return identity,review,event


def accept_body(event):
    return dict(decision='accept',note='Human checked the competing report; retain audit history.',
        event_id=event['event_id'],expected_version=event['version'],current_status='patched; affected-version scope corrected',
        fact='The reviewed report states version 1.1 is not affected.',superseded_facts=[event['state']['key_facts'][1]],update_kind='fact_added')


def test_human_correction_is_atomic_audited_and_idempotent(client):
    c,_=client
    identity,review,event=pending_conflict(c)
    path=f"/api/topics/{identity}/reviews/{review['evidence']['evidence_id']}/resolve"
    body=accept_body(event)
    response=c.post(path,json=body)
    assert response.status_code==200,response.text
    resolution=response.json()
    detail=c.get(f"/api/events/{event['event_id']}").json()
    after=detail['event']
    assert after['version']==event['version']+1
    assert body['superseded_facts'][0] not in after['state']['key_facts']
    assert body['superseded_facts'][0] in detail['history'][-1]['before_state']['key_facts']
    assert detail['history'][-1]['after_state']==after['state']
    record=c.get('/api/runs/'+resolution['run_id']).json()
    Run.model_validate(record['run']);RunStep.model_validate(record['steps'][0])
    assert record['run']['provider']=='human' and record['run']['trigger_type']=='human_review'
    assert record['steps'][0]['decision']['note']==body['note']
    assert resolution['original_assessment']==review['assessment']
    baseline=business_counts(c,identity)
    assert c.post(path,json=body).json()['resolution_id']==resolution['resolution_id']
    run(c,identity,'3')
    assert business_counts(c,identity)==baseline
    assert c.post(path,json={**body,'fact':'Different claim'}).status_code==409


@pytest.mark.parametrize('failure',['version','scope','fact','active','transaction'])
def test_invalid_or_interrupted_review_cannot_partially_commit(client,failure):
    c,_=client
    identity,review,event=pending_conflict(c)
    body=accept_body(event)
    baseline=business_counts(c,identity)
    path=f"/api/topics/{identity}/reviews/{review['evidence']['evidence_id']}/resolve"
    if failure=='version':body['expected_version']=999
    if failure=='scope':
        other=topic(c);run(c,other,'1')
        body['event_id']=c.get(f'/api/topics/{other}/dashboard').json()['events'][0]['event_id']
    if failure=='fact':body['superseded_facts'][0]['fact']='Not a real prior fact'
    if failure=='active':c.app.state.repo.create_run(identity,{'fixture_batch_id':'empty','provider':'mock'},None,None)
    if failure=='transaction':
        def fault():raise RuntimeError('Injected commit interruption')
        c.app.state.manager.state_manager.commit_fault=fault
    response=c.post(path,json=body)
    assert response.status_code in (409,422,500)
    assert business_counts(c,identity)==baseline
    assert c.app.state.repo.review_item(identity,review['evidence']['evidence_id'])['status']=='review'
    assert c.app.state.repo.review_resolutions(identity)==[]
    assert not any(r['provider']=='human' for r in c.app.state.repo.runs(identity))


def test_model_settings_stay_local_and_never_return_key(client):
    c,settings=client
    body={'base_url':'https://provider.example/v1','model':'team-model','api_key':'private-test-key'}
    assert c.get('/api/settings/model').json()['configured'] is False
    result=c.put('/api/settings/model',json=body)
    assert result.status_code==200,result.text
    assert 'private-test-key' not in result.text
    assert result.json()['key_configured'] and result.json()['configured']
    assert 'private-test-key' in settings.env_path.read_text()
    assert settings.api_key=='private-test-key'
    assert 'private-test-key' not in c.get('/api/settings/model').text
    assert c.put('/api/settings/model',json={**body,'api_key':None,'model':'team-model-v2'}).status_code==200
    assert settings.api_key=='private-test-key'
    before=settings.env_path.read_text()
    assert c.put('/api/settings/model',json={**body,'base_url':'https://user:password@provider.example/v1'}).status_code==422
    assert settings.env_path.read_text()==before
    identity=topic(c)
    c.app.state.repo.create_run(identity,{'fixture_batch_id':'empty','provider':'mock'},None,None)
    assert c.put('/api/settings/model',json=body).status_code==409


def test_unconfigured_connection_check_and_replay_metadata(client):
    c,_=client
    assert c.post('/api/settings/model/check').json()['error']['code']=='QWEN_NOT_CONFIGURED'
    corpora=c.get('/api/replay/corpora').json()
    assert [(r['corpus_id'],r['records']) for r in corpora]==[('lbj',30),('nba',18)]
    assert all(len(r['cutoffs'])==3 and r['cutoffs']==sorted(r['cutoffs']) for r in corpora)
    assert 'labels' not in json.dumps(corpora)


def test_connection_check_uses_one_request_and_settings_reject_foreign_origin(client,monkeypatch):
    from app.llm import QwenProvider
    from app.domain import Assessment,Association
    c,settings=client
    settings.api_key='test';settings.model='test-model';settings.base_url='https://provider.example/v1'
    calls=[]
    def assess(self,topic,events,evidence,sources):
        calls.append(self.settings.max_attempts)
        self.attempt=1
        return Assessment(evidence_id=evidence['evidence_id'],is_relevant=False,
            association=Association(type='unresolved',reason='Connection check'),novelty='no_change',reason_codes=['check'])
    monkeypatch.setattr(QwenProvider,'assess',assess)
    assert c.post('/api/settings/model/check',headers={'Origin':'https://external.example'}).status_code==403
    assert calls==[]
    result=c.post('/api/settings/model/check',headers={'Origin':'http://localhost:5173'})
    assert result.status_code==200 and result.json()['model']=='test-model'
    assert calls==[1]


def test_evidence_library_includes_excluded_and_duplicate_items(client):
    c,_=client
    identity=topic(c)
    run(c,identity,'1');run(c,identity,'2')
    items=c.get(f'/api/topics/{identity}/evidence').json()
    assert len(items)==5
    assert any(r['assessment'] and not r['assessment']['is_relevant'] for r in items)
    assert any(r['evidence']['canonical_evidence_id']!=r['evidence']['evidence_id'] for r in items)
    assert c.get('/api/topics/missing/evidence').status_code==404


def test_pilot_export_separates_outputs_and_preserves_human_labels(client,tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('export_pilot',Path(__file__).resolve().parents[2]/'scripts/export_pilot.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    c,_=client
    identity=topic(c);run(c,identity,'1')
    target=tmp_path/'pilot'
    summary=module.export_case(c,identity,target)
    assert summary['evidence']==2
    template=target/'annotations.jsonl'
    rows=[json.loads(line) for line in template.read_text(encoding='utf-8').splitlines()]
    assert all(r['labels']['is_relevant'] is None and r['labels']['event_key'] is None for r in rows)
    assert all('assessment' not in r for r in rows)
    rows[0]['labels']['note']='Human annotation must survive export'
    annotated='\n'.join(json.dumps(r) for r in rows)
    template.write_text(annotated,encoding='utf-8')
    module.export_case(c,identity,target)
    assert template.read_text(encoding='utf-8')==annotated
    outputs=json.loads((target/'agent-output.json').read_text(encoding='utf-8'))
    assert outputs['runs'][0]['steps'] and outputs['events'][0]['history']
