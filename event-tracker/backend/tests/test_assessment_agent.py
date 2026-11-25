import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import Settings
from app.llm import QwenProvider
from app.main import create_app
from test_connectors_and_pipeline import _wait_run
from test_workflow import topic


@pytest.mark.parametrize('cross_topic', [False, True])
def test_agent_assessment_replay(tmp_path, monkeypatch, cross_topic):
    dates = ['2026-06-20T12:00:00Z', '2026-06-21T12:00:00Z']
    titles = ['A new city rail line opens', 'Saturday service will run every 15 minutes']
    (tmp_path / 'rail.json').write_text(json.dumps([
        dict(external_id=f'rail-{i}', url=f'https://news.example/rail/{i}', title=title,
             content=title, published_at=stamp)
        for i, (title, stamp) in enumerate(zip(titles, dates))
    ]), encoding='utf-8')
    settings = Settings(database_path=str(tmp_path / 'tracker.db'), corpus_root=tmp_path,
                        api_key='test', base_url='https://provider.example', model='test-model', max_attempts=2)
    foreign_id = None
    agent_requests = []

    def respond(request):
        messages = json.loads(request.content)['messages']
        inputs = json.loads(messages[1]['content'])
        evidence = inputs['evidence']
        events = inputs['current_events']
        agent_prompt = '"name":"load_skill"' in messages[0]['content']
        if agent_prompt:
            agent_requests.append(request)
            assert events and 'Only the incoming evidence can support a new claim' not in messages[0]['content']
            results = [json.loads(message['content'])['tool_result'] for message in messages[2:]
                       if message['role'] == 'user' and message['content'].startswith('{"tool_result":')]
            if not results:
                output = {'tool_call': {'name': 'load_skill', 'argument': 'evidence-assessment'}}
            elif len(results) == 1 or cross_topic:
                assert 'incoming evidence' in results[0]['guidance']
                output = {'tool_call': {'name': 'get_event_evidence', 'argument': foreign_id or events[0]['event_id']}}
                if len(results) > 1:
                    assert results[1] == {'name': 'get_event_evidence', 'error': 'Event is not available in the current Topic.'}
            else:
                linked, = results[1]['evidence']
                assert linked['title'] == titles[0]
                assert linked['evidence_id'] in events[0]['state']['sources']
                output = None
        else:
            assert len(messages) == 2
            output = None

        if output is None:
            evidence_id = evidence['evidence_id']
            event = events[0] if events else None
            association = (dict(type='existing_event', event_id=event['event_id'], reason='Same rail line') if event
                           else dict(type='new_event', title='New city rail line', reason='Opening report'))
            output = dict(is_relevant=True, association=association, novelty='new_fact',
                          reason_codes=['rail_service'], update_kind='fact_added' if event else 'event_created',
                          expected_version=event['version'] if event else None,
                          supported_claims=[dict(fact=evidence['title'], evidence_ids=[evidence_id])],
                          proposed_state=dict(current_status='Opened', key_facts=[], timeline=[], sources=[evidence_id]))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as model_client, TestClient(create_app(settings)) as client:
        monkeypatch.setattr('app.orchestration.get_provider', lambda name, config: QwenProvider(config, model_client))
        source = dict(source_id='rail_replay', name='Rail news', type='replay', endpoint='replay://rail.json', config={})
        assert client.post('/api/sources', json=source).status_code == 201

        def prepare_topic():
            identity = topic(client, approve=False)
            assert client.put(f'/api/topics/{identity}/sources', json={'approved_source_ids': ['rail_replay']}).status_code == 200
            return identity

        def replay(identity, cutoff, **options):
            response = client.post(f'/api/topics/{identity}/runs', json={
                'provider': 'qwen', 'fixture_batch_id': 'all', 'as_of': cutoff, **options,
            })
            assert response.status_code == 202, response.text
            return _wait_run(client, response.json()['run_id'])

        identity = prepare_topic()
        before = replay(identity, dates[0])
        assert before['run']['status'] == 'completed'
        assert 'agent_mode' not in before['run']['snapshot']['options']
        assert all('agent' not in step['decision'] for step in before['steps'])
        if cross_topic:
            other = prepare_topic()
            assert replay(other, dates[0])['run']['status'] == 'completed'
            foreign_id = client.app.state.repo.events(other)[0]['event_id']
            assert client.app.state.repo.event_evidence_as_of(identity, foreign_id, dates[1]) == []

        rejected = client.post(f'/api/topics/{identity}/runs', json={'provider': 'mock', 'agent_mode': True})
        assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'AGENT_REQUIRES_MODEL'
        after = replay(identity, dates[1], agent_mode=True)
        assert after['run']['snapshot']['options']['agent_mode'] is True
        assessed, = [s for s in after['steps'] if s['step_name'] == 'assess_evidence']
        trace = assessed['decision']['agent']
        assert [call['name'] for call in trace['calls']] == ['load_skill', 'get_event_evidence']
        assert trace['model_calls'] == len(agent_requests) == (4 if cross_topic else 3)
        event, = client.app.state.repo.events(identity)
        if cross_topic:
            assert assessed['error']['code'] == 'PROVIDER_SCHEMA'
            assert trace['calls'][1] == {'name': 'get_event_evidence', 'status': 'denied'}
            assert event['version'] == 1

            cross_topic = False
            foreign_id = None
            resumed = client.post(f"/api/runs/{after['run']['run_id']}/resume")
            assert resumed.status_code == 202
            after = _wait_run(client, resumed.json()['run_id'])
            assert after['run']['snapshot']['options']['agent_mode'] is True
            assessed, = [s for s in after['steps'] if s['step_name'] == 'assess_evidence']
            event, = client.app.state.repo.events(identity)

        assert after['run']['status'] == 'completed'
        assert assessed['decision']['agent']['calls'][1]['evidence_count'] == 1
        assert event['version'] == 2
        assert assessed['decision']['assessment']['proposed_state'] == event['state']
        assert len(client.app.state.repo.event_evidence_as_of(identity, event['event_id'], dates[0])) == 1


def test_agent_reads_compressed_event_history(tmp_path, monkeypatch):
    dates = [f'2026-06-{day}T12:00:00Z' for day in range(20, 26)]
    statuses = ['Proposed', 'Under review', 'Under review', 'Approved', 'Opened']
    (tmp_path / 'history.json').write_text(json.dumps([
        dict(external_id=f'update-{index}', url=f'https://news.example/rail/{index}',
             title=f'Rail project report {index}', content=f'Rail project report {index}', published_at=date)
        for index, date in enumerate(dates)
    ]), encoding='utf-8')
    settings = Settings(database_path=str(tmp_path / 'tracker.db'), corpus_root=tmp_path,
                        api_key='test', base_url='https://provider.example', model='test-model')

    def respond(request):
        messages = json.loads(request.content)['messages']
        inputs = json.loads(messages[1]['content'])
        index = int(inputs['evidence']['title'].split()[-1])
        event = next(iter(inputs['current_events']), None)
        evidence_id = inputs['evidence']['evidence_id']
        if index < len(statuses):
            output = dict(is_relevant=True,
                          association=dict(type='existing_event', event_id=event['event_id'], reason='Same rail project')
                          if event else dict(type='new_event', title='Rail project', reason='First report'),
                          novelty='new_fact', reason_codes=['rail_project'], expected_version=event['version'] if event else None,
                          update_kind='fact_added' if event else 'event_created',
                          supported_claims=[dict(fact=inputs['evidence']['title'], evidence_ids=[evidence_id])],
                          proposed_state=dict(current_status=statuses[index], key_facts=[], timeline=[], sources=[evidence_id]))
        elif len(messages) == 2:
            assert '"name":"get_event_history"' in messages[0]['content']
            output = {'tool_call': {'name': 'get_event_history', 'argument': event['event_id']}}
        else:
            result = json.loads(messages[-1]['content'])['tool_result']
            assert result['name'] == 'get_event_history' and result['record_count'] == 5
            assert [change['after_status'] for change in result['changes']] == ['Under review', 'Approved', 'Opened']
            assert result['changes'][-1]['evidence_ids'] == [event['state']['sources'][-1]]
            output = {'is_relevant': False, 'event_decisions': []}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as model_client, TestClient(create_app(settings)) as client:
        monkeypatch.setattr('app.orchestration.get_provider', lambda name, config: QwenProvider(config, model_client))
        source = dict(source_id='rail_history', name='Rail history', type='replay', endpoint='replay://history.json', config={})
        assert client.post('/api/sources', json=source).status_code == 201
        identity = topic(client, approve=False)
        assert client.put(f'/api/topics/{identity}/sources', json={'approved_source_ids': ['rail_history']}).status_code == 200

        def replay(cutoff, agent_mode):
            response = client.post(f'/api/topics/{identity}/runs', json={
                'provider': 'qwen', 'fixture_batch_id': 'all', 'as_of': cutoff,
                'agent_mode': agent_mode, 'near_duplicate_enabled': False,
            })
            assert response.status_code == 202, response.text
            detail = _wait_run(client, response.json()['run_id'])
            assert detail['run']['status'] == 'completed', detail['run']
            return detail

        replay(dates[4], False)
        after = replay(dates[5], True)
        assessed, = [step for step in after['steps'] if step['step_name'] == 'assess_evidence']
        tool, = assessed['decision']['agent']['calls']
        assert tool['name'] == 'get_event_history' and tool['status'] == 'completed'
        assert tool['result']['record_count'] == 5 and len(tool['result']['changes']) == 3

        repo = client.app.state.repo
        event_id = tool['event_id']
        assert len(repo.event_detail(event_id)['history']) == 5
        earlier = repo.get_event_history(identity, event_id, dates[2])
        assert earlier['record_count'] == 3
        assert [change['after_status'] for change in earlier['changes']] == ['Proposed', 'Under review']
        matches = repo.get_event_history(identity, event_id, dates[-1], ' proposed ')['changes']
        assert [change['after_status'] for change in matches] == ['Proposed', 'Under review']
        assert repo.get_event_history('another-topic', event_id, dates[-1])['changes'] == []
