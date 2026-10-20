"""Import owned/reusable text or Markdown with explicit source metadata."""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.database import SessionLocal
from app.models import KnowledgeChunk

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('file', type=Path)
parser.add_argument('--title', required=True)
parser.add_argument('--url', default='')
parser.add_argument('--source', required=True)
parser.add_argument('--region', required=True)
parser.add_argument('--version', required=True)
parser.add_argument('--review-status', default='待专业复核')
args = parser.parse_args()
text = args.file.read_text(encoding='utf-8').strip()
blocks = []
current = ''
for paragraph in text.split('\n\n'):
    if len(current) + len(paragraph) > 650 and current:
        blocks.append(current)
        current = ''
    while len(paragraph) > 700:
        blocks.append(paragraph[:650])
        paragraph = paragraph[600:]
    current += ('\n\n' if current else '') + paragraph
if current:
    blocks.append(current)
with SessionLocal() as db:
    for i, content in enumerate(blocks):
        digest = hashlib.sha256((args.source + args.title + args.version + content).encode()).hexdigest()[:24]
        cid = 'import-' + digest
        if not db.get(KnowledgeChunk, cid):
            db.add(KnowledgeChunk(id=cid, title=args.title, content=content, meta={'source': args.source, 'url': args.url, 'section': f'整理片段 {i + 1}', 'region': args.region, 'version': args.version, 'review_status': args.review_status, 'tags': []}))
    db.commit()
print(f'Imported {len(blocks)} chunks. Restart the application to rebuild the local index.')
