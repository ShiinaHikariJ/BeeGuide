"""Read-only, bounded native tool calling for the coordinator role."""
import asyncio
import json
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import now, settings
from .llm import COORDINATOR_PROMPT, coordinate_tools
from .weather import get_weather


class NoArguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SearchArguments(NoArguments):
    query: str = Field(min_length=1, max_length=600)
    top_k: int = Field(default=5, ge=1, le=8)


class WeatherArguments(NoArguments):
    city: str = Field(min_length=1, max_length=80)


ARGUMENTS = {
    'get_keeper_profile': NoArguments,
    'search_beekeeping_knowledge': SearchArguments,
    'get_weather': WeatherArguments,
    'get_recent_tasks': NoArguments,
}
DESCRIPTIONS = {
    'get_keeper_profile': '获取当前账号档案与本会话补充。没有 user_id 参数。',
    'search_beekeeping_knowledge': '检索带出处和适用条件的养蜂资料。query 保留问题指定的季节、蜂种。',
    'get_weather': '获取 city 的近期天气预报，工具返回日期和单位。天气不能证明蜂群健康。',
    'get_recent_tasks': '获取当前账号近期任务、ID、完成状态、日期和用户反馈。没有 user_id 参数。',
}
TOOLS = [{'type': 'function', 'function': {'name': name, 'description': DESCRIPTIONS[name], 'parameters': schema.model_json_schema()}} for name, schema in ARGUMENTS.items()]


def add_usage(current, extra):
    return {key: current.get(key, 0) + extra.get(key, 0) for key in set(current) | set(extra)
            if isinstance(current.get(key, 0), (int, float)) and isinstance(extra.get(key, 0), (int, float))}


async def collect_context(state, retriever, writer):
    """Identity and task access come from the server-owned state, never model args."""
    trace, usage = list(state.get('trace', [])), dict(state.get('usage', {}))
    evidence = {'hits': [], 'mode': '尚未检索', 'warning': ''}
    weather, warnings, seen = {}, [], set()
    attempts, rounds, searched, weather_attempted = 0, 0, False, False
    deadline = time.perf_counter() + settings.agent_coordination_timeout
    messages = [
        {'role': 'system', 'content': COORDINATOR_PROMPT},
        {'role': 'user', 'content': json.dumps({'question': state['question'], 'clarification': state.get('clarification', ''),
          'profile': state['profile'], 'route': state['route'], 'current_date': now().date().isoformat(),
          'has_recent_tasks': bool(state.get('history'))}, ensure_ascii=False)},
    ]

    def record(tool, summary, started, **details):
        entry = {'tool': tool, 'agent': 'coordinator', 'summary': summary,
                 'duration_ms': round((time.perf_counter() - started) * 1000), **details}
        trace.append(entry)
        writer({'type': 'tool_result', **entry})

    async def execute(name, args):
        nonlocal evidence, weather, searched, weather_attempted
        if name == 'get_keeper_profile':
            return {'profile': state['profile'], 'source': '档案与本次会话补充'}
        if name == 'get_recent_tasks':
            return {'tasks': state.get('history', []), 'source': '用户任务与执行报告'}
        if name == 'search_beekeeping_knowledge':
            # Preserve explicit seasonal intent even if the model rewrites the query.
            from .rag import requested_seasons
            seasons = ' '.join(sorted(requested_seasons(state['question'])))
            result = await retriever.search(args.query + ' ' + seasons, top_k=args.top_k)
            evidence, searched = result, True
            return result
        weather_attempted = True
        weather = await get_weather(args.city)
        return weather

    for round_number in range(1, settings.agent_max_tool_rounds + 1):
        if attempts >= settings.agent_max_tool_calls or time.perf_counter() >= deadline:
            warnings.append('协调预算已达到上限，使用已收集的信息。')
            break
        started = time.perf_counter()
        try:
            assistant, extra = await asyncio.wait_for(coordinate_tools(messages, TOOLS), timeout=max(0.01, deadline - started))
            usage = add_usage(usage, extra)
            rounds = round_number
            calls = assistant.get('tool_calls', [])
            record('coordinate_tools', f'协调员第 {round_number} 轮：' + (f'提出 {len(calls)} 个工具请求' if calls else '结束信息收集'),
                   started, round=round_number, success=True, usage=extra)
            messages.append(assistant)
            if not calls:
                break
        except (httpx.HTTPError, ValueError, KeyError, TypeError, asyncio.TimeoutError) as error:
            failed_usage = getattr(error, 'usage', {})
            usage = add_usage(usage, failed_usage)
            record('coordinate_tools', '协调员调用失败，切换到固定工具补足', started, round=round_number, success=False,
                   usage=failed_usage, failure_reason=getattr(error, 'reason', type(error).__name__))
            warnings.append('协调员暂不可用，已使用固定工具补足资料。')
            break
        for call in calls:
            started = time.perf_counter()
            name = call['function']['name']
            error, args, result = '', {}, {}
            if attempts >= settings.agent_max_tool_calls or started >= deadline:
                error = 'budget_exhausted'
            else:
                attempts += 1
                try:
                    schema = ARGUMENTS.get(name)
                    if schema is None:
                        error = 'unknown_tool'
                    else:
                        validated = schema.model_validate_json(call['function']['arguments'])
                        args = validated.model_dump()
                        signature = name + ':' + json.dumps(args, ensure_ascii=False, sort_keys=True)
                        if signature in seen:
                            error = 'duplicate_call'
                        else:
                            seen.add(signature)
                            result = await asyncio.wait_for(execute(name, validated), timeout=min(settings.agent_tool_timeout, max(0.01, deadline - started)))
                except (ValidationError, ValueError, KeyError, TypeError):
                    error = 'invalid_arguments'
                except asyncio.TimeoutError:
                    error = 'tool_timeout'
                except httpx.HTTPError:
                    error = 'tool_unavailable'
            if not error and name == 'get_weather' and not result.get('available'):
                error = 'weather_unavailable'
            payload = {'ok': not bool(error), **({'error': error, 'result': result} if error else {'result': result})}
            summary = {'get_keeper_profile': '已获取当前账号档案', 'get_recent_tasks': '已获取当前账号近期任务',
                       'search_beekeeping_knowledge': f'检索命中 {len(result.get("hits", []))} 条资料', 'get_weather': '已查询天气预报'}.get(name, '拒绝未注册工具')
            record(name[:100], ('工具未成功：' + error) if error else summary, started, round=round_number,
                   success=not bool(error), error=error, arguments=args,
                   citation_ids=[hit['id'] for hit in result.get('hits', [])], retrieval_mode=result.get('mode'), warning=result.get('warning', ''))
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(payload, ensure_ascii=False)})
    else:
        warnings.append('协调轮数已达到上限，使用已收集的信息。')

    # Fixed fallbacks also have independent timeouts; no extra model loop is opened.
    if not searched:
        started = time.perf_counter()
        try:
            evidence = await asyncio.wait_for(retriever.search(state['question']), timeout=settings.agent_tool_timeout)
            record('search_beekeeping_knowledge', '系统补足必要的依据检索', started, success=True, fallback=True,
                   citation_ids=[hit['id'] for hit in evidence['hits']], retrieval_mode=evidence['mode'])
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            evidence = {'hits': [], 'mode': '检索不可用', 'warning': '资料检索失败或超时。'}
            record('search_beekeeping_knowledge', '系统补足检索失败或超时', started, success=False, fallback=True)
    if state['route']['need_weather'] and not weather_attempted:
        started = time.perf_counter()
        try:
            city = state['profile'].get('city')
            weather = await asyncio.wait_for(get_weather(city), timeout=settings.agent_tool_timeout) if city else {'available': False, 'reason': '未提供城市。'}
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            weather = {'available': False, 'reason': '天气服务失败或超时。'}
        record('get_weather', '系统补足天气查询' if weather.get('available') else '天气不可用，保留未知', started,
               success=bool(weather.get('available')), fallback=True)
    warning = ' '.join(filter(None, [state.get('warning', ''), *warnings, evidence.get('warning', '')]))
    return {'evidence': evidence, 'weather': weather, 'trace': trace, 'usage': usage, 'warning': warning,
            'coordination': {'rounds': rounds, 'tool_attempts': attempts, 'protocol': 'tools/tool_calls',
                             'degraded': bool(warnings) or any(entry.get('success') is False or entry.get('fallback') for entry in trace if entry.get('agent') == 'coordinator')}}
