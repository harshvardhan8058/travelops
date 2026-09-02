"""One rule decides who wrote a plan, and it lives on the server.

Authorship was being decided in three places at once: the orchestrator derived it inline for the
gate, the API returned a bare generator token, and the console re-derived it in the browser by
string-matching that token against two literals. The browser copy was the one that broke — it
returned "unclassified" for the committed fixture's `fallback-playbook · deterministic`, so the
Recovery Workspace told operators it could not tell who wrote a plan that was plainly the
deterministic playbook.

`authorship_for_generator` is now the single rule, and these tests pin the two properties that
matter: it agrees with the orchestrator's own constant, and it fails safe.

Owner: Stream B contract, Stream A surface.
"""

from __future__ import annotations

import pytest

from app.assurance.authorship import (
    FALLBACK_GENERATOR,
    Authorship,
    authorship_for_generator,
)
from app.orchestrator.playbook import FALLBACK_GENERATOR as PLAYBOOK_GENERATOR


def test_the_constant_matches_the_orchestrators_own_token():
    """Stated in two modules to avoid an import edge; asserted identical so it cannot drift."""
    assert FALLBACK_GENERATOR == PLAYBOOK_GENERATOR


def test_the_playbook_is_deterministic():
    assert authorship_for_generator(PLAYBOOK_GENERATOR) is Authorship.deterministic


def test_display_prose_appended_to_the_token_does_not_defeat_it():
    """The exact string the committed fixture carried, and the exact case the browser got wrong."""
    assert (
        authorship_for_generator("fallback-playbook · deterministic") is Authorship.deterministic
    )


def test_the_planner_agent_is_model_authored():
    assert authorship_for_generator("planner-agent") is Authorship.model


@pytest.mark.parametrize("generator", ["openrouter:openai/gpt-oss-120b", "groq:llama", "", None])
def test_anything_unrecognised_fails_safe_to_model(generator):
    """A stranger gets the stricter gate.

    Assuming an unknown generator is deterministic would hand it the weaker treatment, which is
    the wrong direction for a safety property to fail in.
    """
    assert authorship_for_generator(generator) is Authorship.model
