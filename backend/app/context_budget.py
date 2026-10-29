"""Deterministic prompt selection; original state and persisted records stay intact.

The provider is configurable, so UTF-8 bytes plus framing overhead are used as a
conservative estimate for byte-based tokenizers, not an exact provider token count.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import re

from .config import settings


CONTEXT_RULES = ('\ncontext_selection 表示部分记录未纳入当前上下文，不能据此认定它们不存在。'
                 'history_summary 仅保留任务标识与状态，省略的反馈不代表没有反馈。'
                 '仅依据实际提供的完整资料生成或审查结论；必要依据缺失时明确说明。')


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def estimate_input_tokens(messages, tools=None):
    payload = {'messages': messages}
    if tools:
        payload['tools'] = tools
    return len(json_text(payload).encode('utf-8')) + 128 + 32 * len(messages) + 64 * len(tools or [])


class ContextBudgetError(ValueError):
    reason = 'context_budget_exceeded'

    def __init__(self, report):
        super().__init__('Required context exceeds the configured input budget')
        self.usage = {'context_budget': report}


@dataclass
class PreparedChat:
    messages: list
    max_output_tokens: int
    report: dict

    def with_usage(self, usage):
        return {**usage, 'context_budget': self.report}


def _note_omission(data, key):
    selection = data.setdefault('context_selection', {})
    omitted = selection.setdefault('omitted_records', {})
    omitted[key] = omitted.get(key, 0) + 1


def _tool_record(payload):
    result = payload.get('result')
    if not isinstance(result, dict):
        return None
    candidates = [(key, result[key]) for key in ('hits', 'tasks', 'days')
                  if isinstance(result.get(key), list) and result[key]]
    return max(candidates, key=lambda item: len(json_text(item[1]).encode('utf-8')), default=None)


def bound_tool_payload(payload, limit):
    """Remove whole records, keeping JSON and each retained source unmodified."""
    selected = deepcopy(payload)
    while len(json_text(selected).encode('utf-8')) + 32 > limit:
        candidate = _tool_record(selected)
        if candidate is None:
            selected = {'ok': False, 'error': payload.get('error') or 'tool_result_too_large',
                        'result': {}, 'context_selection': {'result_omitted': True}}
            if len(json_text(selected).encode('utf-8')) + 32 > limit:
                raise ContextBudgetError({'tool_result_limit': limit})
            break
        key, rows = candidate
        rows.pop()
        _note_omission(selected, key)
    return selected


def _terms(text):
    words = set(re.findall(r'[a-z0-9]+', text.lower()))
    for phrase in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return words


def _reduce_context(data):
    conversation = data.get('conversation', [])
    for index, message in enumerate(conversation):
        # Keep previous user requests verbatim; the current question is already pinned.
        if message.get('role') == 'assistant' or message.get('content') == data.get('question'):
            conversation.pop(index)
            _note_omission(data, 'conversation')
            return True
    history = data.get('history', [])
    if history:
        query = _terms((data.get('question') or '') + ' ' + (data.get('clarification') or ''))
        # On equal relevance remove the oldest entry (history is newest first).
        index = min(range(len(history)), key=lambda i: (len(query & _terms(json_text(history[i]))), -i))
        task = history.pop(index)
        # Even omitted task details must not hide that a task already exists/is done.
        data.setdefault('history_summary', []).append({
            key: task[key] for key in ('id', 'title', 'status', 'due_date', 'reported_at') if key in task
        })
        _note_omission(data, 'history')
        return True
    evidence = data.get('evidence', [])
    if isinstance(evidence, list):
        cited = set()
        for key in ('draft', 'previous_draft'):
            cited.update((data.get(key) or {}).get('citation_ids', []))
        # Preserve all cited sources for review/revision, and at least one full source.
        if len(evidence) > 1:
            for index in range(len(evidence) - 1, -1, -1):
                if evidence[index].get('id') not in cited:
                    evidence.pop(index)
                    _note_omission(data, 'evidence')
                    return True
    return False


def prepare_chat(messages, tools=None, max_output_tokens=None):
    """Fit a copy of a chat request, or fail before HTTP if required data cannot fit.

    System messages, question, clarification, profile, user requirements, drafts,
    review feedback and tool-call IDs/arguments are never sliced or rewritten.
    """
    selected = deepcopy(messages)
    output = min(max_output_tokens or settings.model_max_output_tokens, settings.model_max_output_tokens)
    budget = settings.model_context_window - output - settings.context_safety_tokens
    report = {'estimator': 'utf8_bytes_with_framing', 'window': settings.model_context_window,
              'input_budget': budget, 'reserved_output': output,
              'safety_tokens': settings.context_safety_tokens,
              'original_input_estimate': estimate_input_tokens(messages, tools),
              'tool_results_reduced': 0}
    if budget <= 0:
        raise ContextBudgetError(report)
    contexts, tool_views, reduced_tools = {}, {}, set()
    for index, message in enumerate(selected):
        if message.get('role') not in ('user', 'tool') or not isinstance(message.get('content'), str):
            continue
        try:
            payload = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if message['role'] == 'tool':
            bounded = bound_tool_payload(payload, settings.context_tool_result_tokens)
            if bounded != payload:
                reduced_tools.add(index)
                report['tool_results_reduced'] = len(reduced_tools)
            tool_views[index] = bounded
            message['content'] = json_text(bounded)
        else:
            contexts[index] = payload
            message['content'] = json_text(payload)

    while estimate_input_tokens(selected, tools) > budget:
        for index, payload in contexts.items():
            if _reduce_context(payload):
                selected[index]['content'] = json_text(payload)
                break
        else:
            # Shrink results without removing assistant/tool pairs or changing call IDs.
            for index, payload in tool_views.items():
                size = len(json_text(payload).encode('utf-8')) + 32
                if size <= 256:
                    continue
                tool_views[index] = bound_tool_payload(payload, max(256, size // 2))
                selected[index]['content'] = json_text(tool_views[index])
                reduced_tools.add(index)
                report['tool_results_reduced'] = len(reduced_tools)
                break
            else:
                report['input_estimate'] = estimate_input_tokens(selected, tools)
                raise ContextBudgetError(report)

    report['input_estimate'] = estimate_input_tokens(selected, tools)
    report['omitted_records'] = {}
    for payload in contexts.values():
        for key, count in payload.get('context_selection', {}).get('omitted_records', {}).items():
            report['omitted_records'][key] = report['omitted_records'].get(key, 0) + count
    report['omitted_tool_records'] = {}
    for payload in tool_views.values():
        for key, count in payload.get('context_selection', {}).get('omitted_records', {}).items():
            report['omitted_tool_records'][key] = report['omitted_tool_records'].get(key, 0) + count
    return PreparedChat(selected, output, report)
