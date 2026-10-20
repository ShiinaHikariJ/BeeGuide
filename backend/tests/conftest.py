import os
import sys
import tempfile
from pathlib import Path

test_data = Path(tempfile.mkdtemp(prefix='beeguide-tests-'))
os.environ['BEEGUIDE_DATA_DIR'] = str(test_data)
os.environ['DATABASE_URL'] = f'sqlite:///{(test_data / "test.db").as_posix()}'
os.environ['MODEL_API_KEY'] = ''
os.environ['EMBEDDING_ENABLED'] = 'false'
os.environ['WEATHER_ENABLED'] = 'false'
os.environ['AGENT_ARCHITECTURE'] = 'single'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope='session')
def client():
    with TestClient(app) as connection:
        yield connection


@pytest.fixture
def headers(client):
    data = client.post('/api/auth/login', json={'email': 'demo@beeguide.local', 'password': 'bee2026'}).json()
    client.put('/api/profile', headers={'Authorization': 'Bearer ' + data['token']}, json={})
    return {'Authorization': 'Bearer ' + data['token']}


@pytest.fixture
def second_headers(client):
    data = client.post('/api/auth/login', json={'email': 'second@beeguide.local', 'password': 'bee2026'}).json()
    return {'Authorization': 'Bearer ' + data['token']}
