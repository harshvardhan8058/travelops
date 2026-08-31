"""G7 — the shell chip and the citation card must name the same instrument.

Two surfaces publish a policy pack's label: `GET /system/mode` (the shell chip, rendered on every
screen) and `GET /incidents/{id}/policy` (the citation card next to the figures). They used to
derive it two different ways — the shell composed a string from the requested `POLICY_MODE`, the
card read `LoadedPack.ui_label` — so the console could name one instrument in its chrome and a
differently-cased or differently-worded one beside the payout.

That is the defect these tests exist to prevent recurring. They assert agreement between the two
endpoints rather than either endpoint's literal text, so the packs stay free to reword their own
labels and neither surface can drift from the other.

Owner: Stream A.
"""

from __future__ import annotations

import pytest

PREFIX = "/api/v1"


def _shell_pack(client) -> dict:
    return client.get(f"{PREFIX}/system/mode").json()["policy_pack"]


def _card_pack(client, incident: str) -> dict:
    return client.get(f"{PREFIX}/incidents/{incident}/policy").json()["pack"]


class TestTheTwoSurfacesAgree:
    def test_the_label_is_identical_on_both_surfaces(self, client, incident):
        """The whole of G7 in one assertion: one pack, one label, two endpoints."""
        assert _shell_pack(client)["ui_label"] == _card_pack(client, incident)["ui_label"]

    def test_the_pack_identity_is_identical_on_both_surfaces(self, client, incident):
        """A label is only meaningful next to the pack it belongs to.

        Reporting the configured `POLICY_PACK_ID` in the shell while the card reports the pack that
        actually loaded would put one pack's name beside another pack's standing — which is the
        same class of contradiction as a mismatched label.
        """
        shell = _shell_pack(client)
        card = _card_pack(client, incident)
        assert (shell["id"], shell["version"]) == (card["id"], card["version"])

    def test_both_surfaces_agree_with_the_loaded_pack_itself(self, client, incident):
        """Neither surface is merely consistent with the other — both match the authority."""
        from app.policy.entitlements import load_active_pack

        pack = load_active_pack()
        shell = _shell_pack(client)
        card = _card_pack(client, incident)

        assert shell["ui_label"] == pack.ui_label
        assert card["ui_label"] == pack.ui_label
        assert (shell["id"], shell["version"]) == (pack.pack_id, pack.version)

    def test_the_shell_label_is_not_recased(self, client):
        """A recased label misquotes the instrument, which is how "MoCA" once became "MOCA"."""
        from app.policy.entitlements import load_active_pack

        label = _shell_pack(client)["ui_label"]
        authoritative = load_active_pack().ui_label

        assert label == authoritative
        # Explicit about the failure mode: equal-ignoring-case but unequal is precisely a recasing.
        assert not (label != authoritative and label.lower() == authoritative.lower())


class TestTheShellCannotComposeAClaim:
    def test_the_shell_label_never_originates_in_the_mode(self, client, monkeypatch):
        """Changing the reported mode must not change the label; only the pack may.

        `POLICY_MODE` selects which pack loads. It must never *be* the label, which is what the
        previous string switch made it — including a branch that composed the word "VERIFIED".
        """
        from app.policy.entitlements import load_active_pack

        expected = load_active_pack().ui_label
        body = client.get(f"{PREFIX}/system/mode").json()

        assert body["policy_pack"]["ui_label"] == expected
        # The label is the pack's sentence, not the mode token pasted into one.
        assert body["policy_pack"]["ui_label"] != body["policy_mode"]
        assert not body["policy_pack"]["ui_label"].startswith("VERIFIED · ")

    def test_an_unloadable_pack_yields_no_label_rather_than_an_invented_one(
        self, client, monkeypatch
    ):
        """Fail closed: a pack that will not load must produce an empty label, never a stand-in.

        Empty is the contract the console already renders as "policy pack unknown". The endpoint
        must keep answering — the shell is on every screen — but it must not name an instrument it
        could not read.
        """
        from app.errors import PolicyPackUnavailable

        def _unavailable(*_args, **_kwargs):
            raise PolicyPackUnavailable(
                "policy pack test@0 not found", details={"reason_code": "POLICY_PACK_UNAVAILABLE"}
            )

        # Patched where `_policy_pack_payload` looks it up, which is the entitlements module.
        monkeypatch.setattr("app.policy.entitlements.load_active_pack", _unavailable)

        response = client.get(f"{PREFIX}/system/mode")
        assert response.status_code == 200, "the shell must still render"

        pack = response.json()["policy_pack"]
        assert pack["ui_label"] == ""
        # Coordinates are still reported: they are configuration, not a claim about the pack.
        assert pack["id"]
        assert pack["version"]

    @pytest.mark.parametrize("mode", ["demo", "charter"])
    def test_the_reported_identity_is_the_pack_that_loads_not_the_one_configured(self, mode):
        """Demo resolves to the fictional fixture regardless of POLICY_PACK_ID.

        Asserted at the resolver rather than over HTTP because the app's modes are resolved once at
        import. It pins the reason the shell reads identity from the loaded pack: in demo mode the
        configured id is not the id that loads.
        """
        from app.config import Settings
        from app.policy.entitlements import active_pack_coordinates

        settings = Settings(
            _env_file=None,
            policy_mode=mode,
            policy_pack_id="in-moca-charter-2019",
            policy_pack_version="2019.02",
        )
        pack_id, _version = active_pack_coordinates(settings)

        if mode == "demo":
            assert pack_id == "demo-fixture", "demo must not be pointed at a real authority's pack"
        else:
            assert pack_id == "in-moca-charter-2019"
