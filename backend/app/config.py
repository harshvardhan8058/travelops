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
from dataclasses import dataclass, field
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


class LLMProvider(StrEnum):
    """Which OpenAI-compatible endpoint `live` mode talks to.

    A transport choice, not an architectural one. Both providers serve the same
    `openai/gpt-oss-120b` behind the same chat-completions contract, so the agents, prompts,
    response schemas and the assurance gate are identical either way — only the base URL, the
    key and the recorded generator differ.

    `openrouter` is the default because Groq's free and developer tiers cap an account at 8000
    tokens per minute, counted as `prompt_tokens + max_tokens`. That ceiling refused the
    explainer and reporter outright (HTTP 413 `rate_limit_exceeded`, 8902 and 9587 requested).
    """

    openrouter = "openrouter"
    groq = "groq"


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

    llm_mode: LLMMode = LLMMode.fixture
    llm_provider: LLMProvider = LLMProvider.openrouter

    #: OpenRouter, the default live transport. OpenAI-compatible chat-completions, so the
    #: existing request shape is unchanged: `openai/gpt-oss-120b` there advertises support for
    #: `max_tokens`, `response_format` and `temperature`, with a 131072-token context.
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-oss-120b"
    #: Chat completions live at `<base>/chat/completions`, i.e.
    #: https://openrouter.ai/api/v1/chat/completions
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    #: No per-minute token ceiling of the Groq kind; this only keeps a single request sane
    #: against the model's context window. Sizing still applies, because reserving more output
    #: than an artifact needs is paid for whether or not it is used.
    openrouter_tpm_limit: int = 60000

    groq_api_key: str = ""
    #: Groq decommissioned `llama-3.3-70b-versatile` on 2026-08-16 for free and developer tiers
    #: (announced 2026-06-17). Requests to it return HTTP 400 `model_decommissioned`, which is
    #: what took the whole Phase 3 live path down: the planner produced no candidate and both
    #: prose endpoints failed. `openai/gpt-oss-120b` is Groq's documented replacement.
    #: Reasoning is returned in a separate `reasoning` field rather than in `content`, so the
    #: JSON-mode contract this client relies on is unchanged.
    #: See https://console.groq.com/docs/deprecations
    groq_model: str = "openai/gpt-oss-120b"
    #: Groq's OpenAI-compatible prefix. Stated here rather than assumed by an SDK, for the same
    #: reason as `openrouter_base_url`.
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_temperature: float = 0.1
    #: Tokens-per-minute ceiling for the account. Groq charges a request against TPM as
    #: `prompt_tokens + max_tokens` — the RESERVED completion budget, not the tokens actually
    #: returned — and answers HTTP 413 `rate_limit_exceeded` when a single request exceeds it.
    #: Request sizing is derived from this, so an account on a different tier needs only this
    #: number changed. 8000 is the free/developer tier for openai/gpt-oss-120b.
    groq_tpm_limit: int = 8000

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

    #: Wall-clock ceiling for the optional Planner candidate, per incident.
    #:
    #: The candidate is additive: the playbook plan is already persisted and selected before the
    #: agent is asked, and Phase 3's contract is that a model failure never blocks recovery. That
    #: contract was only half-enforced — a *failing* model was handled, a *slow* one was not.
    #:
    #: `POST /incident-groups/{ref}/run` advances eight member incidents sequentially, so with the
    #: client's own budget (60s per attempt x 3 attempts + backoff = 184s worst case) two slow
    #: incidents alone exceed the verifier's 300s request budget. The cascade then times out
    #: part-way and the union rollups report 13 connections and 5 pairings instead of 22 and 9 —
    #: not wrong arithmetic, just fewer incidents having finished.
    #:
    #: 20s is comfortably above a healthy live planner call (a few seconds) and bounds the whole
    #: eight-incident cascade to 160s even if every model call hangs.
    planner_candidate_budget_seconds: float = Field(default=20.0, gt=0, le=180)

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


@dataclass(frozen=True)
class ProviderTransport:
    """Everything the live call needs, resolved from the configured provider.

    One resolution point so `LLMClient` stays a single code path. Adding a provider means adding
    a branch here, not a second client — a second client is how two request shapes, two retry
    policies and two sets of error semantics start drifting apart.

    `endpoint_url` is the FULL chat-completions URL, not a base for something else to complete.
    The previous version passed a base URL to a vendor SDK, which appended its own
    `/openai/v1/chat/completions` to it and produced
    `https://openrouter.ai/api/v1/openai/v1/chat/completions` — a 404 on every live call. The
    URL is the thing that was wrong, so the URL is now stated in one place and asserted.
    """

    provider: LLMProvider
    endpoint_url: str
    api_key: str
    model: str
    tpm_limit: int
    key_env_var: str
    #: `openrouter:openai/gpt-oss-120b`. Recorded on the plan and returned as `generator`;
    #: `_source_of` reads only the `fixture:` prefix, and assurance never branches on it.
    generator: str
    extra_headers: dict[str, str] = field(default_factory=dict)


def _chat_completions_url(base_url: str) -> str:
    """`<base>/chat/completions`, tolerant of a trailing slash on the configured base."""
    return f"{base_url.rstrip('/')}/chat/completions"


def provider_transport(settings: Settings) -> ProviderTransport:
    if settings.llm_provider is LLMProvider.groq:
        return ProviderTransport(
            provider=LLMProvider.groq,
            endpoint_url=_chat_completions_url(settings.groq_base_url),
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            tpm_limit=settings.groq_tpm_limit,
            key_env_var="GROQ_API_KEY",
            generator=f"groq:{settings.groq_model}",
        )
    return ProviderTransport(
        provider=LLMProvider.openrouter,
        endpoint_url=_chat_completions_url(settings.openrouter_base_url),
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        tpm_limit=settings.openrouter_tpm_limit,
        key_env_var="OPENROUTER_API_KEY",
        generator=f"openrouter:{settings.openrouter_model}",
        # Attribution on the account's activity page. OpenRouter-specific, so it lives with the
        # OpenRouter branch rather than being conditionally bolted on at call time.
        extra_headers={
            "HTTP-Referer": "https://github.com/harshvardhan8058/travelops",
            "X-Title": "TravelOps AI",
        },
    )


def resolve_modes(settings: Settings) -> ResolvedModes:
    """Resolve effective modes, failing closed on unsafe combinations."""
    degradations: list[str] = []

    # ---------------------------------------------------------------- reasoning
    llm = settings.llm_mode
    transport = provider_transport(settings)
    if llm is LLMMode.live and not transport.api_key:
        # Names the key for the provider actually selected. Naming GROQ_API_KEY while
        # LLM_PROVIDER=openrouter would send an operator to set the wrong variable.
        if settings.allow_llm_degradation:
            llm = LLMMode.fixture
            degradations.append(
                f"LLM_MODE=live requested without {transport.key_env_var}; degraded to fixture"
            )
        else:
            raise ConfigurationError(
                f"LLM_MODE=live with LLM_PROVIDER={transport.provider.value} requires "
                f"{transport.key_env_var}. Set the key, choose LLM_MODE=fixture, or set "
                "ALLOW_LLM_DEGRADATION=true to permit fallback."
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
