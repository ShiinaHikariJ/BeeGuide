from copy import deepcopy
import json

import httpx
import pytest

from app.config import Settings, settings
from app.context_budget import ContextBudgetError, bound_tool_payload, estimate_input_tokens, prepare_chat
from app.llm import coordinate_tools, generate_advice, review_advice, route_model
from app.schemas import Advice


@pytest.fixture
def small_window(monkeypatch):
    monkeypatch.setattr(settings, 'model_context_window', 4096)
    monkeypatch.setattr(settings, 'model_max_output_tokens', 512)
    monkeypatch.setattr(settings, 'context_safety_tokens', 128)
    monkeypatch.setattr(settings, 'context_tool_result_tokens', 1024)


def chat(context):
    return [{'role': 'system', 'content': '只依据实际提供的资料。'},
            {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]


def test_optional_memory_is_selected_without_losing_requirements_or_task_status(small_window):
    context = {
        'question': '制定冬季准备计划，不要安排开箱', 'clarification': '杭州，准备开始，中蜂',
        'profile': {'city': '杭州', 'stage': 'preparing', 'species': '中蜂'},
        'conversation': [{'role': 'user', 'content': '预算不能超过500元'},
                         {'role': 'assistant', 'content': '历史回答' * 3000}],
        'history': [{'id': 'done-task', 'title': '核对面罩', 'status': 'done', 'detail': '旧详情' * 3000}],
        'evidence': [{'id': 'keep', 'content': '冬季准备需要核对设备。', 'region': '浙江', 'seasons': ['冬季']},
                     {'id': 'large', 'content': '其他资料' * 3000}],
    }
    messages = chat(context)
    original = deepcopy(messages)
    prepared = prepare_chat(messages)
    selected = json.loads(prepared.messages[1]['content'])
    assert messages == original
    for key in ('question', 'clarification', 'profile'):
        assert selected[key] == context[key]
    assert selected['conversation'] == [context['conversation'][0]]
    assert selected['history_summary'][0]['id'] == 'done-task'
    assert selected['history_summary'][0]['status'] == 'done'
    assert selected['evidence'] == [context['evidence'][0]]
    assert prepared.report['omitted_records'] == {'conversation': 1, 'history': 1, 'evidence': 1}
    assert prepared.report['input_estimate'] <= prepared.report['input_budget']


def test_history_selection_keeps_more_relevant_record(small_window):
    context = {'question': '冬季通风', 'history': [
        {'id': 'summer', 'title': '夏季防暑', 'detail': '旧记录' * 1000},
        {'id': 'winter', 'title': '冬季通风', 'status': 'done', 'last_report': '通风口已核对'},
    ]}
    selected = json.loads(prepare_chat(chat(context)).messages[1]['content'])
    assert [task['id'] for task in selected['history']] == ['winter']
    assert selected['history_summary'][0]['id'] == 'summer'


def test_review_preserves_complete_cited_evidence(small_window):
    source = {'id': 'cited', 'content': '适用条件和完整依据。', 'species': ['中蜂'], 'region': '浙江'}
    context = {'draft': {'answer': '建议', 'citation_ids': ['cited']},
               'evidence': [source, {'id': 'uncited', 'content': '其他资料' * 2000}]}
    selected = json.loads(prepare_chat(chat(context)).messages[1]['content'])
    assert selected['draft'] == context['draft']
    assert selected['evidence'] == [source]


def test_tool_payload_removes_whole_records_and_marks_omissions():
    source = {'id': 'source', 'content': '完整的资料和适用条件。' * 10, 'region': '浙江'}
    payload = {'ok': True, 'result': {'hits': [source, {**source, 'id': 'second'}], 'mode': 'test'}}
    original = deepcopy(payload)
    bounded = bound_tool_payload(payload, 700)
    assert bounded['result']['hits'] == [source]
    assert bounded['context_selection']['omitted_records']['hits'] == 1
    assert len(json.dumps(bounded, ensure_ascii=False).encode('utf-8')) < 700
    assert payload == original


def test_unknown_oversized_tool_result_is_explicitly_unavailable():
    bounded = bound_tool_payload({'ok': True, 'result': {'text': '大结果' * 10000}}, 512)
    assert bounded['ok'] is False
    assert bounded['error'] == 'tool_result_too_large'
    assert bounded['context_selection']['result_omitted'] is True


def test_total_tool_history_keeps_all_call_result_pairs(small_window, monkeypatch):
    monkeypatch.setattr(settings, 'context_tool_result_tokens', 2048)
    calls = [{'id': f'call-{i}', 'type': 'function',
              'function': {'name': 'search_beekeeping_knowledge', 'arguments': '{"query":"通风"}'}}
             for i in range(6)]
    messages = chat({'question': '冬季通风'})
    messages.append({'role': 'assistant', 'content': None, 'tool_calls': calls})
    for call in calls:
        messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps({
            'ok': True, 'result': {'hits': [{'id': 'source', 'content': '完整资料' * 100}]}
        }, ensure_ascii=False)})
    original = deepcopy(messages)
    prepared = prepare_chat(messages)
    assert messages == original
    assert [m['role'] for m in prepared.messages] == [m['role'] for m in messages]
    assert prepared.messages[2]['tool_calls'] == calls
    assert [m['tool_call_id'] for m in prepared.messages[3:]] == [call['id'] for call in calls]
    assert all(isinstance(json.loads(m['content']), dict) for m in prepared.messages[3:])
    assert prepared.report['tool_results_reduced'] > 0
    assert estimate_input_tokens(prepared.messages) <= prepared.report['input_budget']


def test_tool_definitions_count_toward_budget(small_window):
    tools = [{'type': 'function', 'function': {'name': 'tool', 'description': '工具说明' * 2000}}]
    with pytest.raises(ContextBudgetError):
        prepare_chat(chat({'question': '短问题'}), tools)


def test_previous_user_requirements_cannot_be_silently_dropped(small_window):
    context = {'question': '继续计划', 'conversation': [
        {'role': 'user', 'content': '之前明确提出的限制' * 3000}
    ]}
    with pytest.raises(ContextBudgetError):
        prepare_chat(chat(context))


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['route', 'advisor', 'reviewer', 'coordinator'])
async def test_required_context_overflow_never_sends_http(small_window, monkeypatch, role):
    def unexpected_http(**_):
        pytest.fail('Over-budget required context must be rejected before HTTP')

    monkeypatch.setattr('app.llm.httpx.AsyncClient', unexpected_http)
    large = '必须保留的内容' * 5000
    with pytest.raises(ContextBudgetError) as caught:
        if role == 'route':
            await route_model(large, {'city': '杭州'})
        elif role == 'advisor':
            await generate_advice({'question': large}, lambda _: None)
        elif role == 'reviewer':
            await review_advice({'draft': {'citation_ids': ['source']}, 'evidence': [{'id': 'source', 'content': large}]})
        else:
            await coordinate_tools(chat({'question': large}), [])
    assert caught.value.reason == 'context_budget_exceeded'


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['route', 'advisor', 'reviewer', 'coordinator'])
async def test_every_model_request_has_output_reserve_and_budget_metrics(monkeypatch, role):
    actual_client = httpx.AsyncClient
    seen = []
    advice = Advice(answer='核对设备。', citation_ids=['source']).model_dump()

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert estimate_input_tokens(body['messages'], body.get('tools')) + body['max_tokens'] + settings.context_safety_tokens <= settings.model_context_window
        if role == 'advisor':
            chunks = [
                {'choices': [{'delta': {'content': json.dumps(advice, ensure_ascii=False)}}]},
                {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 7}},
            ]
            content = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            return httpx.Response(200, content=content, headers={'content-type': 'text/event-stream'})
        if role == 'route':
            output = {'intent': 'general', 'need_weather': False}
        elif role == 'reviewer':
            output = {'verdict': 'approve', 'summary': '符合资料', 'issues': []}
        else:
            return httpx.Response(200, json={'choices': [{'message': {'content': '已收集'}}], 'usage': {'total_tokens': 7}})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}], 'usage': {'total_tokens': 7}})

    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kwargs: actual_client(transport=httpx.MockTransport(handle), **kwargs))
    context = {'question': '准备装备', 'profile': {'city': '杭州'},
               'conversation': [{'role': 'assistant', 'content': '历史回答' * 10000}],
               'evidence': [{'id': 'source', 'content': '核对设备。'}]}
    if role == 'route':
        _, usage = await route_model(context['question'], context['profile'])
    elif role == 'advisor':
        _, usage = await generate_advice(context, lambda _: None)
    elif role == 'reviewer':
        _, usage = await review_advice({**context, 'draft': advice})
    else:
        _, usage = await coordinate_tools(chat(context), [])
    assert len(seen) == 1
    assert usage['total_tokens'] == 7
    assert usage['context_budget']['reserved_output'] == seen[0]['max_tokens']
    if role != 'route':
        assert usage['context_budget']['omitted_records']['conversation'] == 1


def test_invalid_window_configuration_is_rejected():
    with pytest.raises(ValueError, match='MODEL_CONTEXT_WINDOW'):
        Settings(_env_file=None, model_context_window=4096, model_max_output_tokens=4096, context_safety_tokens=128)


def test_budget_overflow_uses_existing_workflow_fallback(client, headers, small_window, monkeypatch):
    from test_workflow import send, session

    async def route(*_):
        return {'intent': 'general', 'need_weather': False}, {}

    def unexpected_http(**_):
        pytest.fail('Oversized advisor system prompt must not reach HTTP')

    monkeypatch.setattr(settings, 'model_api_key', 'test-only')
    monkeypatch.setattr('app.agent.route_model', route)
    monkeypatch.setattr('app.llm.httpx.AsyncClient', unexpected_http)
    result = send(client, headers, session(client, headers), '防护面罩有哪些作用？')
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['mode'] == 'model_fallback'
    assert advice['citations']
    assert '输入限制' in advice['warning']
    run = next(run for run in client.get('/api/runs', headers=headers).json()
               if any(entry.get('failure_reason') == 'context_budget_exceeded' for entry in run['trace']))
    assert run['metrics']['usage'] == {}
