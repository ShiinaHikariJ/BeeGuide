from typing import Literal
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginIn(BaseModel):
    email: str = Field(max_length=150)
    password: str = Field(max_length=200)


class Profile(BaseModel):
    city: str = Field(default='', max_length=80)
    stage: Literal['preparing', 'keeping', ''] = ''
    species: str = Field(default='', max_length=50)
    experience: str = Field(default='', max_length=100)
    equipment: str = Field(default='', max_length=300)
    hive_count: int = Field(default=0, ge=0, le=10000)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=3000)


class ResumeIn(BaseModel):
    action: Literal['save', 'skip']
    draft_id: str = Field(min_length=1, max_length=40)


class CheckinIn(BaseModel):
    report: str = Field(min_length=1, max_length=2000)
    status: Literal['done', 'pending'] = 'done'


class TaskDateIn(BaseModel):
    due_date: date | None = None


class Route(BaseModel):
    intent: Literal['onboarding', 'inspection', 'general', 'followup', 'plan'] = 'general'
    need_weather: bool = False


class ActionItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(max_length=160)
    detail: str = Field(max_length=1000)
    due_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')

    @field_validator('due_date')
    @classmethod
    def valid_calendar_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class Advice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(max_length=12000)
    title: str = Field(default='本次养蜂建议', max_length=100)
    known_facts: list[str] = Field(default_factory=list, max_length=10)
    conditions: list[str] = Field(default_factory=list, max_length=10)
    actions: list[ActionItem] = Field(default_factory=list, max_length=5)
    citation_ids: list[str] = Field(default_factory=list, max_length=8)


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: Literal['unsupported_claim', 'applicability', 'duplicate_task', 'missing_context', 'evidence_missing']
    detail: str = Field(min_length=1, max_length=800)


class AdviceReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    verdict: Literal['approve', 'revise', 'abstain']
    summary: str = Field(min_length=1, max_length=800)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=6)
