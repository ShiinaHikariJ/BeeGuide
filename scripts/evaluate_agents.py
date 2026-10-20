"""Small paid live workflow comparison; requires an explicit --live flag."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Allow real paid model requests')
    parser.add_argument('--modes', nargs='+', choices=['single', 'multi'], default=['single', 'multi'])
    parser.add_argument('--case-ids', nargs='+')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to explicitly enable real model calls.')
    folder = Path(tempfile.mkdtemp(prefix='beeguide-agent-eval-'))
    os.environ['BEEGUIDE_DATA_DIR'] = str(folder)
    os.environ['DATABASE_URL'] = 'sqlite:///' + (folder / 'business.db').as_posix()
    os.environ['EMBEDDING_ENABLED'] = 'false'
    sys.path.insert(0, str(ROOT / 'backend'))
    from fastapi.testclient import TestClient
    from app.config import now, settings
    from app.database import SessionLocal
    from app.main import app
    from app.models import Plan, Task, Checkin

    if not settings.live_model:
        parser.error('Configure MODEL_API_KEY in local .env before a live evaluation.')
    dataset = json.loads((ROOT / 'eval' / 'agent.cases.json').read_text(encoding='utf-8'))
    cases = [case for case in dataset['cases'] if not args.case_ids or case['id'] in args.case_ids]
    if not cases:
        parser.error('No matching case IDs.')
    report = {'date': now().isoformat(), 'model': settings.model_name, 'base_url': settings.model_base_url,
              'dataset_version': dataset['version'], 'scope': dataset['scope'],
              'isolated_database': True, 'embedding_enabled': False, 'cases': [],
              'limits': {'tool_rounds': settings.agent_max_tool_rounds, 'tool_calls': settings.agent_max_tool_calls},
              'quality_evaluation': '内容支持关系、领域正确性与任务合理性待人工审阅，不用模型审查通过率代替准确率。'}
    output = ROOT / 'eval' / 'results.agents.local.json'

    def persist():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def events(response):
        response.raise_for_status()
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'email': 'demo@beeguide.local', 'password': 'bee2026'})
        login.raise_for_status()
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        owner = login.json()['user']['id']
        # Identical real task/feedback context is used by both modes. No plan writes during comparison.
        seed_session = client.post('/api/sessions', headers=headers).json()['id']
        with SessionLocal() as db:
            plan = Plan(user_id=owner, session_id=seed_session, draft_id='eval-completed-equipment', title='准备记录', advice={})
            db.add(plan)
            db.flush()
            task = Task(user_id=owner, plan_id=plan.id, position=0, title='核对防护面罩和手套',
                        detail='记录防护设备是否完整。', status='done')
            db.add(task)
            db.flush()
            db.add(Checkin(user_id=owner, task_id=task.id, report='已经核对面罩和手套，还没有购买蜂群。'))
            db.commit()
        saved_candidate = None
        for architecture in args.modes:
            settings.agent_architecture = architecture
            for case in cases:
                client.put('/api/profile', headers=headers, json={'city': '杭州', 'stage': case['stage'], 'species': '中蜂'}).raise_for_status()
                sid = client.post('/api/sessions', headers=headers).json()['id']
                started = time.perf_counter()
                row = {'id': case['id'], 'architecture': architecture, 'question': case['question']}
                try:
                    emitted = events(client.post(f'/api/sessions/{sid}/messages', headers=headers, json={'content': case['question']}))
                    advice = next((event['advice'] for event in emitted if event['type'] == 'advice'), None)
                    run = next(run for run in client.get('/api/runs', headers=headers).json() if run['session_id'] == sid)
                    traces = run['trace']
                    selected = {trace['tool'] for trace in traces if trace.get('agent') == 'coordinator' and trace.get('success') and not trace.get('fallback')}
                    called = {trace['tool'] for trace in traces}
                    information = called | ({'get_recent_tasks', 'get_keeper_profile'} if 'get_keeper_context' in called else set())
                    row.update({'duration_ms': round((time.perf_counter() - started) * 1000),
                                'mode': (advice or {}).get('mode', 'error'), 'status': run['status'],
                                'review_status': (advice or {}).get('review', {}).get('status', 'not_required'),
                                'revision_count': run['metrics'].get('revision_count', 0),
                                'coordination': (advice or {}).get('coordination', {}),
                                'usage': run['metrics']['usage'], 'first_token_ms': run['metrics']['first_token_ms'],
                                'stream_deltas': sum(event['type'] == 'text_delta' for event in emitted),
                                'tool_names': sorted(called), 'model_selected_tools': sorted(selected & set(case['required_tools'])),
                                'required_information_present': set(case['required_tools']) <= information,
                                'workflow_path': (advice or {}).get('workflow_path'),
                                'review_expected': case['review_expected'], 'advice': advice, 'trace': traces,
                                'error_events': [event for event in emitted if event['type'] == 'error']})
                    if architecture == 'multi' and case['id'] == 'plan' and advice and advice['mode'] == 'live_model' and advice['actions']:
                        saved_candidate = (sid, advice['draft_id'])
                except (httpx.HTTPError, ValueError, KeyError, StopIteration) as error:
                    row.update({'mode': 'error', 'error_type': type(error).__name__, 'duration_ms': round((time.perf_counter() - started) * 1000)})
                report['cases'].append(row)
                persist()
                print(json.dumps({key: row.get(key) for key in ['id', 'architecture', 'mode', 'workflow_path', 'review_status', 'revision_count', 'duration_ms', 'usage', 'required_information_present']}, ensure_ascii=True), flush=True)
        # Verify one live reviewed draft can still complete the original confirmation/feedback loop.
        if saved_candidate:
            sid, draft_id = saved_candidate
            saved = events(client.post(f'/api/sessions/{sid}/resume', headers=headers, json={'action': 'save', 'draft_id': draft_id}))
            plan_id = next((event['plan_id'] for event in saved if event['type'] == 'plan_saved'), None)
            report['confirmation'] = {'plan_saved': bool(plan_id)}
            if plan_id:
                plan = next(plan for plan in client.get('/api/plans', headers=headers).json() if plan['id'] == plan_id)
                task_id = plan['tasks'][0]['id']
                feedback = client.post(f'/api/tasks/{task_id}/checkins', headers=headers, json={'report': '已记录准备情况，待进一步核对本地场地。'})
                report['confirmation']['feedback_saved'] = feedback.status_code == 200
    report['summary'] = {}
    for architecture in args.modes:
        rows = [row for row in report['cases'] if row['architecture'] == architecture]
        report['summary'][architecture] = {'case_count': len(rows), 'live_model_count': sum(row['mode'] == 'live_model' for row in rows),
          'mean_duration_ms': round(sum(row['duration_ms'] for row in rows) / len(rows)),
          'total_tokens': sum(row.get('usage', {}).get('total_tokens', 0) for row in rows),
          'review_passed_count': sum(row.get('review_status') == 'passed' for row in rows),
          'revision_count': sum(row.get('revision_count', 0) for row in rows)}
    persist()
    print(json.dumps({'summary': report['summary'], 'confirmation': report.get('confirmation'), 'report': str(output)}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
