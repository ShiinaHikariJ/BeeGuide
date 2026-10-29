import re
import time
from typing import TypedDict

import httpx
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError
from sqlalchemy import select

from .config import now, settings
from .database import SessionLocal
from .llm import generate_advice, review_advice, route_model
from .collaboration import add_usage, collect_context
from .models import Checkin, Message, Plan, Task, User, uid
from .schemas import Advice
from .weather import CITIES, get_weather


class State(TypedDict, total=False):
    user_id: str
    session_id: str
    question: str
    profile: dict
    profile_record: dict
    history: list
    conversation: list
    route: dict
    clarification: str
    evidence: dict
    weather: dict
    advice: dict
    draft_id: str
    plan_id: str
    trace: list
    usage: dict
    mode: str
    warning: str
    architecture: str
    coordination: dict
    candidate: dict
    draft_mode: str
    review: dict
    revision_count: int
    requires_review: bool
    workflow_path: str


def notify(event):
    get_stream_writer()(event)


def step(node, label):
    notify({'type': 'status', 'node': node, 'label': label})


def traced(state, tool, details, started):
    entry = {'tool': tool, 'duration_ms': round((time.perf_counter() - started) * 1000), **details}
    notify({'type': 'tool_result', **entry})
    return [*state.get('trace', []), entry]


def local_route(question):
    if any(w in question for w in ['上次', '做完', '完成了', '反馈', '回访']):
        intent = 'followup'
    elif any(w in question for w in ['检查', '开箱', '巡检']):
        intent = 'inspection'
    elif any(w in question for w in ['新手', '不会养', '开始养', '入门', '准备养', '养一箱']):
        intent = 'onboarding'
    elif any(w in question for w in ['计划', '清单', '任务']):
        intent = 'plan'
    else:
        intent = 'general'
    return {'intent': intent, 'need_weather': any(w in question for w in ['天气', '明天', '下雨', '温度', '风大', '今天适合'])}


def parse_profile(text):
    values = {}
    for city in CITIES:
        if city in text:
            values['city'] = city
            break
    if 'city' not in values:
        match = re.search(r'([\u4e00-\u9fff]{2,6})市', text)
        if match:
            values['city'] = match.group(1)
    if any(w in text for w in ['还没', '准备开始', '准备养', '没开始', '尚未', '想先养']):
        values['stage'] = 'preparing'
    elif any(w in text for w in ['已有', '已经', '正在养', '养了', '有蜂箱']):
        values['stage'] = 'keeping'
    for name in ['中蜂', '意蜂']:
        if name in text:
            values['species'] = name
    return values


def local_advice(state):
    hits = state['evidence']['hits']
    intent = state['route']['intent']
    intro = {'onboarding': '先把引入蜂群前的准备做好。下面是知识库中与这次咨询相关的资料整理。', 'inspection': '先明确检查目的和当前蜂群情况。天气可以提供参考，当前资料不支持仅凭天气决定开箱。', 'followup': '我已读取你的计划和执行反馈。先区分已经记录的事实与仍需观察的情况。', 'plan': '下面把相关资料整理为可确认的准备任务。', 'general': '下面是知识库里与问题相关的内容。'}[intent]
    if not hits:
        return Advice(answer='当前知识库没有找到足够相关的资料，暂时无法给出有依据的具体建议。请补充问题的背景，或咨询当地有经验的养蜂人员。', conditions=['知识库当前覆盖范围有限。']).model_dump()
    body = '\n\n'.join(f'{i + 1}. {h["title"]}\n{h["content"]}' for i, h in enumerate(hits[:3]))
    facts = [f'所在城市：{state["profile"]["city"]}'] if state['profile'].get('city') else []
    if state['profile'].get('stage'):
        facts.append('阶段：' + ('准备开始' if state['profile']['stage'] == 'preparing' else '已有蜂群'))
    if state.get('history'):
        facts.append(f'已读取 {len(state["history"])} 项历史任务；完成状态来自用户报告。')
    actions = []
    if intent == 'onboarding':
        actions = [{'title': '核对个人防护和蜂箱设备', 'detail': '参照准备资料列出已有和待购设备，确认规格与本地蜂种适配。', 'due_date': None}, {'title': '记录候选场地和当地蜜源', 'detail': '记录水源、主要植物花期、通道和邻里情况，按当地规定核实可用性。', 'due_date': None}]
    elif intent in ['inspection', 'plan', 'followup']:
        actions = [{'title': '补充本次观察与复查记录', 'detail': '记录日期、蜂种、检查目的和实际所见；未确认的信息保留未知，执行后再反馈。', 'due_date': None}]
    return Advice(answer=intro + '\n\n' + body, title={'onboarding': '开始养蜂的准备计划', 'inspection': '本次检查准备', 'followup': '复查与反馈安排'}.get(intent, '本次养蜂建议'), known_facts=facts, conditions=['最终内容为本地资料整理，未采用模型草稿。' if settings.live_model else '这是本地资料演示结果，未调用大模型。', '请核对引用资料的地区、蜂种和季节条件；跨地区具体操作需结合本地情况与专业意见。'], actions=actions, citation_ids=[h['id'] for h in hits[:3]]).model_dump()


def advice_context(state):
    return {'current_date': now().date().isoformat(), 'timezone': 'Asia/Shanghai',
            'question': state['question'], 'clarification': state.get('clarification'),
            'profile': state['profile'], 'history': state['history'], 'conversation': state['conversation'],
            'evidence': state['evidence']['hits'], 'weather': state.get('weather', {})}


def valid_citations(advice, state):
    allowed = {hit['id'] for hit in state['evidence']['hits']}
    return bool(advice['citation_ids']) and set(advice['citation_ids']) <= allowed


def build_graph(checkpointer, retriever):
    async def context(state):
        step('context', '读取养蜂档案与历史')
        started = time.perf_counter()
        with SessionLocal() as db:
            user = db.get(User, state['user_id'])
            tasks = list(db.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.created_at.desc()).limit(10)))
            history = []
            for task in tasks:
                report = db.scalar(select(Checkin).where(Checkin.task_id == task.id, Checkin.user_id == user.id).order_by(Checkin.created_at.desc()).limit(1))
                history.append({'id': task.id, 'title': task.title, 'detail': task.detail, 'status': task.status,
                                'due_date': task.due_date, 'created_at': task.created_at,
                                'last_report': report.report if report else None, 'reported_at': report.created_at if report else None})
            messages = list(db.scalars(select(Message).where(Message.session_id == state['session_id']).order_by(Message.created_at.desc()).limit(6)))
        current_profile = state.get('profile', user.profile) if state.get('profile_record') == user.profile else user.profile
        return {'profile': current_profile, 'profile_record': user.profile, 'history': history,
                'architecture': settings.agent_architecture if settings.live_model else 'local',
                'conversation': [{'role': m.role, 'content': m.content} for m in reversed(messages)],
                'trace': traced(state, 'get_keeper_context', {'summary': f'读取档案和 {len(history)} 项历史任务'}, started)}

    async def route(state):
        step('route', '判断咨询场景与需要的工具')
        started = time.perf_counter()
        result, usage, warning = local_route(state['question']), {}, ''
        if settings.live_model:
            try:
                result, usage = await route_model(state['question'], state['profile'])
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
                usage = add_usage(usage, getattr(error, 'usage', {}))
                warning = '模型路由暂不可用，本次使用规则路由。'
        workflow_path = 'collaborative' if state.get('architecture') == 'multi' and (result['intent'] != 'general' or result['need_weather']) else 'direct'
        return {'route': result, 'usage': usage, 'warning': warning, 'workflow_path': workflow_path,
                'trace': traced(state, 'route_consultation', {'agent': 'coordinator', 'summary': '判断咨询场景：' + result['intent'],
                                                           'success': not bool(warning), 'usage': usage}, started)}

    async def clarify(state):
        step('clarify', '核对必要信息')
        profile = {**state['profile'], **parse_profile(state['question'])}
        knowledge_only = any(w in state['question'] for w in ['面罩', '手套', '防护装备', '蜂路']) and not any(w in state['question'] for w in ['计划', '养一箱', '开始养'])
        needs_context = state['route']['intent'] in ['onboarding', 'inspection', 'plan'] and not knowledge_only
        missing = [k for k in ['city', 'stage'] if not profile.get(k)] if needs_context else (['city'] if state['route']['need_weather'] and not profile.get('city') else [])
        clarification = ''
        for _ in range(2):
            if not missing:
                break
            question = '为了让建议适合你的情况，请补充：' + '、'.join({'city': '所在城市', 'stage': '准备开始还是已有蜂群'}[k] for k in missing) + '。例如“杭州，准备开始”。也可以在右侧填写并保存档案。'
            reply = interrupt({'kind': 'clarify', 'question': question, 'fields': missing})
            clarification += str(reply) + '\n'
            with SessionLocal() as db:
                stored = db.get(User, state['user_id']).profile
            profile.update({k: v for k, v in stored.items() if v not in ['', None]})
            profile.update(parse_profile(str(reply)))
            missing = [k for k in missing if not profile.get(k)]
        with SessionLocal() as db:
            profile_record = db.get(User, state['user_id']).profile
        return {'profile': profile, 'profile_record': profile_record, 'clarification': clarification}

    async def retrieve(state):
        step('retrieve', '检索相关养蜂资料')
        started = time.perf_counter()
        supplements = {'onboarding': ' 新手准备装备选址', 'inspection': ' 检查开箱天气记录', 'followup': ' 计划反馈复查记录', 'plan': ' 准备计划'}
        evidence = await retriever.search(state['question'] + supplements.get(state['route']['intent'], ''))
        return {'evidence': evidence, 'trace': traced(state, 'search_beekeeping_knowledge', {'summary': f'命中 {len(evidence["hits"])} 条资料', 'retrieval_mode': evidence['mode'], 'citation_ids': [h['id'] for h in evidence['hits']], 'requested_seasons': evidence.get('requested_seasons', []), 'warning': evidence['warning']}, started)}

    async def weather(state):
        step('weather', '查询当地天气预报')
        started = time.perf_counter()
        city = state['profile'].get('city')
        result = await get_weather(city) if city else {'available': False, 'reason': '未提供城市，未调用天气服务。'}
        return {'weather': result, 'trace': traced(state, 'get_weather', {'summary': f'{city}天气已获取' if result.get('available') else result['reason'], 'available': result.get('available'), 'provider': result.get('provider')}, started)}

    async def coordinate(state):
        step('coordinate', '协调员：选择工具并收集资料')
        return await collect_context(state, retriever, notify)

    async def generate(state):
        step('generate', '养蜂顾问：整理有依据的建议与任务草稿')
        started = time.perf_counter()
        mode, warning, usage = 'local_evidence', state.get('warning', ''), state.get('usage', {})
        failure_reason = ''
        advice = local_advice(state)
        generation_usage = {}
        requires_review = state.get('architecture') == 'multi' and (state['route']['intent'] != 'general' or state['route']['need_weather'])
        observed_first = False

        def writer(event):
            nonlocal observed_first
            if requires_review:
                if event['type'] == 'text_delta' and not observed_first:
                    observed_first = True
                    notify({'type': 'model_token', 'agent': 'advisor'})
            else:
                notify(event)

        if settings.live_model and state['evidence']['hits']:
            try:
                advice, new_usage = await generate_advice(advice_context(state), writer)
                generation_usage = new_usage
                usage = add_usage(usage, new_usage)
                advice = Advice.model_validate(advice).model_dump()
                mode = 'live_model'
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
                generation_usage = getattr(error, 'usage', {})
                usage = add_usage(usage, generation_usage)
                mode, warning = 'model_fallback', '模型调用或结构校验失败，本次仅展示知识资料整理。'
                failure_reason = getattr(error, 'reason', type(error).__name__)
                if failure_reason == 'context_budget_exceeded':
                    warning = '本次资料较长，无法在模型输入限制内完整处理，仅展示资料整理。'
        if mode == 'live_model' and not valid_citations(advice, state):
            warning = '模型返回了无效引用或未引用资料，本次已回退为资料整理。'
            advice, mode = local_advice(state), 'model_fallback'
        if not state['evidence']['hits']:
            advice, mode = local_advice(state), 'insufficient_evidence'
        return {'candidate': advice, 'draft_mode': mode, 'warning': warning, 'usage': usage,
                'requires_review': requires_review and mode == 'live_model', 'revision_count': 0,
                'review': {'status': 'pending' if requires_review and mode == 'live_model' else 'not_required', 'issues': []},
                'trace': traced(state, 'generate_advice', {'agent': 'advisor', 'summary': '顾问已生成草稿' if mode == 'live_model' else '使用资料整理', 'success': mode == 'live_model', 'mode': mode, 'usage': generation_usage, 'failure_reason': failure_reason}, started)}

    async def review(state):
        step('review', '审查员：检查引用依据、适用条件与重复任务')
        started = time.perf_counter()
        usage, warning = state['usage'], state.get('warning', '')
        review_usage = {}
        try:
            result, extra = await review_advice({**advice_context(state), 'draft': state['candidate']})
            review_usage = extra
            from .schemas import AdviceReview
            result = AdviceReview.model_validate(result).model_dump()
            if result['verdict'] == 'approve' and result['issues'] or result['verdict'] == 'revise' and not result['issues']:
                raise ValueError('Inconsistent review verdict')
            usage = add_usage(usage, extra)
            status = {'approve': 'passed', 'revise': 'revise', 'abstain': 'blocked'}[result['verdict']]
            if status == 'revise' and state.get('revision_count', 0) >= 1:
                status = 'blocked'
            result = {**result, 'status': status, 'revision_count': state.get('revision_count', 0)}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
            review_usage = getattr(error, 'usage', {})
            usage = add_usage(usage, review_usage)
            result = {'status': 'unavailable', 'summary': '审查服务暂不可用，回退为资料整理。', 'issues': [], 'revision_count': state.get('revision_count', 0)}
            result['failure_reason'] = getattr(error, 'reason', type(error).__name__)
            warning = '审查服务暂不可用，未展示未经审查的任务草稿。'
        return {'review': result, 'usage': usage, 'warning': warning,
                'trace': traced(state, 'review_advice', {'agent': 'reviewer', 'summary': result['summary'],
                                                       'success': result['status'] != 'unavailable', 'verdict': result.get('verdict'), 'usage': review_usage,
                                                       'revision_count': state.get('revision_count', 0), 'issues': result['issues'], 'failure_reason': result.get('failure_reason')}, started)}

    async def revise(state):
        step('revise', '养蜂顾问：根据审查意见修改一次')
        started = time.perf_counter()
        usage, warning = state['usage'], state.get('warning', '')
        mode, candidate = state['draft_mode'], state['candidate']
        revision_usage = {}
        try:
            candidate, extra = await generate_advice({**advice_context(state), 'previous_draft': state['candidate'],
                                                    'review_feedback': state['review']}, lambda event: None)
            revision_usage = extra
            usage = add_usage(usage, extra)
            candidate = Advice.model_validate(candidate).model_dump()
            if not valid_citations(candidate, state):
                raise ValueError('Invalid revised citations')
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
            revision_usage = getattr(error, 'usage', {})
            usage = add_usage(usage, revision_usage)
            candidate, mode = local_advice(state), 'review_fallback'
            candidate['actions'] = []
            warning = '修改调用或校验失败，本次只展示资料。'
        return {'candidate': candidate, 'draft_mode': mode, 'usage': usage, 'warning': warning, 'revision_count': 1,
                'review': state['review'] if mode == 'live_model' else {**state['review'], 'status': 'unavailable'},
                'trace': traced(state, 'revise_advice', {'agent': 'advisor', 'summary': '顾问完成一次修改' if mode == 'live_model' else '修改失败，回退资料',
                                                       'success': mode == 'live_model', 'revision_count': 1, 'usage': revision_usage}, started)}

    async def publish(state):
        advice, mode = state['candidate'], state['draft_mode']
        review_result = state.get('review', {'status': 'not_required', 'issues': []})
        warning = state.get('warning', '')
        if state.get('requires_review') and review_result['status'] not in ['passed', 'not_required']:
            advice, mode = local_advice(state), 'review_fallback'
            advice['actions'] = []
            advice['conditions'].append('任务草稿未通过审查或审查不可用，本次仅展示资料。')
            warning = warning or '本次审查未通过，仅展示资料，不保存任务草稿。'
        draft_id = uid()
        output = {**advice, 'citations': [h for h in state['evidence']['hits'] if h['id'] in advice['citation_ids']],
                  'weather': state.get('weather'), 'draft_id': draft_id, 'mode': mode, 'warning': warning,
                  'retrieval_mode': state['evidence']['mode'], 'architecture': state.get('architecture', 'single'),
                  'review': review_result, 'coordination': state.get('coordination', {})}
        output['workflow_path'] = state.get('workflow_path', 'direct')
        notify({'type': 'advice', 'advice': output})
        return {'advice': output, 'draft_id': draft_id, 'mode': mode, 'warning': warning}

    async def confirm(state):
        reply = interrupt({'kind': 'confirm', 'draft_id': state['draft_id'], 'question': '是否把这些任务保存为养护计划？'})
        if not isinstance(reply, dict) or reply.get('action') != 'save' or reply.get('draft_id') != state['draft_id']:
            return {'plan_id': ''}
        step('save', '保存已确认的计划与待办')
        started = time.perf_counter()
        with SessionLocal() as db:
            plan = db.scalar(select(Plan).where(Plan.draft_id == state['draft_id'], Plan.user_id == state['user_id']))
            if not plan:
                plan = Plan(user_id=state['user_id'], session_id=state['session_id'], draft_id=state['draft_id'], title=state['advice']['title'], advice=state['advice'])
                db.add(plan)
                db.flush()
                for i, action in enumerate(state['advice']['actions']):
                    db.add(Task(user_id=state['user_id'], plan_id=plan.id, position=i, **action))
                db.commit()
            plan_id = plan.id
        trace = traced(state, 'save_plan', {'summary': '用户确认的计划已保存', 'plan_id': plan_id}, started)
        notify({'type': 'plan_saved', 'plan_id': plan_id})
        return {'plan_id': plan_id, 'trace': trace}

    graph = StateGraph(State)
    for name, function in [('context', context), ('route', route), ('clarify', clarify), ('retrieve', retrieve), ('weather', weather),
                           ('coordinate', coordinate), ('generate', generate), ('review', review), ('revise', revise), ('publish', publish), ('confirm', confirm)]:
        graph.add_node(name, function)
    graph.add_edge(START, 'context')
    graph.add_edge('context', 'route')
    graph.add_edge('route', 'clarify')
    graph.add_conditional_edges('clarify', lambda s: 'coordinate' if s.get('architecture') == 'multi' and s.get('workflow_path') == 'collaborative' else 'retrieve')
    graph.add_edge('coordinate', 'generate')
    graph.add_conditional_edges('retrieve', lambda s: 'weather' if s['route']['need_weather'] else 'generate')
    graph.add_edge('weather', 'generate')
    graph.add_conditional_edges('generate', lambda s: 'review' if s.get('requires_review') else 'publish')
    graph.add_conditional_edges('review', lambda s: 'revise' if s['review']['status'] == 'revise' else 'publish')
    graph.add_conditional_edges('revise', lambda s: 'review' if s['draft_mode'] == 'live_model' else 'publish')
    graph.add_conditional_edges('publish', lambda s: 'confirm' if s['advice']['actions'] else END)
    graph.add_edge('confirm', END)
    return graph.compile(checkpointer=checkpointer)
