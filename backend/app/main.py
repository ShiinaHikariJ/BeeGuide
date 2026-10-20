import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent import build_graph
from .auth import create_token, current_user, verify_password
from .config import DATA, ROOT, settings
from .database import get_db, SessionLocal
from .models import AgentRun, ChatSession, Checkin, KnowledgeChunk, Message, Plan, Task, User
from .rag import Retriever
from .schemas import CheckinIn, LoginIn, MessageIn, Profile, ResumeIn, TaskDateIn
from .seed import seed


@asynccontextmanager
async def lifespan(app):
    command.upgrade(Config(str(ROOT / 'backend' / 'alembic.ini')), 'head')
    seed()
    app.state.retriever = Retriever()
    app.state.locks = {}
    if settings.database_url.startswith('postgresql'):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        saver = AsyncPostgresSaver.from_conn_string(settings.database_url.replace('postgresql+psycopg://', 'postgresql://'))
    else:
        saver = AsyncSqliteSaver.from_conn_string(str(DATA / 'checkpoints.db'))
    async with saver as checkpoint:
        await checkpoint.setup()
        app.state.graph = build_graph(checkpoint, app.state.retriever)
        yield


app = FastAPI(title='蜂伴 BeeGuide', version='0.1.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5178', 'http://127.0.0.1:5178'], allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Authorization', 'Content-Type'])


def owned(db, model, item_id, user_id):
    item = db.get(model, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(404, '记录不存在')
    return item


def graph_config(session_id):
    return {'configurable': {'thread_id': session_id}, 'recursion_limit': 20}


def interruption(snapshot):
    for task in snapshot.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


@app.get('/api/health')
def health():
    return {'status': 'ok', 'model_mode': 'live_model' if settings.live_model else 'local_evidence', 'model': settings.model_name if settings.live_model else None, 'embedding_enabled': settings.embedding_enabled and settings.live_model, 'database': 'PostgreSQL' if settings.database_url.startswith('postgresql') else 'SQLite', 'timezone': 'Asia/Shanghai', 'agent_architecture': settings.agent_architecture, 'agent_roles': ['coordinator', 'advisor', 'reviewer'] if settings.agent_architecture == 'multi' and settings.live_model else [], 'tool_protocol': 'tools/tool_calls' if settings.agent_architecture == 'multi' and settings.live_model else None}


@app.post('/api/auth/login')
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, '账号或密码不正确')
    return {'token': create_token(user.id), 'user': {'id': user.id, 'name': user.name, 'email': user.email, 'profile': user.profile}}


@app.get('/api/me')
def me(user: User = Depends(current_user)):
    return {'id': user.id, 'name': user.name, 'email': user.email, 'profile': user.profile}


@app.put('/api/profile')
def profile(body: Profile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.profile = body.model_dump()
    db.commit()
    return user.profile


@app.get('/api/sessions')
def sessions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{'id': s.id, 'title': s.title, 'created_at': s.created_at} for s in db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.created_at.desc()))]


@app.post('/api/sessions')
def new_session(user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = ChatSession(user_id=user.id)
    db.add(session)
    db.commit()
    return {'id': session.id, 'title': session.title}


@app.get('/api/sessions/{session_id}')
async def get_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = owned(db, ChatSession, session_id, user.id)
    messages = db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))
    snapshot = await app.state.graph.aget_state(graph_config(session_id))
    return {'id': session_id, 'title': session.title, 'messages': [{'id': m.id, 'role': m.role, 'content': m.content, 'payload': m.payload} for m in messages], 'awaiting': interruption(snapshot)}


def sse(event):
    return 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'


async def run_stream(session_id, user_id, graph_input, save_answer):
    lock = app.state.locks.setdefault(session_id, asyncio.Lock())
    started = time.perf_counter()
    first_token = None
    status = 'error'
    events = []
    try:
        yield sse({'type': 'status', 'node': 'start', 'label': '开始处理咨询'})
        async for event in app.state.graph.astream(graph_input, graph_config(session_id), stream_mode='custom'):
            if event.get('type') in ['text_delta', 'model_token'] and first_token is None:
                first_token = round((time.perf_counter() - started) * 1000)
            events.append(event)
            yield sse(event)
        snapshot = await app.state.graph.aget_state(graph_config(session_id))
        waiting = interruption(snapshot)
        state = snapshot.values
        advice = state.get('advice', {})
        status = 'waiting_' + waiting['kind'] if waiting else 'complete'
        with SessionLocal() as db:
            if waiting and waiting['kind'] == 'clarify':
                db.add(Message(session_id=session_id, role='assistant', content=waiting['question'], payload={'clarification': True}))
            elif save_answer and advice:
                db.add(Message(session_id=session_id, role='assistant', content=advice['answer'], payload=advice))
            elif any(e.get('type') == 'plan_saved' for e in events):
                db.add(Message(session_id=session_id, role='assistant', content='计划已保存，可在“养护计划”中查看、记录执行反馈。', payload={'plan_saved': state.get('plan_id')}))
            run = AgentRun(user_id=user_id, session_id=session_id, status=status, mode=state.get('mode', 'pending'), trace=state.get('trace', []), metrics={'duration_ms': round((time.perf_counter() - started) * 1000), 'first_token_ms': first_token, 'usage': state.get('usage', {}), 'architecture': state.get('architecture', 'single'), 'workflow_path': state.get('workflow_path', 'direct'), 'coordination': state.get('coordination', {}), 'review_status': state.get('review', {}).get('status', 'not_required'), 'revision_count': state.get('revision_count', 0)})
            db.add(run)
            db.commit()
            run_id = run.id
        if waiting:
            yield sse({'type': 'awaiting', **waiting})
        yield sse({'type': 'done', 'run_id': run_id, 'status': status})
    except Exception:
        logging.getLogger(__name__).exception('Agent workflow failed')
        with SessionLocal() as db:
            db.add(AgentRun(user_id=user_id, session_id=session_id, status='error', mode='error', trace=[{'tool': 'workflow', 'summary': '流程发生错误，请重新尝试', 'success': False}], metrics={'duration_ms': round((time.perf_counter() - started) * 1000)}))
            db.commit()
        yield sse({'type': 'error', 'message': '流程暂时失败，已记录运行状态。请重试或新建咨询。'})
    finally:
        lock.release()


@app.post('/api/sessions/{session_id}/messages')
async def send_message(session_id: str, body: MessageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = owned(db, ChatSession, session_id, user.id)
    lock = app.state.locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, '这次咨询仍在处理中')
    content = body.content.strip()
    if not content:
        raise HTTPException(422, '请输入咨询内容')
    await lock.acquire()
    try:
        snapshot = await app.state.graph.aget_state(graph_config(session_id))
        waiting = interruption(snapshot)
        if waiting and waiting['kind'] == 'confirm':
            await app.state.graph.ainvoke(Command(resume={'action': 'skip', 'draft_id': waiting['draft_id']}), graph_config(session_id))
        db.add(Message(session_id=session_id, role='user', content=content, payload={}))
        if session.title == '新的养蜂咨询':
            session.title = content[:24]
        db.commit()
        graph_input = Command(resume=content) if waiting and waiting['kind'] == 'clarify' else {'user_id': user.id, 'session_id': session_id, 'question': content, 'trace': [], 'usage': {}, 'advice': {}, 'weather': {}, 'warning': '', 'mode': 'pending', 'clarification': '', 'plan_id': '', 'candidate': {}, 'draft_mode': 'pending', 'coordination': {}, 'review': {}, 'revision_count': 0, 'requires_review': False, 'evidence': {}}
    except BaseException:
        lock.release()
        raise
    return StreamingResponse(run_stream(session_id, user.id, graph_input, True), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/sessions/{session_id}/resume')
async def resume(session_id: str, body: ResumeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned(db, ChatSession, session_id, user.id)
    existing = db.scalar(select(Plan).where(Plan.draft_id == body.draft_id, Plan.user_id == user.id, Plan.session_id == session_id))
    if existing and body.action == 'save':
        return StreamingResponse(iter([sse({'type': 'plan_saved', 'plan_id': existing.id}), sse({'type': 'done', 'status': 'complete'})]), media_type='text/event-stream')
    lock = app.state.locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, '这次咨询仍在处理中')
    await lock.acquire()
    try:
        waiting = interruption(await app.state.graph.aget_state(graph_config(session_id)))
        if not waiting or waiting['kind'] != 'confirm' or waiting['draft_id'] != body.draft_id:
            raise HTTPException(409, '这份草稿已经结束，请根据当前咨询重新生成')
    except BaseException:
        lock.release()
        raise
    return StreamingResponse(run_stream(session_id, user.id, Command(resume=body.model_dump()), False), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


@app.get('/api/plans')
def plans(user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = []
    for plan in db.scalars(select(Plan).where(Plan.user_id == user.id).order_by(Plan.created_at.desc())):
        tasks = list(db.scalars(select(Task).where(Task.plan_id == plan.id, Task.user_id == user.id).order_by(Task.position)))
        result.append({'id': plan.id, 'title': plan.title, 'created_at': plan.created_at, 'tasks': [{'id': t.id, 'title': t.title, 'detail': t.detail, 'status': t.status, 'due_date': t.due_date, 'reports': [{'report': c.report, 'created_at': c.created_at} for c in db.scalars(select(Checkin).where(Checkin.task_id == t.id, Checkin.user_id == user.id).order_by(Checkin.created_at.desc()))]} for t in tasks]})
    return result


@app.post('/api/tasks/{task_id}/checkins')
def checkin(task_id: str, body: CheckinIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = owned(db, Task, task_id, user.id)
    previous = db.scalar(select(Checkin).where(Checkin.task_id == task.id, Checkin.user_id == user.id).order_by(Checkin.created_at.desc()).limit(1))
    if not previous or previous.report != body.report or task.status != body.status:
        db.add(Checkin(task_id=task.id, user_id=user.id, report=body.report))
        task.status = body.status
        db.commit()
    return {'id': task.id, 'status': task.status}


@app.put('/api/tasks/{task_id}')
def schedule_task(task_id: str, body: TaskDateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = owned(db, Task, task_id, user.id)
    task.due_date = body.due_date.isoformat() if body.due_date else None
    db.commit()
    return {'id': task.id, 'due_date': task.due_date}


@app.get('/api/knowledge')
def knowledge(db: Session = Depends(get_db)):
    return [{'id': c.id, 'title': c.title, 'content': c.content, **c.meta} for c in db.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.id))]


@app.get('/api/runs')
def runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{'id': r.id, 'session_id': r.session_id, 'status': r.status, 'mode': r.mode, 'trace': r.trace, 'metrics': r.metrics, 'created_at': r.created_at} for r in db.scalars(select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.created_at.desc()).limit(50))]


dist = ROOT / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
