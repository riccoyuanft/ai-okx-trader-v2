from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fernet_key: str
    jwt_secret: str
    jwt_expire_hours: int = 24

    supabase_url: str
    supabase_key: str

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # AI Providers
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"

    qwen_api_key: str
    qwen_model: str = "qwen-plus"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    doubao_api_key: str
    doubao_model: str = "doubao-pro-32k"
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
