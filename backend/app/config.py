from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('BEEGUIDE_DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    model_api_key: str = ''
    model_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    model_name: str = 'qwen-plus'
    model_thinking_enabled: bool = False
    embedding_enabled: bool = False
    embedding_model: str = 'text-embedding-v3'
    embedding_dimensions: int = 1024
    local_retrieval_strategy: Literal['bm25', 'vector', 'hybrid'] = 'vector'
    database_url: str = f'sqlite:///{(DATA / "beeguide.db").as_posix()}'
    weather_enabled: bool = True
    agent_architecture: Literal['single', 'multi'] = 'multi'
    agent_max_tool_rounds: int = Field(default=3, ge=1, le=4)
    agent_max_tool_calls: int = Field(default=6, ge=1, le=8)
    agent_tool_timeout: float = Field(default=30, ge=1, le=60)
    agent_coordination_timeout: float = Field(default=100, ge=1, le=180)

    @property
    def live_model(self):
        return bool(self.model_api_key.strip())


settings = Settings()


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))
