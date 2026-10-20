from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .config import now, settings
from .database import Base


def uid():
    return uuid4().hex


def stamp():
    return now().isoformat()


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(150), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(Text)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)


class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    title: Mapped[str] = mapped_column(String(100), default='新的养蜂咨询')
    created_at: Mapped[str] = mapped_column(String(50), default=stamp)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('chat_sessions.id'))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(50), default=stamp)


class Plan(Base):
    __tablename__ = 'plans'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('chat_sessions.id'))
    draft_id: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    advice: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(50), default=stamp)


class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (UniqueConstraint('plan_id', 'position'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    plan_id: Mapped[str] = mapped_column(ForeignKey('plans.id'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    position: Mapped[int]
    title: Mapped[str] = mapped_column(String(180))
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    due_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[str] = mapped_column(String(50), default=stamp)


class Checkin(Base):
    __tablename__ = 'checkins'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    report: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(50), default=stamp)


class KnowledgeChunk(Base):
    __tablename__ = 'knowledge_chunks'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list | None] = mapped_column(Vector(settings.embedding_dimensions).with_variant(JSON, 'sqlite'), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(150), nullable=True)


class AgentRun(Base):
    __tablename__ = 'agent_runs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('chat_sessions.id'))
    status: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(40))
    trace: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(50), default=stamp)
