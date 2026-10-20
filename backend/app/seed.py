import json

from sqlalchemy import select

from .auth import hash_password
from .config import ROOT
from .database import SessionLocal
from .models import KnowledgeChunk, User


def load_knowledge():
    rows = json.loads((ROOT / 'knowledge' / 'seed.json').read_text(encoding='utf-8'))
    registry = json.loads((ROOT / 'knowledge' / 'sources.json').read_text(encoding='utf-8'))['sources']
    sources = {source['id']: source for source in registry}
    for item in json.loads((ROOT / 'knowledge' / 'domestic.json').read_text(encoding='utf-8')):
        source = sources[item['source_id']]
        rows.append({**{key: value for key, value in source.items() if key not in ['id', 'title']}, 'source_title': source['title'], **item})
    ids = [row['id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Knowledge chunk IDs must be unique across source files.')
    return rows


def seed():
    rows = load_knowledge()
    with SessionLocal() as db:
        for email, name in [('demo@beeguide.local', '养蜂新手'), ('second@beeguide.local', '另一位蜂友')]:
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, name=name, password_hash=hash_password('bee2026'), profile={}))
        for item in rows:
            chunk = db.get(KnowledgeChunk, item['id'])
            if not chunk:
                chunk = KnowledgeChunk(id=item['id'], title=item['title'], content=item['content'], meta={})
                db.add(chunk)
            if chunk.content != item['content']:
                chunk.embedding = None
                chunk.embedding_version = None
            chunk.title, chunk.content = item['title'], item['content']
            chunk.meta = {k: v for k, v in item.items() if k not in ['id', 'title', 'content']}
        db.commit()
