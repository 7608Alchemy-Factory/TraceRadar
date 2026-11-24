"""Contract tests with scripted model outputs; these do not measure model accuracy."""
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app.core import Settings
from app.domain import Assessment, Association, EventState, Fact, TimelineItem
from app.llm import QwenProvider, PROMPT_VERSION
from app.main import create_app
from app.notification_policy import decide_notification
from test_workflow import run, topic, business_counts


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(database_path=str(tmp_path / 'd.db'), provider='mock', api_key='', base_url='', model=''))) as c:
        yield c


@pytest.mark.parametrize('subject,start,finish', [
    ('Player transfer', 'Negotiations reported', 'Contract officially signed'),
    ('Acme acquisition', 'Regulatory review pending', 'Regulator approved acquisition'),
])
@pytest.mark.parametrize('preference,expected_action', [('standard','immediate'),('digest_only','digest')])
def test_domain_independent_lifecycle(client, monkeypatch, subject, start, finish, preference, expected_action):
    identity = client.post('/api/topics',json={'title':subject,'description':'Track confirmed progress and conflicts',
                                             'notification_preferences':preference}).json()['topic_id']
    client.post('/api/sources',json={'source_id':'manual_news','name':'Manual news','type':'manual','endpoint':'manual://news'})
    client.put(f'/api/topics/{identity}/sources',json={'approved_source_ids':['manual_news']})

    def scripted(self, topic, events, evidence, sources):
        existing = events[0] if events else None
        association = Association(type='existing_event',event_id=existing['event_id'],reason='Same independently evolving matter') if existing else Association(type='new_event',title=subject,reason='Initial report')
        a = Assessment(evidence_id=evidence['evidence_id'],is_relevant=True,association=association,
                       novelty='new_fact',reason_codes=['scripted_contract_test'],expected_version=existing['version'] if existing else None)
        if evidence['title']=='Conflicting report':
            a.novelty='conflict';a.requires_review=True;a.conflicts=['Contradicts signed/approved outcome']
            return a
        if evidence['title']=='Confirmation':
            a.novelty='confirmation'
            return a
        fact = Fact(fact=evidence['content'],evidence_ids=[evidence['evidence_id']])
        prior = EventState.model_validate(existing['state']) if existing else None
        a.proposed_state=EventState(current_status=finish if existing else start,
            key_facts=[*(prior.key_facts if prior else []),fact],
            timeline=[*(prior.timeline if prior else []),TimelineItem(occurred_at=evidence['published_at'],description=evidence['title'],evidence_ids=[evidence['evidence_id']])],
            sources=[*(prior.sources if prior else []),evidence['evidence_id']])
        a.supported_claims=[fact]
        a.update_kind='major_milestone' if existing else 'event_created'
        return a

    from app.llm import MockProvider
    monkeypatch.setattr(MockProvider,'assess',scripted)
    def feed(label,content,day):
        # near-duplicate classification is C's separate responsibility in these D tests.
        response=client.post(f'/api/topics/{identity}/runs',json={'fixture_batch_id':'empty','provider':'mock','near_duplicate_enabled':False,
            'manual_items':[{'title':label,'content':content,'published_at':f'2026-07-{day:02d}T10:00:00Z','source_id':'manual_news'}]})
        from test_connectors_and_pipeline import _wait_run
        detail=_wait_run(client,response.json()['run_id'])
        assert detail['run']['status']=='completed',detail
        return detail
    feed('Initial report',start,1)
    feed('Official outcome',finish,2)
    before=business_counts(client,identity)
    feed('Confirmation','Independent confirmation without new substantive details',3)
    feed('Conflicting report','A competing report denies the previously stated outcome',4)
    feed('Official outcome',finish,2)  # Repeat must not alert again.
    assert business_counts(client,identity)==before
    d=client.get(f'/api/topics/{identity}/dashboard').json()
    assert len(d['events'])==1 and len(d['reviews'])==1
    event=d['events'][0]
    assert event['state']['current_status']==finish and event['version']==2
    detail=client.get(f"/api/events/{event['event_id']}").json()
    assert len(detail['history'])==2
    assert len(event['state']['key_facts'])==2
    assert len(detail['evidence'])==4  # supporting + confirmation + conflict
    notices=client.get(f'/api/topics/{identity}/notifications').json()
    milestone=next(n for n in notices if 'major_milestone' in n['reason_codes'])
    assert milestone['action']==expected_action
    assert milestone['transition_id'] in {h['transition_id'] for h in detail['history']}


def test_review_closure_audit_replay_and_topic_scope(client):
    identity=topic(client)
    for batch in ('1','2','3'): run(client,identity,batch)
    d=client.get(f'/api/topics/{identity}/dashboard').json()
    evidence_id=d['reviews'][0]['evidence']['evidence_id']
    path=f'/api/topics/{identity}/reviews/{evidence_id}/resolve'
    baseline=business_counts(client,identity)
    assert client.post(path,json={'decision':'dismiss','note':'   '}).status_code==422
    other=topic(client)
    assert client.post(f'/api/topics/{other}/reviews/{evidence_id}/resolve',json={'decision':'dismiss','note':'Reviewed'}).status_code==404
    body={'decision':'dismiss','note':'Conflicting report retained; official evidence still stands.'}
    result=client.post(path,json=body)
    assert result.status_code==200
    assert client.post(path,json=body).json()['resolution_id']==result.json()['resolution_id']
    assert client.post(path,json={**body,'note':'Different reason'}).status_code==409
    assert business_counts(client,identity)==baseline
    run(client,identity,'3')
    d=client.get(f'/api/topics/{identity}/dashboard').json()
    assert len(d['reviews'])==1 and len(d['review_resolutions'])==1
    assert d['review_resolutions'][0]['original_assessment']==result.json()['original_assessment']
    # Reinitialization/migration preserves the audit record.
    client.app.state.repo.initialize([])
    assert len(client.app.state.repo.review_resolutions(identity))==1


def test_goal_edit_and_review_lock_preserve_run_snapshot(client):
    identity=topic(client)
    for batch in ('1','2','3'): run(client,identity,batch)
    evidence_id=client.get(f'/api/topics/{identity}/dashboard').json()['reviews'][0]['evidence']['evidence_id']
    repo=client.app.state.repo
    pending,_=repo.create_run(identity,{'fixture_batch_id':'empty','provider':'mock'},None,None)
    assert client.patch(f'/api/topics/{identity}',json={'description':'A new tracking goal'}).status_code==409
    assert client.post(f'/api/topics/{identity}/reviews/{evidence_id}/resolve',json={'decision':'dismiss','note':'Reviewed'}).status_code==409
    client.app.state.manager.submit(pending['run_id']);client.app.state.manager.queue.join()
    assert client.patch(f'/api/topics/{identity}',json={'title':'Updated title','description':'A new tracking goal'}).status_code==200
    assert repo.get('runs',pending['run_id'])['snapshot']['topic']['title']=='ExampleSoft security'


def test_keep_state_can_close_without_note(client):
    identity = topic(client)
    for batch in ('1', '2', '3'):
        run(client, identity, batch)
    dashboard = f'/api/topics/{identity}/dashboard'
    before = client.get(dashboard).json()
    review_id = before['reviews'][0]['evidence']['evidence_id']
    path = f'/api/topics/{identity}/reviews/{review_id}/resolve'
    result = client.post(path, json={'decision': 'dismiss'})
    assert result.status_code == 200
    assert result.json()['note'] is None
    assert client.post(path, json={'decision': 'dismiss'}).json() == result.json()
    assert client.get(dashboard).json()['events'] == before['events']


def test_generic_prompt_and_wire_contract():
    settings=Settings(api_key='test',base_url='https://provider.invalid/v1',model='test')
    reply=Assessment(evidence_id='current-evidence',is_relevant=False,association=Association(type='unresolved',reason='Off topic'),novelty='no_change',reason_codes=['irrelevant'])
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':reply.model_dump_json()}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        provider=QwenProvider(settings,transport)
        assert provider.assess({'title':'NBA transfers'},[],{'evidence_id':'current-evidence'},[]).is_relevant is False
    prompt=captured[0]['messages'][0]['content']
    assert 'ExampleSoft' not in prompt
    assert 'untrusted data' in prompt and 'Conflicting evidence' in prompt
    assert captured[0]['response_format']=={'type':'json_object'}
    assert 'event_decisions' in prompt
    assert 'impact_reason' in prompt
    assert 'proposed_title' in prompt
    assert 'exclusions' in prompt
    assert PROMPT_VERSION=='assessment-v9'


@pytest.mark.parametrize('corrupt,code', [('claims','UNGROUNDED_UPDATE'),('timeline','INVALID_TIMELINE'),
                                        ('overwrite','SILENT_OVERWRITE'),('status','INVALID_MILESTONE')])
def test_unsupported_updates_preserve_confirmed_state(client,monkeypatch,corrupt,code):
    from app.llm import MockProvider
    identity=topic(client)
    run(client,identity,'1')
    original=MockProvider.assess
    before=client.get(f'/api/topics/{identity}/dashboard').json()['events'][0]
    def invalid(self,topic,events,evidence,sources):
        a=original(self,topic,events,evidence,sources)
        if a.update_kind=='major_milestone':
            if corrupt=='claims': a.supported_claims=[]
            if corrupt=='timeline': a.proposed_state.timeline=a.proposed_state.timeline[:-1]
            if corrupt=='overwrite': a.proposed_state.key_facts=a.proposed_state.key_facts[1:]
            if corrupt=='status': a.proposed_state.current_status=before['state']['current_status']
        return a
    monkeypatch.setattr(MockProvider,'assess',invalid)
    result=run(client,identity,'3')
    assert any(s['error'] and s['error']['code']==code for s in result['steps'])
    after=client.get(f'/api/topics/{identity}/dashboard').json()['events'][0]
    assert after==before


@pytest.mark.parametrize('kind,priority,action', [('event_created','routine','digest'),('fact_added','routine','digest'),
                                               ('minor_update','low','record_only'),('major_milestone','breaking','immediate')])
@pytest.mark.parametrize('preference',['standard','digest_only'])
def test_notification_policy(kind,priority,action,preference):
    expected='digest' if preference=='digest_only' and action=='immediate' else action
    p,a,reasons=decide_notification(kind,preference)
    assert (p,a)==(priority,expected)
    assert kind in reasons
