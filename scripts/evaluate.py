"""Offline retrieval/routing benchmark. No external model calls by default."""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from alembic import command
from alembic.config import Config
from app.agent import local_route
from app.config import settings
from app.rag import Retriever
from app.seed import seed


async def evaluate():
    settings.embedding_enabled = False
    command.upgrade(Config(str(ROOT / 'backend' / 'alembic.ini')), 'head')
    seed()
    cases = json.loads((ROOT / 'eval' / 'cases.json').read_text(encoding='utf-8'))
    domestic_cases = json.loads((ROOT / 'eval' / 'domestic.cases.json').read_text(encoding='utf-8'))
    retrieval_cases = [{**case, 'benchmark_split': 'legacy'} for case in cases['retrieval']] + [{**case, 'benchmark_split': 'domestic'} for case in domestic_cases['retrieval']]
    retriever = Retriever()
    results = {}
    for strategy in ['bm25', 'vector', 'hybrid']:
        rows = []
        for case in retrieval_cases:
            started = time.perf_counter()
            hits = (await retriever.search(case['query'], strategy=strategy))['hits']
            ids = [h['id'] for h in hits]
            relevant = set(case['relevant'])
            rows.append({**case, 'retrieved': ids, 'recall_at_5': len(relevant & set(ids)) / len(relevant), 'latency_ms': round((time.perf_counter() - started) * 1000, 2)})
        splits = {name: [row for row in rows if row['benchmark_split'] == name] for name in ['legacy', 'domestic']}
        results[strategy] = {'mean_recall_at_5': round(sum(r['recall_at_5'] for r in rows) / len(rows), 4), 'by_split': {name: {'case_count': len(group), 'mean_recall_at_5': round(sum(row['recall_at_5'] for row in group) / len(group), 4)} for name, group in splits.items()}, 'cases': rows}
    routes = [{**case, 'actual': local_route(case['query'])} for case in cases['routing']]
    route_accuracy = sum(r['actual']['intent'] == r['intent'] and r['actual']['need_weather'] == r['weather'] for r in routes) / len(routes)
    output = {'scope': f'{len(retrieval_cases)} 条内部检索题（原题 {len(cases["retrieval"])} + 国内新增 {len(domestic_cases["retrieval"])}）+ {len(routes)} 条规则路由题；{len(retriever.chunks)} 个知识片段；本地词频检索；不是现场专业效果评测。', 'knowledge_chunk_count': len(retriever.chunks), 'embedding_api_used': False, 'model_api_used': False, 'retrieval': results, 'routing': {'accuracy': route_accuracy, 'cases': routes}}
    (ROOT / 'eval' / 'results.local.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'scope': output['scope'], 'recall_at_5': {k: v['mean_recall_at_5'] for k, v in results.items()}, 'by_split': {k: v['by_split'] for k, v in results.items()}, 'routing_accuracy': route_accuracy}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(evaluate())
