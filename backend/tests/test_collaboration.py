import asyncio
import json

import httpx
import pytest

from app.collaboration import collect_context
from app.config import settings
from app.llm import coordinate_tools, review_advice
from app.schemas import Advice
from test_workflow import events, send, session


def call(name, arguments=None, call_id='call-1'):
    return {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments or {})}}


def state():
    return {'question': '冬季通风需要注意什么？', 'profile': {'city': '杭州'},
            'route': {'intent': 'general', 'need_weather': False},
            'history': [{'id': 'owned-task', 'status': 'done'}], 'trace': [], 'usage': {}}


class RetrievalStub:
    def __init__(self):
        self.queries = []

    async def search(self, query, top_k=5):
        self.queries.append(query)
        return {'hits': [{'id': 'evidence', 'title': '资料', 'content': '保留通风。'}], 'mode': 'test', 'warning': ''}


@pytest.mark.asyncio
async def test_tool_results_return_to_coordinator_and_preserve_season(monkeypatch):
    seen = []

    async def coordinator(messages, tools):
        seen.append(messages.copy())
        if len(seen) == 1:
            return {'role': 'assistant', 'content': None, 'tool_calls': [call('get_recent_tasks')]}, {'total_tokens': 4}
        if len(seen) == 2:
            result = json.loads(messages[-1]['content'])
            assert messages[-1]['tool_call_id'] == 'call-1'
            assert result['result']['tasks'][0]['id'] == 'owned-task'
            return {'role': 'assistant', 'content': None, 'tool_calls': [call('search_beekeeping_knowledge', {'query': '通风'}, 'call-2')]}, {'total_tokens': 5}
        assert messages[-1]['tool_call_id'] == 'call-2'
        assert json.loads(messages[-1]['content'])['result']['hits'][0]['id'] == 'evidence'
        return {'role': 'assistant', 'content': '已获取足够资料'}, {'total_tokens': 6}

    monkeypatch.setattr('app.collaboration.coordinate_tools', coordinator)
    retriever = RetrievalStub()
    output = await collect_context(state(), retriever, lambda event: None)
    assert output['coordination']['rounds'] == 3
    assert output['coordination']['tool_attempts'] == 2
    assert output['usage']['total_tokens'] == 15
    assert '冬季' in retriever.queries[0]
    assert not any(entry.get('fallback') for entry in output['trace'])


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,arguments,error', [
    ('save_plan', {}, 'unknown_tool'),
    ('get_recent_tasks', {'user_id': 'another-user'}, 'invalid_arguments'),
    ('get_keeper_profile', {'user_id': 'another-user'}, 'invalid_arguments'),
    ('search_beekeeping_knowledge', {'query': '通风', 'top_k': 999}, 'invalid_arguments'),
    ('get_weather', {'city': '杭州', 'url': 'http://example.com'}, 'invalid_arguments'),
])
async def test_coordinator_rejects_unregistered_or_cross_user_arguments(monkeypatch, tool, arguments, error):
    iterations = 0

    async def coordinator(messages, tools):
        nonlocal iterations
        iterations += 1
        if iterations == 1:
            return {'role': 'assistant', 'content': None, 'tool_calls': [call(tool, arguments)]}, {}
        assert json.loads(messages[-1]['content'])['error'] == error
        return {'role': 'assistant', 'content': '停止'}, {}

    monkeypatch.setattr('app.collaboration.coordinate_tools', coordinator)
    output = await collect_context(state(), RetrievalStub(), lambda event: None)
    assert any(entry.get('error') == error for entry in output['trace'])
    assert output['evidence']['hits']
    assert any(entry.get('fallback') for entry in output['trace'])


@pytest.mark.asyncio
async def test_duplicate_calls_and_call_budget_are_bounded(monkeypatch):
    async def coordinator(messages, tools):
        return {'role': 'assistant', 'content': None, 'tool_calls': [
            call('search_beekeeping_knowledge', {'query': '通风'}, 'a'),
            call('search_beekeeping_knowledge', {'query': '通风'}, 'b'),
            call('get_recent_tasks', {}, 'c'),
        ]}, {}

    monkeypatch.setattr(settings, 'agent_max_tool_calls', 2)
    monkeypatch.setattr('app.collaboration.coordinate_tools', coordinator)
    retriever = RetrievalStub()
    output = await collect_context(state(), retriever, lambda event: None)
    assert len(retriever.queries) == 1
    assert output['coordination']['tool_attempts'] == 2
    assert any(entry.get('error') == 'duplicate_call' for entry in output['trace'])
    assert any(entry.get('error') == 'budget_exhausted' for entry in output['trace'])


@pytest.mark.asyncio
async def test_tool_timeout_and_empty_evidence_are_explicit(monkeypatch):
    class SlowRetriever:
        async def search(self, *_args, **_kwargs):
            await asyncio.sleep(0.1)

    async def coordinator(messages, tools):
        return {'role': 'assistant', 'content': None, 'tool_calls': [call('search_beekeeping_knowledge', {'query': '通风'})]}, {}

    monkeypatch.setattr(settings, 'agent_tool_timeout', 0.01)
    monkeypatch.setattr(settings, 'agent_max_tool_rounds', 1)
    monkeypatch.setattr('app.collaboration.coordinate_tools', coordinator)
    output = await collect_context(state(), SlowRetriever(), lambda event: None)
    assert any(entry.get('error') == 'tool_timeout' for entry in output['trace'])
    assert output['evidence']['hits'] == []
    assert output['coordination']['degraded']


@pytest.mark.asyncio
async def test_coordinator_failure_uses_fixed_retrieval(monkeypatch):
    async def unavailable(*_):
        raise httpx.ConnectError('not available')

    monkeypatch.setattr('app.collaboration.coordinate_tools', unavailable)
    output = await collect_context(state(), RetrievalStub(), lambda event: None)
    assert output['coordination']['degraded']
    assert output['evidence']['hits']
    assert '协调员' in output['warning']


def install_agents(monkeypatch, intent='plan', verdicts=None):
    monkeypatch.setattr(settings, 'model_api_key', 'test-only')
    monkeypatch.setattr(settings, 'agent_architecture', 'multi')
    counts = {'advisor': 0, 'reviewer': 0, 'coordinator': 0}
    contexts = []
    verdicts = iter(verdicts or ['approve'])

    async def route(*_):
        return {'intent': intent, 'need_weather': False}, {'total_tokens': 2}

    async def coordinate(messages, tools):
        counts['coordinator'] += 1
        if not any(message['role'] == 'tool' for message in messages):
            return {'role': 'assistant', 'content': None, 'tool_calls': [call('search_beekeeping_knowledge', {'query': '防护面罩'})]}, {'total_tokens': 3}
        return {'role': 'assistant', 'content': '资料已获取'}, {'total_tokens': 3}

    async def advisor(context, writer):
        counts['advisor'] += 1
        contexts.append(context)
        writer({'type': 'text_delta', 'delta': '未审查草稿'})
        return Advice(answer='按资料核对防护面罩。', citation_ids=['protective-equipment'],
                      actions=[{'title': '核对防护面罩', 'detail': '核对面罩是否完整。'}]).model_dump(), {'total_tokens': 5}

    async def reviewer(context):
        counts['reviewer'] += 1
        verdict = next(verdicts)
        return {'verdict': verdict, 'summary': '检查完成', 'issues': [] if verdict == 'approve' else [{'code': 'duplicate_task', 'detail': '不要重复已完成的准备任务。'}]}, {'total_tokens': 7}

    monkeypatch.setattr('app.agent.route_model', route)
    monkeypatch.setattr('app.collaboration.coordinate_tools', coordinate)
    monkeypatch.setattr('app.agent.generate_advice', advisor)
    monkeypatch.setattr('app.agent.review_advice', reviewer)
    return counts, contexts


def multi_query(client, headers):
    client.put('/api/profile', headers=headers, json={'city': '杭州', 'stage': 'preparing'})
    sid = session(client, headers)
    return sid, send(client, headers, sid, '请列一份防护装备准备计划')


def test_multi_agents_review_before_confirmation_and_persist_trace(client, headers, monkeypatch):
    counts, _ = install_agents(monkeypatch)
    sid, result = multi_query(client, headers)
    assert counts == {'advisor': 1, 'reviewer': 1, 'coordinator': 2}
    assert not any(event['type'] == 'text_delta' for event in result)
    assert any(event['type'] == 'model_token' for event in result)
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['architecture'] == 'multi'
    assert advice['review']['status'] == 'passed'
    assert any(event['type'] == 'awaiting' and event['kind'] == 'confirm' for event in result)
    saved = events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': advice['draft_id']}))
    assert any(event['type'] == 'plan_saved' for event in saved)
    run = next(run for run in client.get('/api/runs', headers=headers).json() if run['session_id'] == sid and run['status'] == 'waiting_confirm')
    assert run['metrics']['usage']['total_tokens'] == 20
    assert run['metrics']['first_token_ms'] is not None
    assert {entry.get('agent') for entry in run['trace']} >= {'coordinator', 'advisor', 'reviewer'}


def test_review_revision_gets_feedback_and_second_review(client, headers, monkeypatch):
    counts, contexts = install_agents(monkeypatch, verdicts=['revise', 'approve'])
    _, result = multi_query(client, headers)
    assert counts['advisor'] == counts['reviewer'] == 2
    assert contexts[1]['review_feedback']['issues'][0]['code'] == 'duplicate_task'
    assert contexts[1]['previous_draft']['answer']
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['review']['status'] == 'passed'
    assert advice['review']['revision_count'] == 1


@pytest.mark.parametrize('verdicts', [['revise', 'revise'], ['abstain']])
def test_unapproved_draft_is_never_savable(client, headers, monkeypatch, verdicts):
    counts, _ = install_agents(monkeypatch, verdicts=verdicts)
    _, result = multi_query(client, headers)
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['mode'] == 'review_fallback'
    assert not advice['actions']
    assert not any(event['type'] == 'awaiting' for event in result)
    assert counts['advisor'] <= 2 and counts['reviewer'] <= 2


def test_review_outage_is_explicit_and_blocks_draft(client, headers, monkeypatch):
    install_agents(monkeypatch)

    async def unavailable(*_):
        raise httpx.ConnectError('not available')

    monkeypatch.setattr('app.agent.review_advice', unavailable)
    _, result = multi_query(client, headers)
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['review']['status'] == 'unavailable'
    assert advice['mode'] == 'review_fallback'
    assert not advice['actions']


def test_simple_question_skips_review_and_new_turn_clears_old_review(client, headers, monkeypatch):
    counts, _ = install_agents(monkeypatch, intent='general')
    sid, result = multi_query(client, headers)
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['review']['status'] == 'not_required'
    assert advice['workflow_path'] == 'direct'
    assert counts['coordinator'] == 0
    assert counts['reviewer'] == 0
    monkeypatch.setattr(settings, 'agent_architecture', 'single')
    result = send(client, headers, sid, '面罩的作用是什么？')
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['architecture'] == 'single'
    assert advice['coordination'] == {}
    assert advice['review']['status'] == 'not_required'


@pytest.mark.asyncio
async def test_native_tool_call_protocol_with_mock_transport(monkeypatch):
    actual_client = httpx.AsyncClient

    def handle(request):
        body = json.loads(request.content)
        assert body['tool_choice'] == 'auto'
        assert body['tools'][0]['function']['name'] == 'get_recent_tasks'
        assert 'response_format' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': None, 'tool_calls': [call('get_recent_tasks')]}}], 'usage': {'total_tokens': 9}})

    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kwargs: actual_client(transport=httpx.MockTransport(handle), **kwargs))
    message, usage = await coordinate_tools([{'role': 'user', 'content': '回访'}], [{'type': 'function', 'function': {'name': 'get_recent_tasks'}}])
    assert message['tool_calls'][0]['id'] == 'call-1'
    assert usage['total_tokens'] == 9


@pytest.mark.asyncio
async def test_inconsistent_reviewer_json_is_rejected(monkeypatch):
    actual_client = httpx.AsyncClient

    def handle(request):
        output = {'verdict': 'approve', 'summary': '通过', 'issues': [{'code': 'unsupported_claim', 'detail': '缺少支持'}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})

    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kwargs: actual_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ValueError):
        await review_advice({'draft': {}, 'evidence': []})


@pytest.mark.asyncio
async def test_truncated_review_preserves_usage_and_deepseek_disables_thinking(monkeypatch):
    from app.llm import ModelOutputError
    actual_client = httpx.AsyncClient
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com/v1')
    monkeypatch.setattr(settings, 'model_thinking_enabled', False)

    def handle(request):
        assert json.loads(request.content)['thinking'] == {'type': 'disabled'}
        return httpx.Response(200, json={'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}], 'usage': {'total_tokens': 1800}})

    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kwargs: actual_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ModelOutputError) as caught:
        await review_advice({'draft': {}, 'evidence': []})
    assert caught.value.reason == 'output_truncated'
    assert caught.value.usage['total_tokens'] == 1800


def test_failed_review_usage_is_included_in_run(client, headers, monkeypatch):
    from app.llm import ModelOutputError
    install_agents(monkeypatch)

    async def truncated(*_):
        raise ModelOutputError('output_truncated', {'total_tokens': 1800})

    monkeypatch.setattr('app.agent.review_advice', truncated)
    sid, result = multi_query(client, headers)
    advice = next(event['advice'] for event in result if event['type'] == 'advice')
    assert advice['review']['failure_reason'] == 'output_truncated'
    run = next(run for run in client.get('/api/runs', headers=headers).json() if run['session_id'] == sid)
    assert run['metrics']['usage']['total_tokens'] == 1813
