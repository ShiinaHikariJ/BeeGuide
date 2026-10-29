from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('BEEGUIDE_DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    model_api_key: str = ''
    model_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    model_name: str = 'qwen-plus'
    model_thinking_enabled: bool = False
    model_context_window: int = Field(default=32768, ge=4096)
    model_max_output_tokens: int = Field(default=4096, ge=256)
    context_safety_tokens: int = Field(default=1024, ge=128)
    context_tool_result_tokens: int = Field(default=4096, ge=256)
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

    @model_validator(mode='after')
    def validate_context_budget(self):
        if self.model_context_window <= self.model_max_output_tokens + self.context_safety_tokens:
            raise ValueError('MODEL_CONTEXT_WINDOW must exceed output reserve plus safety margin')
        return self

    @property
    def live_model(self):
        return bool(self.model_api_key.strip())


settings = Settings()


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))
