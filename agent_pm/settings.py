"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    openai_api_key: str | None = Field(
        None,
        alias="OPENAI_API_KEY",
        description="Optional when DRY_RUN=true; required for live OpenAI access.",
    )
    github_token: str | None = Field(None, alias="GITHUB_TOKEN")
    jira_base_url: str | None = Field(None, alias="JIRA_BASE_URL")
    jira_api_token: str | None = Field(None, alias="JIRA_API_TOKEN")
    jira_email: str | None = Field(None, alias="JIRA_EMAIL")
    dry_run: bool = Field(True, alias="DRY_RUN")
    approval_required: bool = Field(True, alias="APPROVAL_REQUIRED")
    vector_store_path: Path = Field(Path("./data/vector_store.json"), alias="VECTOR_STORE_PATH")
    trace_dir: Path = Field(Path("./data/traces"), alias="TRACE_DIR")
    tool_config_path: Path = Field(Path("./config/tools.yaml"), alias="TOOL_CONFIG_PATH")
    procedure_dir: Path = Field(Path("./procedures"), alias="PROCEDURE_DIR")
    allowed_projects: list[str] = Field(default_factory=list, alias="ALLOWED_PROJECTS")

    @field_validator("google_calendar_scopes", mode="before")
    @classmethod
    def _parse_google_scopes(cls, value):
        default_scopes = ["https://www.googleapis.com/auth/calendar.events"]
        if value is None or value == "":
            return default_scopes
        if isinstance(value, str):
            scopes = [scope.strip() for scope in value.split(",") if scope.strip()]
            return scopes or default_scopes
        return value

    slack_bot_token: str | None = Field(None, alias="SLACK_BOT_TOKEN")
    slack_status_channel: str | None = Field(None, alias="SLACK_STATUS_CHANNEL")
    calendar_base_url: str | None = Field(None, alias="CALENDAR_BASE_URL")
    calendar_api_key: str | None = Field(None, alias="CALENDAR_API_KEY")
    calendar_id: str | None = Field(None, alias="CALENDAR_ID")
    google_service_account_json: str | None = Field(None, alias="GOOGLE_SERVICE_ACCOUNT_JSON")
    google_service_account_file: Path | None = Field(None, alias="GOOGLE_SERVICE_ACCOUNT_FILE")
    google_calendar_scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/calendar.events"],
        alias="GOOGLE_CALENDAR_SCOPES",
    )
    google_calendar_delegated_user: str | None = Field(None, alias="GOOGLE_CALENDAR_DELEGATED_USER")
    use_dspy: bool = Field(False, alias="USE_DSPY")
    agent_session_db: Path = Field(Path("./data/agent_sessions.db"), alias="AGENTS_SESSION_DB")
    agent_tools_enabled: bool = Field(False, alias="AGENT_TOOLS_ENABLED")
    agents_config_path: Path = Field(Path("./config/agents.yaml"), alias="AGENTS_CONFIG_PATH")
    goal_alignment_notify: bool = Field(False, alias="GOAL_ALIGNMENT_NOTIFY")
    trace_export_webhook: str | None = Field(None, alias="TRACE_EXPORT_WEBHOOK")
    trace_export_s3_bucket: str | None = Field(None, alias="TRACE_EXPORT_S3_BUCKET")
    trace_export_s3_prefix: str = Field("traces/", alias="TRACE_EXPORT_S3_PREFIX")
    api_key: str | None = Field(None, alias="API_KEY")
    admin_api_key: str | None = Field(None, alias="ADMIN_API_KEY")
    log_format: str = Field("json", alias="LOG_FORMAT")  # json or text
    task_queue_workers: int = Field(5, alias="TASK_QUEUE_WORKERS")
    database_url: str | None = Field(None, alias="DATABASE_URL")
    database_echo: bool = Field(False, alias="DATABASE_ECHO")
    redis_url: str = Field("redis://localhost:6379", alias="REDIS_URL")
    enable_opentelemetry: bool = Field(False, alias="ENABLE_OPENTELEMETRY")
    otel_service_name: str = Field("agent-pm", alias="OTEL_SERVICE_NAME")
    otel_exporter_endpoint: str | None = Field(None, alias="OTEL_EXPORTER_ENDPOINT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]


settings = get_settings()
