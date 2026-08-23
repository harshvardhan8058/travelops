"""Typed settings and fail-closed runtime mode resolution.

Two rules govern this module:

1. An unknown mode, or missing safety configuration, refuses startup. Guessing is worse
   than not starting.
2. A degradation (live -> fixture, gmail -> console) is permitted only when explicitly
   allowed, and is always reported. The system never silently pretends to be healthier
   than it is.

Owner: Stream A. Other streams read settings; they do not add validation here.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when configuration is unsafe or incoherent. Always fatal at startup."""


class AppEnv(StrEnum):
    development = "development"
    demo = "demo"
    test = "test"


class LLMMode(StrEnum):
    live = "live"
    fixture = "fixture"
    off = "off"


class WeatherMode(StrEnum):
    live = "live"
    fixture = "fixture"


class NotificationMode(StrEnum):
    console = "console"
    mailtrap = "mailtrap"
    gmail = "gmail"


class PolicyMode(StrEnum):
    """Governs what the system may claim about regulation.

    demo     - fictional fixture, no citation, no legal claim
    charter  - official-but-dated pack; real cited figures behind a dated badge
    verified - requires an approved primary-source pack; currently unreachable
    """

    demo = "demo"
    charter = "charter"
    verified = "verified"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _strip_surrounding_whitespace(cls, values: Any) -> Any:
        """Trim every incoming string value before anything is parsed.

        This exists for one specific, invisible failure. Git for Windows defaults
        `core.autocrlf=true`, so a fresh clone writes `.env.example` with CRLF; the documented
        `Copy-Item .env.example .env` then produces a `.env` whose every value ends in `\\r`.
        `LLM_MODE=fixture\\r` is not a member of `LLMMode`, so the API refuses to start — correctly,
        per rule 1 above, but with a message that names the enum and never mentions the carriage
        return. The value looks right in every editor and the operator has nothing to go on.

        `.gitattributes` now pins LF so a fresh clone cannot produce it. This is the second line of
        defence, for a working copy cloned before that landed or a `.env` touched by an editor that
        appends CR.

        Trimming whitespace only. It corrects nothing else and cannot turn an unknown mode into a
        known one: an actually-wrong value still refuses startup, which is the whole point of this
        module.
        """
        if not isinstance(values, dict):
            return values
        return {
            key: value.strip() if isinstance(value, str) else value for key, value in values.items()
        }

    app_env: AppEnv = AppEnv.development
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://travelops:travelops@localhost:5432/travelops"
    redis_url: str = "redis://localhost:6379/0"

    #: Reasoning agents are opt-in. `off` means the deterministic playbook is the planner,
    #: which is the path the demo is verified on; a model is an improvement on it, never a
    #: prerequisite. Defaulting to `fixture` would silently route the demo through an agent.
    llm_mode: LLMMode = LLMMode.off
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_temperature: float = 0.1

    weather_mode: WeatherMode = WeatherMode.fixture
    weather_poll_seconds: int = 60

    # Deterministic risk index threshold (0-100). NOT a calibrated probability.
    delay_risk_event_threshold: int = Field(default=75, ge=0, le=100)

    notification_mode: NotificationMode = NotificationMode.console
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    demo_recipient_allowlist: str = ""

    policy_mode: PolicyMode = PolicyMode.charter
    policy_pack_dir: Path = Path("./policy_packs")
    policy_pack_id: str = "in-moca-charter-2019"
    policy_pack_version: str = "2019.02"

    assurance_config_path: Path = Path("./config/assurance.v1.yaml")

    #: Browser origins the API accepts. Loopback development ports only.
    #:
    #: A hardcoded list meant the console could only ever be served on 5173. Any other port — a
    #: `vite preview`, a second instance, a rehearsal on a spare port — got a CORS failure that
    #: renders as an empty screen with the reason only in the browser console. That is the worst
    #: possible failure mode for a demo: it looks like the backend is down when it is answering.
    cors_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173"
    )

    #: Plan-level (group-scoped) assurance config. A SEPARATE setting on purpose.
    #:
    #: v1 has no `plan:` section — it predates plan-level assurance — so loading it for the
    #: plan gate raises. Pointing `assurance_config_path` at v2 instead would work for the
    #: action gate, but it would silently change `config_version` on every new
    #: `assurance_evaluation` row from `assurance-v1` to `assurance-v2`, and a Phase 1 record
    #: must stay interpretable under the semantics it was decided by. Two paths, two
    #: identities, each recorded on the decision it governed.
    plan_config_path: Path = Path("./config/assurance.v2.yaml")

    max_workflow_steps: int = Field(default=20, ge=1, le=1000)
    action_timeout_seconds: int = Field(default=30, ge=1, le=600)

    data_seed: int = 20260807
    demo_dataset_id: str = "bengaluru_storm"

    # Explicit opt-ins for degradation. Default False = fail closed.
    allow_llm_degradation: bool = False
    allow_notification_degradation: bool = True

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper

    @property
    def recipient_allowlist(self) -> list[str]:
        return [item.strip() for item in self.demo_recipient_allowlist.split(",") if item.strip()]


class ResolvedModes:
    """Effective runtime modes after validation, with degradation reasons.

    Serialised by GET /system/mode. Contains no secrets.
    """

    def __init__(
        self,
        *,
        llm: LLMMode,
        weather: WeatherMode,
        notification: NotificationMode,
        policy: PolicyMode,
        real_email_enabled: bool,
        assurance_config_present: bool,
        assurance_config_version: str | None,
        assurance_config_hash: str | None,
        degradations: list[str],
    ) -> None:
        self.llm = llm
        self.weather = weather
        self.notification = notification
        self.policy = policy
        self.real_email_enabled = real_email_enabled
        self.assurance_config_present = assurance_config_present
        self.assurance_config_version = assurance_config_version
        self.assurance_config_hash = assurance_config_hash
        self.degradations = degradations

    @property
    def workflow_executable(self) -> bool:
        """Assurance config is mandatory. Without it, no action may be authorised."""
        return self.assurance_config_present

    def to_dict(self) -> dict[str, object]:
        return {
            "llm_mode": self.llm.value,
            "weather_mode": self.weather.value,
            "notification_mode": self.notification.value,
            "policy_mode": self.policy.value,
            "real_email_enabled": self.real_email_enabled,
            "assurance": {
                "config_present": self.assurance_config_present,
                "config_version": self.assurance_config_version,
                "config_hash": self.assurance_config_hash,
                "workflow_executable": self.workflow_executable,
            },
            "degradations": self.degradations,
        }


#: Repository root, derived from this file's location: backend/app/config.py -> ../..
REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_repo_path(path: Path) -> Path:
    """Resolve a possibly-relative path predictably.

    An absolute path is returned unchanged. A relative path is tried against the current
    working directory first, then against the repository root.

    Without this, `./config/assurance.v1.yaml` resolves differently depending on whether a
    process was started from the repo root, from `backend/`, or inside the container — and
    the symptom is a confusing "workflow execution blocked" instead of an obvious error.
    """
    if path.is_absolute():
        return path
    if path.exists():
        return path
    return REPO_ROOT / path


def _read_assurance_config(path: Path) -> tuple[str | None, str | None]:
    """Return (version, sha256) for the gate config, or (None, None) if unreadable."""
    path = resolve_repo_path(path)
    if not path.is_file():
        return None, None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    version: str | None = None
    try:
        import yaml

        parsed = yaml.safe_load(raw.decode("utf-8")) or {}
        if isinstance(parsed, dict):
            value = parsed.get("version")
            version = str(value) if value is not None else None
    except Exception:
        version = None
    return version, digest


def resolve_modes(settings: Settings) -> ResolvedModes:
    """Resolve effective modes, failing closed on unsafe combinations."""
    degradations: list[str] = []

    # ---------------------------------------------------------------- reasoning
    llm = settings.llm_mode
    if llm is LLMMode.live and not settings.groq_api_key:
        if settings.allow_llm_degradation:
            llm = LLMMode.fixture
            degradations.append("LLM_MODE=live requested without GROQ_API_KEY; degraded to fixture")
        else:
            raise ConfigurationError(
                "LLM_MODE=live requires GROQ_API_KEY. Set the key, choose "
                "LLM_MODE=fixture, or set ALLOW_LLM_DEGRADATION=true to permit fallback."
            )

    # ---------------------------------------------------------------- notifications
    notification = settings.notification_mode
    real_email_enabled = False
    if notification in {NotificationMode.mailtrap, NotificationMode.gmail}:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", settings.smtp_host),
                ("SMTP_USERNAME", settings.smtp_username),
                ("SMTP_PASSWORD", settings.smtp_password),
            )
            if not value
        ]
        if missing:
            if settings.allow_notification_degradation:
                notification = NotificationMode.console
                degradations.append(
                    f"NOTIFICATION_MODE={settings.notification_mode.value} missing "
                    f"{', '.join(missing)}; degraded to console"
                )
            else:
                raise ConfigurationError(
                    f"NOTIFICATION_MODE={settings.notification_mode.value} requires "
                    f"{', '.join(missing)}."
                )
        elif not settings.recipient_allowlist:
            # Credentials present but no allowlist: never blast synthetic passengers.
            degradations.append(
                "DEMO_RECIPIENT_ALLOWLIST is empty; all deliveries recorded as simulated"
            )
        else:
            real_email_enabled = True

    # ---------------------------------------------------------------- assurance
    version, digest = _read_assurance_config(settings.assurance_config_path)
    config_present = digest is not None
    if not config_present:
        degradations.append(
            "assurance config not found at "
            f"{resolve_repo_path(settings.assurance_config_path)}; "
            "workflow execution is blocked"
        )

    # ---------------------------------------------------------------- policy
    policy = settings.policy_mode
    if policy is PolicyMode.verified:
        pack_dir = settings.policy_pack_dir / settings.policy_pack_id / settings.policy_pack_version
        raise ConfigurationError(
            "POLICY_MODE=verified requires an approved primary-source pack whose "
            f"verified_mode_eligible is true. {pack_dir} is not eligible "
            "(PACK_NOT_VERIFIED_ELIGIBLE). Use POLICY_MODE=charter or demo."
        )

    return ResolvedModes(
        llm=llm,
        weather=settings.weather_mode,
        notification=notification,
        policy=policy,
        real_email_enabled=real_email_enabled,
        assurance_config_present=config_present,
        assurance_config_version=version,
        assurance_config_hash=digest,
        degradations=degradations,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_modes() -> ResolvedModes:
    return resolve_modes(get_settings())
