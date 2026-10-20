import json

import httpx
import pytest

from app.config import settings
from app.rag import Retriever
from app.schemas import Advice


@pytest.mark.asyncio
async def test_retrieval_and_metadata(client):
    result = await Retriever().search('新手需要哪些防护装备和面罩？')
    assert 'protective-equipment' in [h['id'] for h in result['hits']]
    assert result['mode'] == 'TF-IDF（本地词频向量演示）'
    assert all(h['section'] and h['review_status'] for h in result['hits'])


@pytest.mark.asyncio
@pytest.mark.parametrize('query,expected,excluded', [
    ('冬季给蜂箱保温时还要留通风吗？', ['cn-winter-ventilation'], ['cn-summer-shade', 'cn-spring-cold-brood']),
    ('冬季保温和夏季遮阴通风有什么不同？', ['cn-winter-ventilation', 'cn-summer-shade'], []),
])
async def test_seasonal_applicability_and_comparison(client, query, expected, excluded):
    result = await Retriever().search(query)
    ids = {hit['id'] for hit in result['hits']}
    assert set(expected) <= ids
    assert not (set(excluded) & ids)


@pytest.mark.asyncio
async def test_weather_real_schema_with_mock_transport(monkeypatch):
    from app.weather import get_weather
    real_client = httpx.AsyncClient
    def handle(request):
        assert request.url.params['timezone'] == 'Asia/Shanghai'
        return httpx.Response(200, json={'daily': {'time': ['2026-10-06'], 'temperature_2m_max': [25], 'temperature_2m_min': [18], 'precipitation_probability_max': [40], 'wind_speed_10m_max': [12]}})
    monkeypatch.setattr(settings, 'weather_enabled', True)
    monkeypatch.setattr('app.weather.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    result = await get_weather('杭州')
    assert result['available'] is True
    assert result['units']['wind'] == 'km/h'
    assert result['days'][0]['date'] == '2026-10-06'
    assert result['provider'] == 'Open-Meteo'


@pytest.mark.asyncio
async def test_actual_streaming_client_parses_json_and_usage(monkeypatch):
    from app.llm import generate_advice
    output = Advice(answer='你好\n请先记录情况', citation_ids=['start-preparation']).model_dump()
    raw = json.dumps(output, ensure_ascii=False)
    frames = []
    for token in [raw[:14], raw[14:25], raw[25:]]:
        frames.append('data: ' + json.dumps({'choices': [{'delta': {'content': token}}]}, ensure_ascii=False) + '\n\n')
    frames.append('data: ' + json.dumps({'choices': [], 'usage': {'total_tokens': 50}}) + '\n\n')
    frames.append('data: [DONE]\n\n')
    real_client = httpx.AsyncClient
    def handle(request):
        body = json.loads(request.content)
        assert body['stream'] is True
        return httpx.Response(200, text=''.join(frames), headers={'content-type': 'text/event-stream'})
    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    emitted = []
    advice, usage = await generate_advice({'evidence': []}, emitted.append)
    assert advice['answer'] == output['answer']
    assert ''.join(e['delta'] for e in emitted) == output['answer']
    assert usage['total_tokens'] == 50
