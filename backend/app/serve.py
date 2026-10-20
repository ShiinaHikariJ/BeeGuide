import asyncio
import sys
import uvicorn

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

uvicorn.run('app.main:app', host='127.0.0.1' if sys.platform == 'win32' else '0.0.0.0', port=8008)
