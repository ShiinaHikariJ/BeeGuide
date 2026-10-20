import asyncio
import logging
import re
from collections import Counter

import httpx
import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import select

from .config import settings
from .database import SessionLocal, engine
from .models import KnowledgeChunk

jieba.setLogLevel(logging.WARNING)
STOP = set('的 了 是 我 你 和 在 吗 呢 有 一个 什么 怎么 如何 想 可以 需要'.split())


def tokenize(text):
    words = [w.lower() for w in jieba.lcut(text) if w.strip() and w not in STOP and re.search(r'[\w\u4e00-\u9fff]', w)]
    return words or ['__empty__']


def requested_seasons(text):
    aliases = {'春季': ['春季', '春天', '早春', '春繁', '倒春寒'], '夏季': ['夏季', '夏天', '越夏', '度夏'], '秋季': ['秋季', '秋天', '秋末', '秋繁'], '冬季': ['冬季', '冬天', '越冬', '入冬', '暖冬']}
    return {season for season, words in aliases.items() if any(word in text for word in words)}


class Retriever:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.reload()

    def reload(self):
        with SessionLocal() as db:
            self.chunks = list(db.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.id)))
        self.tokens = [tokenize(c.title + ' ' + c.content + ' ' + ' '.join(c.meta.get('tags', []))) for c in self.chunks]
        self.bm25 = BM25Okapi(self.tokens)
        df = Counter(word for tokens in self.tokens for word in set(tokens))
        self.idf = {w: np.log((len(self.tokens) + 1) / (count + 1)) + 1 for w, count in df.items()}
        self.local_vectors = [self.tfidf(tokens) for tokens in self.tokens]

    def tfidf(self, tokens):
        counts = Counter(tokens)
        return {w: count * self.idf.get(w, 0) for w, count in counts.items()}

    @staticmethod
    def cosine(a, b):
        norm = np.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
        return sum(v * b.get(w, 0) for w, v in a.items()) / norm if norm else 0.0

    async def embeddings(self, texts):
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(settings.model_base_url.rstrip('/') + '/embeddings', headers={'Authorization': f'Bearer {settings.model_api_key}'}, json={'model': settings.embedding_model, 'input': texts, 'dimensions': settings.embedding_dimensions})
            response.raise_for_status()
            rows = sorted(response.json()['data'], key=lambda x: x['index'])
            vectors = [r['embedding'] for r in rows]
            if len(vectors) != len(texts) or any(len(v) != settings.embedding_dimensions or not np.isfinite(v).all() for v in vectors):
                raise ValueError('embedding dimension or count mismatch')
            return vectors

    async def search(self, query, strategy='auto', top_k=5):
        automatic = strategy == 'auto'
        tokens = tokenize(query)
        scores = self.bm25.get_scores(tokens)
        # BM25 can be negative in tiny corpora; overlap defines eligibility.
        overlap = {i for i, row in enumerate(self.tokens) if set(tokens) & set(row)}
        bm_order = sorted(overlap, key=lambda i: float(scores[i]), reverse=True)
        mode = 'BM25 + 本地 TF-IDF（词频向量）'
        warning = None
        semantic_order = []
        if settings.embedding_enabled and settings.live_model:
            try:
                version = f'{settings.embedding_model}:{settings.embedding_dimensions}'
                async with self.lock:
                    missing = [c for c in self.chunks if c.embedding_version != version or c.embedding is None]
                    for start in range(0, len(missing), 10):
                        batch = missing[start:start + 10]
                        vectors = await self.embeddings([c.content for c in batch])
                        with SessionLocal() as db:
                            for c, vector in zip(batch, vectors):
                                stored = db.get(KnowledgeChunk, c.id)
                                stored.embedding = vector
                                stored.embedding_version = version
                                c.embedding = vector
                                c.embedding_version = version
                            db.commit()
                query_vector = (await self.embeddings([query]))[0]
                if engine.dialect.name == 'postgresql':
                    with SessionLocal() as db:
                        ids = list(db.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.embedding_version == version).order_by(KnowledgeChunk.embedding.cosine_distance(query_vector)).limit(12)))
                    index = {c.id: i for i, c in enumerate(self.chunks)}
                    semantic_order = [index[cid] for cid in ids]
                else:
                    q = np.asarray(query_vector)
                    similarity = [float(np.dot(q, c.embedding) / max(np.linalg.norm(q) * np.linalg.norm(c.embedding), 1e-12)) for c in self.chunks]
                    semantic_order = sorted(range(len(similarity)), key=lambda i: similarity[i], reverse=True)[:12]
                mode = 'BM25 + Embedding 语义检索 + RRF'
            except (httpx.HTTPError, ValueError, KeyError):
                warning = 'Embedding 服务暂不可用，本次使用本地词频检索。'
        if not semantic_order:
            vector = self.tfidf(tokens)
            similarities = [self.cosine(vector, v) for v in self.local_vectors]
            semantic_order = sorted((i for i in overlap if similarities[i] > 0), key=lambda i: similarities[i], reverse=True)
        if automatic:
            strategy = 'hybrid' if mode.startswith('BM25 + Embedding') else settings.local_retrieval_strategy
            if strategy == 'bm25':
                mode = 'BM25（本地资料演示）'
            elif strategy == 'vector':
                mode = 'TF-IDF（本地词频向量演示）'
        if strategy == 'bm25':
            ranked = bm_order
        elif strategy == 'vector':
            ranked = semantic_order
        else:
            fusion = Counter()
            for ranking in [bm_order[:12], semantic_order[:12]]:
                for rank, i in enumerate(ranking):
                    fusion[i] += 1 / (60 + rank + 1)
            ranked = sorted(fusion, key=fusion.get, reverse=True)
        seasons = requested_seasons(query)
        if seasons:
            def applicable(i):
                declared = self.chunks[i].meta.get('seasons', [])
                return not declared or any(value in ['日常', '四季'] for value in declared) or bool(seasons & requested_seasons(' '.join(declared)))
            ranked = [i for i in ranked if applicable(i)]
        hits = [{'id': self.chunks[i].id, 'title': self.chunks[i].title, 'content': self.chunks[i].content, **self.chunks[i].meta} for i in ranked[:top_k]]
        return {'hits': hits, 'mode': mode, 'warning': warning, 'requested_seasons': sorted(seasons)}
