import hashlib
from .core import AppError
from .domain import StateUpdateProposal, uid, now


class StateManager:
    def __init__(self, repository):
        self.repo = repository
        self.commit_fault = None  # Test-only injection; never exposed through HTTP.

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
            # The demo uses publication time; it must never substitute fetched_at.
            if item['occurred_at'] not in {available[i]['published_at'] for i in item['evidence_ids']}:
                raise AppError('INVALID_TIMELINE', 'Demo timeline must use referenced publication time.', 422)
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
        if proposal.update_kind == 'major_milestone' and not (
                'patch released' in evidence['content'].lower() and state['current_status'] == 'patched'):
            raise AppError('INVALID_MILESTONE', 'Demo milestone requires patch-release evidence.', 422)
        stamp = now()
        event = dict(event_id=existing['event_id'] if existing else uid(), topic_id=topic['topic_id'],
                     title=existing['title'] if existing else a.association.title, state=state,
                     version=existing['version'] + 1 if existing else 1,
                     created_at=existing['created_at'] if existing else stamp, updated_at=stamp)
        key = hashlib.sha256(f"{topic['topic_id']}:{evidence['canonical_evidence_id']}:policy-v1".encode()).hexdigest()
        transition = dict(transition_id=uid(), event_id=event['event_id'], run_id=run_id,
                          before_state=existing['state'] if existing else None, after_state=state,
                          evidence_ids=[evidence['evidence_id']], idempotency_key=key, committed_at=stamp)
        priority, action = {'event_created': ('routine', 'digest'), 'fact_added': ('routine', 'digest'),
                            'major_milestone': ('breaking', 'immediate'), 'minor_update': ('low', 'record_only')}[proposal.update_kind]
        reasons = [proposal.update_kind]
        if action == 'immediate' and topic['notification_preferences'] == 'digest_only':
            action = 'digest'
            reasons.append('digest_only_preference')
        notification = dict(notification_id=uid(), topic_id=topic['topic_id'], event_id=event['event_id'],
                            transition_id=transition['transition_id'], priority=priority, action=action,
                            reason_codes=reasons, created_at=stamp)
        committed = self.repo.commit_state(event, transition, notification, a.model_dump(mode='json'), evidence,
                                           proposal.expected_version, self.commit_fault)
        if not committed:
            self.repo.finish_evidence(topic['topic_id'], evidence['evidence_id'], 'completed', a.model_dump(mode='json'))
        return 'committed' if committed else 'duplicate'
