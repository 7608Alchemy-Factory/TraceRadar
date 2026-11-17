from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TopicInput(Model):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    notification_preferences: Literal['standard', 'digest_only'] = 'standard'


class TopicPatch(Model):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    notification_preferences: Literal['standard', 'digest_only'] | None = None


class Topic(TopicInput):
    topic_id: str
    status: Literal['draft', 'active'] = 'draft'
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Source(Model):
    source_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{1,63}$')
    name: str = Field(min_length=1, max_length=200)
    type: Literal['fixture', 'replay', 'rss', 'manual'] = 'fixture'
    endpoint: str = Field(min_length=1, max_length=1000)
    config: dict = Field(default_factory=dict)


class SourceRegisterInput(Source):
    pass


class SourceApproval(Model):
    approved_source_ids: list[str]


class TopicSource(Model):
    topic_id: str
    source_id: str
    approved_at: AwareDatetime


class Evidence(Model):
    evidence_id: str
    source_id: str
    external_id: str
    url: str
    title: str
    content: str
    published_at: AwareDatetime
    fetched_at: AwareDatetime
    content_hash: str
    canonical_evidence_id: str


class TimelineItem(Model):
    occurred_at: AwareDatetime
    description: str
    evidence_ids: list[str] = Field(min_length=1)


class Fact(Model):
    fact: str
    evidence_ids: list[str] = Field(min_length=1)


class EventState(Model):
    current_status: str = Field(min_length=1)
    timeline: list[TimelineItem]
    key_facts: list[Fact]
    sources: list[str] = Field(min_length=1)


class Event(Model):
    event_id: str
    topic_id: str
    title: str
    state: EventState
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Association(Model):
    type: Literal['existing_event', 'new_event', 'unresolved']
    event_id: str | None = None
    title: str | None = None
    reason: str

    @model_validator(mode='after')
    def check_target(self):
        if self.type == 'existing_event' and not self.event_id:
            raise ValueError('existing_event requires event_id')
        if self.type == 'new_event' and not self.title:
            raise ValueError('new_event requires title')
        return self


UpdateKind = Literal['event_created', 'fact_added', 'major_milestone', 'minor_update']


class Assessment(Model):
    evidence_id: str
    is_relevant: bool
    association: Association
    novelty: Literal['new_fact', 'confirmation', 'conflict', 'no_change']
    supported_claims: list[Fact] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    proposed_state: EventState | None = None
    requires_review: bool = False
    reason_codes: list[str]
    update_kind: UpdateKind | None = None
    expected_version: int | None = None


class StateUpdateProposal(Model):
    association: Association
    expected_version: int | None
    update_kind: UpdateKind
    state: EventState
    evidence_ids: list[str] = Field(min_length=1)


class ManualEvidenceItem(Model):
    external_id: str | None = None
    url: str = ''
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    published_at: AwareDatetime | None = None
    source_id: str | None = None


class RunInput(Model):
    fixture_batch_id: str = Field(default='1', min_length=1, max_length=100)
    provider: Literal['mock', 'qwen'] | None = None
    as_of: AwareDatetime | None = None
    prefilter_enabled: bool = False
    near_duplicate_enabled: bool = True
    manual_items: list[ManualEvidenceItem] = Field(default_factory=list)


RunStatus = Literal['pending', 'running', 'completed', 'completed_with_errors', 'failed', 'cancelled']
StepStatus = Literal['pending', 'running', 'completed', 'skipped', 'failed']


class TopicEvidence(Model):
    topic_id: str
    evidence_id: str
    status: Literal['pending', 'completed', 'review', 'failed']
    assessment: Assessment | None = None
    error: dict | None = None


class StateTransition(Model):
    transition_id: str
    event_id: str
    run_id: str
    before_state: EventState | None
    after_state: EventState
    evidence_ids: list[str]
    idempotency_key: str
    committed_at: AwareDatetime


class Notification(Model):
    notification_id: str
    topic_id: str
    event_id: str
    transition_id: str
    priority: Literal['breaking', 'routine', 'low']
    action: Literal['immediate', 'digest', 'record_only']
    reason_codes: list[str]
    created_at: AwareDatetime


class Run(Model):
    run_id: str
    topic_id: str
    trigger_type: Literal['manual'] = 'manual'
    status: RunStatus
    provider: Literal['mock', 'qwen']
    fixture_batch_id: str
    model: str | None = None
    prompt_version: str = 'assessment-v1'
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    error: dict | None = None
    snapshot: dict


class RunStep(Model):
    step_id: str
    run_id: str
    sequence: int
    step_name: str
    evidence_id: str | None = None
    status: StepStatus
    attempt: int = 1
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    decision: dict = Field(default_factory=dict)
    error: dict | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
