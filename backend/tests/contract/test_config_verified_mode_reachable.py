"""G2 — POLICY_MODE=verified is reachable, but only through the loader.

The config layer used to refuse verified mode unconditionally, which meant the day an approved,
source-verified pack existed the system would still have refused it. `resolve_modes` now consults
the loader instead of pre-judging: an eligible pack is admitted, an ineligible one is refused for
the loader's own reason.

These tests prove both directions without inventing any regulatory content. The eligible-pack case
builds a *synthetic, obviously-fictional* pack in a temporary directory — it cites a fake clause,
names a fake reviewer, and hashes a throwaway source file. It exists only to prove the config seam
admits a pack the loader accepts; it is never committed and asserts nothing about real law.

The refusal cases run against the packs that actually ship, so the fail-closed guarantee is pinned
to reality: with the charter and demo-fixture packs on disk, verified mode must still refuse.

This lives under `tests/contract/` (A-owned) rather than `tests/unit/`, whose root is SHARED by
default per OWNERS. It exercises A-owned `config.py` startup behaviour and does not touch the SHARED
`test_config_fail_closed.py` guard, which keeps its own assertions.

Owner: Stream A.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.config import ConfigurationError, PolicyMode, Settings, resolve_modes


def _settings(**overrides) -> Settings:
    # _env_file=None so a developer's local .env cannot affect the result.
    return Settings(_env_file=None, **overrides)


# --------------------------------------------------------------------------- refusal (real packs)


class TestVerifiedStillFailsClosed:
    """With the packs that exist today, verified mode must refuse — and say why."""

    def test_default_charter_pack_is_refused_in_verified_mode(self):
        with pytest.raises(ConfigurationError, match="PACK_NOT_VERIFIED_ELIGIBLE"):
            resolve_modes(_settings(policy_mode="verified"))

    def test_demo_fixture_pack_is_refused_in_verified_mode(self):
        """A draft fixture is not an approved primary source."""
        with pytest.raises(ConfigurationError, match="PACK_NOT_VERIFIED_ELIGIBLE"):
            resolve_modes(
                _settings(
                    policy_mode="verified",
                    policy_pack_id="demo-fixture",
                    policy_pack_version="1.0",
                )
            )

    def test_missing_pack_is_refused_with_its_own_reason_code(self):
        """A path that does not resolve is POLICY_PACK_UNAVAILABLE, still fatal at startup."""
        with pytest.raises(ConfigurationError, match="POLICY_PACK_UNAVAILABLE"):
            resolve_modes(
                _settings(
                    policy_mode="verified",
                    policy_pack_id="does-not-exist",
                    policy_pack_version="9.9",
                )
            )

    def test_the_refusal_names_the_pack_it_was_asked_to_load(self):
        with pytest.raises(ConfigurationError) as excinfo:
            resolve_modes(_settings(policy_mode="verified"))
        message = str(excinfo.value)
        assert "in-moca-charter-2019@2019.02" in message


# --------------------------------------------------------------------------- other modes untouched


class TestOtherModesUnchanged:
    def test_charter_mode_resolves(self):
        modes = resolve_modes(_settings(policy_mode="charter"))
        assert modes.policy is PolicyMode.charter

    def test_demo_mode_resolves(self):
        modes = resolve_modes(_settings(policy_mode="demo"))
        assert modes.policy is PolicyMode.demo

    def test_charter_does_not_touch_the_loader(self, monkeypatch):
        """Non-verified startup must not pay a pack load; the loader stays lazy for them."""
        import app.policy.loader as loader_mod

        def _boom(*_args, **_kwargs):
            raise AssertionError("load_pack must not be called for charter mode")

        monkeypatch.setattr(loader_mod, "load_pack", _boom)
        modes = resolve_modes(_settings(policy_mode="charter"))
        assert modes.policy is PolicyMode.charter


# --------------------------------------------------------------------------- eligible pack admitted


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def eligible_pack_root(tmp_path: Path) -> Path:
    """A synthetic, fictional pack that satisfies every real precondition the loader enforces.

    Approved status, verified_mode_eligible, a named reviewer with a recorded approval, a cited
    computational rule, and an archived source file whose SHA-256 matches the recorded hash. It
    proves the seam, not any law: the clause ref and reviewer are made up on purpose.
    """
    pack_id = "xx-test-verified"
    version = "1.0"
    directory = tmp_path / pack_id / version
    directory.mkdir(parents=True)

    source_bytes = b"SYNTHETIC TEST SOURCE DOCUMENT - NOT LAW\n"
    _write(directory / "source.txt", source_bytes.decode())
    digest = hashlib.sha256(source_bytes).hexdigest()

    _write(
        directory / "pack.yaml",
        f"""id: {pack_id}
version: "{version}"
jurisdiction: XX
authority: Synthetic Test Authority
document: Synthetic Verified Pack (test only)
document_date: 2026-01-01
currency: INR
status: approved
verified_mode_eligible: true
ui_label: "SYNTHETIC VERIFIED TEST PACK"
precedence:
  conflict_rules_defined: true
  on_conflict: needs_human
required_context:
  - event
""",
    )
    _write(
        directory / "applicability.yaml",
        f"""pack: {pack_id}
version: "{version}"
required_facts:
  - event.type
applies_when:
  any_of:
    - event.type: cancellation
on_missing_required_fact: undetermined
on_undetermined: needs_human
""",
    )
    _write(
        directory / "rules.yaml",
        f"""pack: {pack_id}
version: "{version}"
rules:
  - id: cancellation.refund.full
    status: approved
    scope: all
    source_clause_refs: ["synthetic:clause:1"]
    interpretation: A fictional full-refund rule for test purposes only.
    when:
      all:
        - {{ fact: event.type, op: eq, value: cancellation }}
    entitlement:
      type: full_refund
      cash: true
""",
    )
    _write(
        directory / "review.yaml",
        """review_status: approved
reviewer_name: Synthetic Test Reviewer
approval: approved-for-test-2026-01-01
rule_signoff:
  cancellation.refund.full: approved
""",
    )
    _write(
        directory / "source-metadata.yaml",
        f"""local_path: source.txt
content_sha256: "{digest}"
archived: true
""",
    )
    return tmp_path


class TestEligiblePackIsAdmitted:
    def test_verified_mode_resolves_when_the_loader_accepts_the_pack(self, eligible_pack_root):
        """The whole point of G2: an eligible pack makes verified mode reachable."""
        modes = resolve_modes(
            _settings(
                policy_mode="verified",
                policy_pack_dir=str(eligible_pack_root),
                policy_pack_id="xx-test-verified",
                policy_pack_version="1.0",
            )
        )
        assert modes.policy is PolicyMode.verified

    def test_tampering_with_the_source_breaks_eligibility(self, eligible_pack_root):
        """Source integrity is not skippable: change the file and verified must refuse."""
        source = eligible_pack_root / "xx-test-verified" / "1.0" / "source.txt"
        source.write_text("tampered content that no longer matches the recorded hash\n")
        with pytest.raises(ConfigurationError, match="PACK_NOT_VERIFIED_ELIGIBLE"):
            resolve_modes(
                _settings(
                    policy_mode="verified",
                    policy_pack_dir=str(eligible_pack_root),
                    policy_pack_id="xx-test-verified",
                    policy_pack_version="1.0",
                )
            )

    def test_dropping_verified_flag_breaks_eligibility(self, eligible_pack_root):
        """verified_mode_eligible: false must refuse even with an otherwise-approved pack."""
        pack = eligible_pack_root / "xx-test-verified" / "1.0" / "pack.yaml"
        pack.write_text(
            pack.read_text().replace(
                "verified_mode_eligible: true", "verified_mode_eligible: false"
            )
        )
        with pytest.raises(ConfigurationError, match="PACK_NOT_VERIFIED_ELIGIBLE"):
            resolve_modes(
                _settings(
                    policy_mode="verified",
                    policy_pack_dir=str(eligible_pack_root),
                    policy_pack_id="xx-test-verified",
                    policy_pack_version="1.0",
                )
            )
