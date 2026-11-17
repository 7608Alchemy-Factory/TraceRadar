import hashlib
from datetime import datetime
from .core import AppError
from .domain import StateUpdateProposal, Fact, uid, now
from .notification_policy import decide_notification


class StateManager:
    def __init__(self, repository):
        self.repo = repository
        self.commit_fault = None  # Test-only injection; never exposed through HTTP.

    def resolve_review(self, topic, evidence_id, body):
        if body.decision == 'dismiss':
            return self.repo.resolve_review(topic['topic_id'],evidence_id,body.decision,body.note)
        request=body.model_dump(mode='json')
        prior=self.repo.review_resolution(topic['topic_id'],evidence_id)
        if prior:
            if prior.get('request')!=request:
                raise AppError('REVIEW_ALREADY_RESOLVED','This review already has an audit record.')
            return prior
        review=self.repo.review_item(topic['topic_id'],evidence_id)
        if review['status']!='review':
            raise AppError('NOT_PENDING_REVIEW','Evidence is not awaiting review.')
        existing=self.repo.get('events',body.event_id)
        if existing['topic_id']!=topic['topic_id']:
            raise AppError('CROSS_TOPIC_EVENT','Event belongs to a different topic.',422)
        if existing['version']!=body.expected_version:
            raise AppError('VERSION_CONFLICT','Event changed; reopen the review with current state.')
        removed=[f.model_dump(mode='json') for f in body.superseded_facts]
        if any(f not in existing['state']['key_facts'] for f in removed):
            raise AppError('INVALID_CORRECTION','Superseded facts must exactly match current key facts.',422)
        evidence=self.repo.get('evidence',evidence_id)
        state={**existing['state'],
               'current_status':body.current_status.strip(),
               'key_facts':[f for f in existing['state']['key_facts'] if f not in removed]+[
                   Fact(fact=body.fact.strip(),evidence_ids=[evidence_id]).model_dump(mode='json')],
               'sources':list(dict.fromkeys([*existing['state']['sources'],evidence_id])),
               'timeline':[*existing['state']['timeline'],dict(occurred_at=evidence['published_at'],
                    description='人工审核采纳：'+body.fact.strip(),evidence_ids=[evidence_id])]}
        state['timeline'].sort(key=lambda item:datetime.fromisoformat(item['occurred_at'].replace('Z','+00:00')))
        if body.update_kind=='major_milestone' and state['current_status']==existing['state']['current_status']:
            raise AppError('INVALID_MILESTONE','A major milestone must change current_status.',422)
        stamp,run_id,transition_id=now(),uid(),uid()
        event={**existing,'state':state,'version':existing['version']+1,'updated_at':stamp}
        transition=dict(transition_id=transition_id,event_id=event['event_id'],run_id=run_id,
            before_state=existing['state'],after_state=state,evidence_ids=[evidence_id],
            idempotency_key=f"human-review:{topic['topic_id']}:{evidence_id}",committed_at=stamp)
        priority,action,reasons=decide_notification(body.update_kind,topic['notification_preferences'])
        notification=dict(notification_id=uid(),topic_id=topic['topic_id'],event_id=event['event_id'],
            transition_id=transition_id,priority=priority,action=action,
            reason_codes=[*reasons,'human_review','fact_corrected' if removed else 'evidence_accepted'],created_at=stamp)
        resolution=dict(resolution_id=uid(),topic_id=topic['topic_id'],evidence_id=evidence_id,decision='accept',
            note=body.note,actor='local_user',resolved_at=stamp,original_assessment=review['assessment'],request=request,
            run_id=run_id,transition_id=transition_id,event_id=event['event_id'],superseded_facts=removed)
        run=dict(run_id=run_id,topic_id=topic['topic_id'],trigger_type='human_review',provider='human',status='completed',
            fixture_batch_id='review',model=None,prompt_version='human-review-v1',started_at=stamp,completed_at=stamp,
            counts={'committed':1},error=None,snapshot={'topic':topic,'sources':[self.repo.get('sources',evidence['source_id'])]})
        step=dict(step_id=uid(),run_id=run_id,sequence=1,step_name='human_review_commit',evidence_id=evidence_id,
            status='completed',attempt=1,input_refs=[evidence_id,event['event_id']],output_refs=[transition_id],
            decision={'resolution_id':resolution['resolution_id'],'note':body.note,'superseded_facts':removed,
                      'notification_action':action},error=None,started_at=stamp,completed_at=stamp)
        return self.repo.commit_review(event,transition,notification,run,step,resolution,body.expected_version,self.commit_fault)

    def apply(self, topic, run_id, evidence, assessment):
        a = assessment
        if a.evidence_id != evidence['evidence_id']:
            raise AppError('INVALID_EVIDENCE', 'Assessment evidence ID does not match input.', 422)
        existing = None
        if a.association.type == 'existing_event':
            existing = self.repo.get('events', a.association.event_id)
            if existing['topic_id'] != topic['topic_id']:
                raise AppError('CROSS_TOPIC_EVENT', 'Event belongs to a different topic.', 422)
        if not a.is_relevant or a.requires_review or a.conflicts or a.novelty == 'conflict' or a.association.type == 'unresolved':
            review = a.is_relevant and (a.requires_review or bool(a.conflicts) or a.novelty == 'conflict' or a.association.type == 'unresolved')
            self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'review' if review else 'completed',
                                      a.model_dump(mode='json'), event_id=existing['event_id'] if existing and review else None,
                                      relation='conflicting' if review else None)
            return 'review' if review else 'irrelevant'
        if a.novelty in ('no_change', 'confirmation'):
            self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed', a.model_dump(mode='json'),
                                      event_id=existing['event_id'] if existing else None, relation='supporting')
            return 'no_change'
        if not a.proposed_state or not a.update_kind:
            raise AppError('INVALID_PROPOSAL', 'A state update requires a complete proposal.', 422)
        proposal = StateUpdateProposal(association=a.association, expected_version=a.expected_version,
                                       update_kind=a.update_kind, state=a.proposed_state, evidence_ids=[evidence['evidence_id']])
        state = proposal.state.model_dump(mode='json')
        available = self.repo.available_evidence(topic['topic_id'])
        approved = {s['source_id'] for s in self.repo.get('runs', run_id)['snapshot']['sources']}
        prior_ids = set(existing['state']['sources']) if existing else set()
        allowed = prior_ids | {evidence['evidence_id']}
        refs = set(state['sources'])
        for claim in [*state['key_facts'], *state['timeline'], *[f.model_dump() for f in a.supported_claims]]:
            refs.update(claim['evidence_ids'])
            if not set(claim['evidence_ids']) <= set(state['sources']):
                raise AppError('INVALID_REFERENCE', 'Claim references must appear in state sources.', 422)
        if not refs <= allowed or not refs <= available.keys() or evidence['evidence_id'] not in state['sources']:
            raise AppError('INVALID_REFERENCE', 'Proposal contains unavailable evidence references.', 422)
        if available[evidence['evidence_id']]['source_id'] not in approved:
            raise AppError('UNAPPROVED_SOURCE', 'Input source was not approved in this run.', 422)
        for item in state['timeline']:
            if datetime.fromisoformat(item['occurred_at'].replace('Z', '+00:00')) not in {
                    datetime.fromisoformat(available[i]['published_at'].replace('Z', '+00:00')) for i in item['evidence_ids']}:
                raise AppError('INVALID_TIMELINE', 'Timeline must use referenced publication time.', 422)
        state['timeline'].sort(key=lambda item: datetime.fromisoformat(item['occurred_at'].replace('Z', '+00:00')))
        if existing:
            if proposal.expected_version != existing['version']:
                raise AppError('VERSION_CONFLICT', 'Proposal version does not match the current event.')
            if state == existing['state']:
                self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed', a.model_dump(mode='json'))
                return 'no_change'
            for field in ('key_facts', 'timeline', 'sources'):
                if any(item not in state[field] for item in existing['state'][field]):
                    raise AppError('SILENT_OVERWRITE', 'Existing facts, timeline and references must be preserved.', 422)
            if proposal.update_kind == 'event_created':
                raise AppError('INVALID_UPDATE_KIND', 'Existing events cannot be created again.', 422)
        elif proposal.update_kind != 'event_created':
            raise AppError('INVALID_UPDATE_KIND', 'A new event requires event_created.', 422)
        current_claims = [f.model_dump(mode='json') for f in a.supported_claims
                          if evidence['evidence_id'] in f.evidence_ids]
        if not current_claims or any(f not in state['key_facts'] for f in current_claims):
            raise AppError('UNGROUNDED_UPDATE', 'Updates need current-evidence claims present in key_facts.', 422)
        prior_facts = existing['state']['key_facts'] if existing else []
        if not any(f not in prior_facts for f in current_claims):
            raise AppError('UNGROUNDED_UPDATE', 'Updates need a new supported fact.', 422)
        if not any(evidence['evidence_id'] in item['evidence_ids'] for item in state['timeline']):
            raise AppError('INVALID_TIMELINE', 'Updates need a timeline entry citing current evidence.', 422)
        if proposal.update_kind == 'major_milestone' and state['current_status'] == existing['state']['current_status']:
            raise AppError('INVALID_MILESTONE', 'A major milestone must materially change current_status.', 422)
        stamp = now()
        event = dict(event_id=existing['event_id'] if existing else uid(), topic_id=topic['topic_id'],
                     title=existing['title'] if existing else a.association.title, state=state,
                     version=existing['version'] + 1 if existing else 1,
                     created_at=existing['created_at'] if existing else stamp, updated_at=stamp)
        key = hashlib.sha256(f"{topic['topic_id']}:{evidence['canonical_evidence_id']}:policy-v1".encode()).hexdigest()
        transition = dict(transition_id=uid(), event_id=event['event_id'], run_id=run_id,
                          before_state=existing['state'] if existing else None, after_state=state,
                          evidence_ids=[evidence['evidence_id']], idempotency_key=key, committed_at=stamp)
        priority, action, reasons = decide_notification(proposal.update_kind, topic['notification_preferences'])
        notification = dict(notification_id=uid(), topic_id=topic['topic_id'], event_id=event['event_id'],
                            transition_id=transition['transition_id'], priority=priority, action=action,
                            reason_codes=reasons, created_at=stamp)
        committed = self.repo.commit_state(event, transition, notification, a.model_dump(mode='json'), evidence,
                                           proposal.expected_version, self.commit_fault)
        if not committed:
            self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed', a.model_dump(mode='json'))
        return 'committed' if committed else 'duplicate'
