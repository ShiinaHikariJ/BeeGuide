import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.llm import partial_answer
from test_workflow import draft, events, send, session


def test_temporary_profile_remains_in_same_conversation(client, headers):
    sid = session(client, headers)
    send(client, headers, sid, '我完全不会养蜂，想先养一箱。')
    send(client, headers, sid, '杭州，准备开始')
    result = send(client, headers, sid, '请再列一份准备计划')
    assert not any(e['type'] == 'awaiting' and e['kind'] == 'clarify' for e in result)
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert '所在城市：杭州' in advice['known_facts']


def test_same_session_rejects_concurrent_turn(client, headers, monkeypatch):
    entered = threading.Event()
    async def slow_route(*_):
        entered.set()
        await asyncio.sleep(0.3)
        return {'intent': 'general', 'need_weather': False}, {}
    async def generate(*_):
        return {'answer': '防护资料说明', 'title': '防护', 'known_facts': [], 'conditions': [], 'actions': [], 'citation_ids': ['protective-equipment']}, {}
    monkeypatch.setattr(settings, 'model_api_key', 'test-only')
    monkeypatch.setattr('app.agent.route_model', slow_route)
    monkeypatch.setattr('app.agent.generate_advice', generate)
    sid = session(client, headers)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(send, client, headers, sid, '防护面罩')
        assert entered.wait(3)
        blocked = client.post(f'/api/sessions/{sid}/messages', headers=headers, json={'content': '第二个请求'})
        assert blocked.status_code == 409
        future.result()


def test_rejects_mismatched_draft(client, headers):
    sid, advice = draft(client, headers)
    assert client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': 'wrong'}).status_code == 409
    events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': advice['draft_id']}))


def test_unicode_surrogate_pair_is_not_emitted_incomplete():
    assert partial_answer('{"answer":"\\ud83d') == ''
    assert partial_answer('{"answer":"\\ud83d\\udc1d') == '🐝'


def test_schedule_requires_owner_and_valid_date(client, headers, second_headers):
    sid, advice = draft(client, headers)
    events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': advice['draft_id']}))
    task = client.get('/api/plans', headers=headers).json()[0]['tasks'][0]
    url = f'/api/tasks/{task["id"]}'
    assert client.put(url, headers=second_headers, json={'due_date': '2026-10-07'}).status_code == 404
    assert client.put(url, headers=headers, json={'due_date': '2026-99-99'}).status_code == 422
    assert client.put(url, headers=headers, json={'due_date': '2026-10-07'}).json()['due_date'] == '2026-10-07'


def test_user_correction_overrides_session_memory(client, headers):
    sid = session(client, headers)
    send(client, headers, sid, '我想先养一箱')
    send(client, headers, sid, '杭州，准备开始')
    client.put('/api/profile', headers=headers, json={'city': '成都', 'stage': 'keeping'})
    result = send(client, headers, sid, '请帮我制定准备计划')
    advice = next(e['advice'] for e in result if e['type'] == 'advice')
    assert '所在城市：成都' in advice['known_facts']
    assert '阶段：已有蜂群' in advice['known_facts']


def test_preparing_equipment_does_not_change_existing_keeper_stage():
    from app.agent import parse_profile
    assert parse_profile('我已有蜂群，想准备一些装备')['stage'] == 'keeping'
