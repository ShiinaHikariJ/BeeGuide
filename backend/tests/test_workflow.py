import json

import httpx
import pytest

from app.config import settings
from app.llm import partial_answer


def events(response):
    assert response.status_code == 200, response.text
    result = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert not [e for e in result if e['type'] == 'error'], result
    return result


def session(client, headers):
    return client.post('/api/sessions', headers=headers).json()['id']


def send(client, headers, sid, content):
    return events(client.post(f'/api/sessions/{sid}/messages', headers=headers, json={'content': content}))


def draft(client, headers):
    client.put('/api/profile', headers=headers, json={'city': '杭州', 'stage': 'preparing'})
    sid = session(client, headers)
    response = send(client, headers, sid, '新手开始养蜂需要准备什么？')
    advice = next(e['advice'] for e in response if e['type'] == 'advice')
    return sid, advice


def test_auth_and_empty_message(client, headers):
    assert client.get('/api/plans').status_code == 401
    assert client.post('/api/auth/login', json={'email': 'demo@beeguide.local', 'password': 'wrong'}).status_code == 401
    sid = session(client, headers)
    assert client.post(f'/api/sessions/{sid}/messages', headers=headers, json={'content': '   '}).status_code == 422


def test_clarification_checkpoint_and_resume(client, headers):
    sid = session(client, headers)
    result = send(client, headers, sid, '我完全不会养蜂，想先养一箱。')
    assert next(e for e in result if e['type'] == 'awaiting')['kind'] == 'clarify'
    restored = client.get(f'/api/sessions/{sid}', headers=headers).json()
    assert restored['awaiting']['kind'] == 'clarify'
    assert restored['messages'][-1]['payload']['clarification'] is True
    result = send(client, headers, sid, '杭州，准备开始，中蜂')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert '所在城市：杭州' in advice['known_facts']
    assert advice['mode'] == 'local_evidence'
    assert advice['citations']
    # Conversation facts are temporary; they are not silently saved to the profile.
    assert not client.get('/api/me', headers=headers).json()['profile'].get('city')


def test_confirmation_idempotency_and_user_isolation(client, headers, second_headers):
    sid, advice = draft(client, headers)
    before = len(client.get('/api/plans', headers=headers).json())
    body = {'action': 'save', 'draft_id': advice['draft_id']}
    result = events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json=body))
    assert any(e['type'] == 'plan_saved' for e in result)
    events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json=body))
    plans = client.get('/api/plans', headers=headers).json()
    assert len(plans) == before + 1
    assert len(plans[0]['tasks']) == len(advice['actions'])
    assert client.get(f'/api/sessions/{sid}', headers=second_headers).status_code == 404
    assert client.post(f'/api/sessions/{sid}/resume', headers=second_headers, json=body).status_code == 404
    task = plans[0]['tasks'][0]
    assert client.post(f'/api/tasks/{task["id"]}/checkins', headers=second_headers, json={'report': 'x'}).status_code == 404


def test_skip_and_stale_draft(client, headers):
    sid, advice = draft(client, headers)
    send(client, headers, sid, '防护面罩有什么作用？')
    assert client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': advice['draft_id']}).status_code == 409


def test_feedback_is_persisted_and_reused(client, headers):
    sid, advice = draft(client, headers)
    events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': advice['draft_id']}))
    task = client.get('/api/plans', headers=headers).json()[0]['tasks'][0]
    for _ in range(2):
        assert client.post(f'/api/tasks/{task["id"]}/checkins', headers=headers, json={'report': '今天已经核对防护面罩，尚未购买蜂群。'}).status_code == 200
    stored = client.get('/api/plans', headers=headers).json()[0]['tasks'][0]
    assert stored['status'] == 'done'
    assert len(stored['reports']) == 1
    result = send(client, headers, sid, '我做完了上次的准备，想安排下一步。')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert any('历史任务' in fact for fact in advice['known_facts'])


def test_missing_evidence_and_weather_failure(client, headers):
    sid = session(client, headers)
    result = send(client, headers, sid, '如何修理汽车变速器？')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert advice['mode'] == 'insufficient_evidence'
    assert not advice['actions']
    client.put('/api/profile', headers=headers, json={'city': '杭州', 'stage': 'keeping'})
    result = send(client, headers, sid, '明天适合开箱检查吗？')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert advice['weather']['available'] is False
    assert not any('temperature_max' in h for h in advice['weather'])


def test_live_invalid_citation_falls_back(client, headers, monkeypatch):
    async def fake_route(*_):
        return {'intent': 'general', 'need_weather': False}, {}
    async def fake_generate(*_):
        return {'answer': '错误引用', 'title': 'x', 'known_facts': [], 'conditions': [], 'actions': [], 'citation_ids': ['invented']}, {}
    monkeypatch.setattr(settings, 'model_api_key', 'test-only')
    monkeypatch.setattr('app.agent.route_model', fake_route)
    monkeypatch.setattr('app.agent.generate_advice', fake_generate)
    result = send(client, headers, session(client, headers), '新手需要什么防护装备？')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert advice['mode'] == 'model_fallback'
    assert not any(c['id'] == 'invented' for c in advice['citations'])


def test_model_failure_is_explicit(client, headers, monkeypatch):
    async def fail(*_):
        raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(settings, 'model_api_key', 'test-only')
    monkeypatch.setattr('app.agent.route_model', fail)
    monkeypatch.setattr('app.agent.generate_advice', fail)
    result = send(client, headers, session(client, headers), '新手需要什么防护装备？')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert advice['mode'] == 'model_fallback'
    assert '模型' in advice['warning']


@pytest.mark.parametrize('raw,expected', [('{"answer":"你好', '你好'), ('{"answer":"第一行\\n第二行', '第一行\n第二行'), ('{"answer":"\\u4f60\\u597', '你'), ('{"title":"a","answer":"内容"}', '内容')])
def test_json_stream_prefix(raw, expected):
    assert partial_answer(raw) == expected
