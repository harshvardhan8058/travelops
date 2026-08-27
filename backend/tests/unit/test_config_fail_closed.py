"""Configuration must fail closed.

The system refuses to start on an unsafe combination, and degrades only when explicitly
permitted — always reporting that it did.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import (
    ConfigurationError,
    LLMMode,
    NotificationMode,
    Settings,
    resolve_modes,
)


def _settings(**overrides) -> Settings:
    # _env_file=None so a developer's local .env cannot affect the result.
    return Settings(_env_file=None, **overrides)


class TestRefusals:
    def test_live_llm_without_key_is_refused(self):
        """The default provider is OpenRouter, so its key is the one named."""
        with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
            resolve_modes(_settings(llm_mode="live"))

    def test_the_refusal_names_the_selected_providers_key_not_the_other_one(self):
        """Naming the wrong variable sends an operator to set a key that changes nothing."""
        with pytest.raises(ConfigurationError, match="GROQ_API_KEY"):
            resolve_modes(_settings(llm_mode="live", llm_provider="groq"))

    def test_a_key_for_the_other_provider_does_not_satisfy_live_mode(self):
        with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
            resolve_modes(_settings(llm_mode="live", groq_api_key="set-but-wrong-provider"))

    def test_live_is_accepted_once_the_selected_provider_has_its_key(self):
        modes = resolve_modes(_settings(llm_mode="live", openrouter_api_key="or-key"))
        assert modes.llm.value == "live"

    def test_verified_policy_mode_is_refused(self):
        """No approved primary-source pack exists yet, so verified must be unreachable."""
        with pytest.raises(ConfigurationError, match="PACK_NOT_VERIFIED_ELIGIBLE"):
            resolve_modes(_settings(policy_mode="verified"))

    def test_smtp_mode_without_credentials_is_refused_when_degradation_disallowed(self):
        with pytest.raises(ConfigurationError, match="SMTP_HOST"):
            resolve_modes(
                _settings(notification_mode="gmail", allow_notification_degradation=False)
            )

    @pytest.mark.parametrize("bad", ["sideways", "LIVE_ISH", ""])
    def test_unknown_mode_is_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            _settings(llm_mode=bad)

    def test_invalid_log_level_is_rejected(self):
        with pytest.raises(ValidationError):
            _settings(log_level="chatty")


class TestPermittedDegradation:
    def test_llm_degrades_only_when_allowed_and_is_reported(self):
        modes = resolve_modes(_settings(llm_mode="live", allow_llm_degradation=True))
        assert modes.llm is LLMMode.fixture
        assert any("degraded to fixture" in d for d in modes.degradations)

    def test_notification_degrades_to_console_and_is_reported(self):
        modes = resolve_modes(_settings(notification_mode="mailtrap"))
        assert modes.notification is NotificationMode.console
        assert modes.real_email_enabled is False
        assert any("degraded to console" in d for d in modes.degradations)

    def test_credentials_without_allowlist_never_send_real_email(self):
        """Synthetic passengers must not be emailed by accident."""
        modes = resolve_modes(
            _settings(
                notification_mode="gmail",
                smtp_host="smtp.example.com",
                smtp_username="u",
                smtp_password="p",
                demo_recipient_allowlist="",
            )
        )
        assert modes.real_email_enabled is False
        assert any("allowlist" in d.lower() for d in modes.degradations)

    def test_allowlist_enables_real_email(self):
        modes = resolve_modes(
            _settings(
                notification_mode="gmail",
                smtp_host="smtp.example.com",
                smtp_username="u",
                smtp_password="p",
                demo_recipient_allowlist="ops@example.com, second@example.com",
            )
        )
        assert modes.real_email_enabled is True
        # Assert on the notification concern specifically rather than an empty list, so an
        # unrelated degradation elsewhere cannot silently pass or fail this test.
        assert not any("console" in d or "allowlist" in d.lower() for d in modes.degradations)


class TestAssuranceGating:
    def test_missing_assurance_config_blocks_workflow_execution(self):
        modes = resolve_modes(_settings(assurance_config_path="/nonexistent/assurance.yaml"))
        assert modes.assurance_config_present is False
        assert modes.workflow_executable is False

    def test_real_config_is_loaded_with_version_and_hash(self):
        """The default relative path must resolve regardless of the working directory."""
        modes = resolve_modes(_settings())
        assert modes.assurance_config_present is True
        assert modes.assurance_config_version == "assurance-v1"
        assert modes.assurance_config_hash
        assert modes.workflow_executable is True

    def test_hash_is_stable_for_identical_content(self):
        first = resolve_modes(_settings()).assurance_config_hash
        second = resolve_modes(_settings()).assurance_config_hash
        assert first == second, "replay depends on a stable config hash"


class TestModeSerialisation:
    def test_mode_payload_contains_no_secrets(self):
        modes = resolve_modes(_settings(groq_api_key="super-secret-value"))
        serialised = str(modes.to_dict())
        assert "super-secret-value" not in serialised

    def test_allowlist_parsing_trims_and_drops_blanks(self):
        settings = _settings(demo_recipient_allowlist=" a@example.com , , b@example.com ")
        assert settings.recipient_allowlist == ["a@example.com", "b@example.com"]
