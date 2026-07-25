"""Application settings loaded from environment variables."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_dry_run_override: ContextVar[bool | None] = ContextVar("agent_pm_dry_run_override", default=None)


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
    github_repositories: list[str] = Field(default_factory=list, alias="GITHUB_REPOSITORIES")
    linear_api_key: str | None = Field(None, alias="LINEAR_API_KEY")
    linear_team_ids: list[str] = Field(default_factory=list, alias="LINEAR_TEAM_IDS")
    sentry_auth_token: str | None = Field(None, alias="SENTRY_AUTH_TOKEN")
    sentry_org_slug: str | None = Field(None, alias="SENTRY_ORG_SLUG")
    sentry_base_url: str | None = Field(None, alias="SENTRY_BASE_URL")
    jira_base_url: str | None = Field(None, alias="JIRA_BASE_URL")
    jira_api_token: str | None = Field(None, alias="JIRA_API_TOKEN")
    jira_email: str | None = Field(None, alias="JIRA_EMAIL")
    dry_run: bool = Field(True, alias="DRY_RUN")
    approval_required: bool = Field(True, alias="APPROVAL_REQUIRED")
    vector_store_path: Path = Field(Path("./data/vector_store.json"), alias="VECTOR_STORE_PATH")
    trace_dir: Path = Field(Path("./data/traces"), alias="TRACE_DIR")
    plugin_config_path: Path = Field(Path("./config/plugins.yaml"), alias="PLUGIN_CONFIG_PATH")
    plugin_secret_path: Path | None = Field(None, alias="PLUGIN_SECRET_PATH")
    procedure_dir: Path = Field(Path("./procedures"), alias="PROCEDURE_DIR")
    allowed_projects: list[str] = Field(default_factory=list, alias="ALLOWED_PROJECTS")

    def __getattribute__(self, name: str):
        if name == "dry_run":
            override = _dry_run_override.get()
            if override is not None:
                return override
        return super().__getattribute__(name)

    @contextmanager
    def override_dry_run(self, value: bool):
        token = _dry_run_override.set(value)
        try:
            yield
        finally:
            _dry_run_override.reset(token)

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

    @staticmethod
    def _parse_csv_list(value: str | list[str] | None) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "github_repositories",
        "linear_team_ids",
        "slack_sync_channels",
        "gmail_label_filter",
        "notion_database_ids",
        mode="before",
    )
    @classmethod
    def _parse_string_lists(cls, value):
        return cls._parse_csv_list(value)

    @field_validator("google_drive_scopes", "gmail_scopes", mode="before")
    @classmethod
    def _parse_drive_scopes(cls, value):
        default_scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        if value is None or value == "":
            return default_scopes
        if isinstance(value, str):
            scopes = [scope.strip() for scope in value.split(",") if scope.strip()]
            return scopes or default_scopes
        return value

    slack_bot_token: str | None = Field(None, alias="SLACK_BOT_TOKEN")
    slack_status_channel: str | None = Field(None, alias="SLACK_STATUS_CHANNEL")
    slack_sync_channels: list[str] = Field(default_factory=list, alias="SLACK_SYNC_CHANNELS")
    slack_sync_interval_seconds: int = Field(900, alias="SLACK_SYNC_INTERVAL_SECONDS")
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
    calendar_sync_interval_seconds: int = Field(1800, alias="CALENDAR_SYNC_INTERVAL_SECONDS")
    calendar_sync_window_days: int = Field(14, alias="CALENDAR_SYNC_WINDOW_DAYS")
    google_drive_scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/drive.readonly"],
        alias="GOOGLE_DRIVE_SCOPES",
    )
    google_drive_sync_interval_seconds: int = Field(3600, alias="GOOGLE_DRIVE_SYNC_INTERVAL_SECONDS")
    google_drive_query: str | None = Field(None, alias="GOOGLE_DRIVE_QUERY")
    agent_session_db: Path = Field(Path("./data/agent_sessions.db"), alias="AGENTS_SESSION_DB")
    agent_tools_enabled: bool = Field(False, alias="AGENT_TOOLS_ENABLED")
    agents_config_path: Path = Field(Path("./config/agents.yaml"), alias="AGENTS_CONFIG_PATH")
    goal_alignment_notify: bool = Field(False, alias="GOAL_ALIGNMENT_NOTIFY")
    alignment_log_path: Path = Field(Path("./data/alignment_log.json"), alias="ALIGNMENT_LOG_PATH")
    trace_export_webhook: str | None = Field(None, alias="TRACE_EXPORT_WEBHOOK")
    trace_export_s3_bucket: str | None = Field(None, alias="TRACE_EXPORT_S3_BUCKET")
    trace_export_s3_prefix: str = Field("traces/", alias="TRACE_EXPORT_S3_PREFIX")
    api_key: str | None = Field(None, alias="API_KEY")
    admin_api_key: str | None = Field(None, alias="ADMIN_API_KEY")
    log_format: str = Field("json", alias="LOG_FORMAT")  # json or text
    database_url: str | None = Field("sqlite+aiosqlite:///./data/agent_pm.db", alias="DATABASE_URL")
    database_echo: bool = Field(False, alias="DATABASE_ECHO")
    enable_opentelemetry: bool = Field(False, alias="ENABLE_OPENTELEMETRY")
    otel_service_name: str = Field("agent-pm", alias="OTEL_SERVICE_NAME")
    otel_exporter_endpoint: str | None = Field(None, alias="OTEL_EXPORTER_ENDPOINT")
    github_sync_interval_seconds: int = Field(900, alias="GITHUB_SYNC_INTERVAL_SECONDS")
    gmail_service_account_json: str | None = Field(None, alias="GMAIL_SERVICE_ACCOUNT_JSON")
    gmail_service_account_file: Path | None = Field(None, alias="GMAIL_SERVICE_ACCOUNT_FILE")
    gmail_scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/gmail.readonly"],
        alias="GMAIL_SCOPES",
    )
    gmail_delegated_user: str | None = Field(None, alias="GMAIL_DELEGATED_USER")
    gmail_label_filter: list[str] = Field(default_factory=list, alias="GMAIL_LABEL_FILTER")
    email_sync_interval_seconds: int = Field(1800, alias="EMAIL_SYNC_INTERVAL_SECONDS")
    notion_api_token: str | None = Field(None, alias="NOTION_API_TOKEN")
    notion_database_ids: list[str] = Field(default_factory=list, alias="NOTION_DATABASE_IDS")
    notion_sync_interval_seconds: int = Field(1800, alias="NOTION_SYNC_INTERVAL_SECONDS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]


settings = get_settings()
