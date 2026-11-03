"""PR 11 (ADR-0072): the shared evidence flow writes the document before it presents.

``adapters.confirmation.present`` is the one evidence flow every host shares (Claude's MCP form
inside ``HostConfirmation`` and the Pi private bridge both call it), so the write-first step lives
there and both hosts get it:

1. the first presentation carries ``document_digest: None`` — the explicit fetch PR 10 staged —
   and the core refuses it with ``evidence.document_mismatch``, nothing spent, the refusal's
   private result carrying the core-rendered ``document: {text, digest}`` for the batch the
   ``key`` names;
2. the adapter verifies the pair (``core.hitl.digest`` of the encoded text), publishes the bytes
   **once** through the write-once sidecar beside the run's own record, and repeats the same
   presentation with the digest;
3. on acceptance the receipt, the items and the document's ``{digest, path}` come back, and the
   submitted receipt repeats the presentation's digest (the core requires the confirmation to name
   what its own presentation recorded).

A document that is absent or inconsistent, or a write the storage refuses, shows nothing and spends
nothing: the fetch spends no interaction and the second call never happens. The one-retry behaviour
for a conflicting batch key is unchanged: the fetch is an ordinary presentation call for staleness
purposes (a genuine batch conflict re-enters the single retry), while the fetch's *designed*
refusal — the mismatch that carries the document — is not a conflict: it neither consumes nor
triggers the retry and costs nothing.

The short Claude forms keep the quotes (the fact judged), the source lines, the decision enums and
the note fields; the research records' per-claim texts and the reasons no deterministic check
exists are in the document, pointed at by its short digest and its absolute path, while a decision
claim's own text stays in the message in full — the claim a human must decide is what is judged,
exactly like a research item's quote. The terminal consent keeps its decision fully
worded and shows the count of obligations ending now would leave unresolved, read out of the
written document itself.

Every case runs on its own ``EMPIRICA_HOME`` (``ConformanceCase``) and every store here is injected
under that case home — nothing reaches a real machine home.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from adapters import confirmation
from adapters.confirmation import HostConfirmation
from adapters.public_tools import PublicTools
from adapters.state.hitl import DocumentMismatch, HitlDocuments
from core import hitl
from core.confirmation import KIND, PRESENTATION
from test_evidence_confirmation import URL, graph_with_decision
from test_evidence_hosts import Human, MediatorFixture, accept
from application.protocol import host_profile_id

CLAUDE_PROFILE = host_profile_id("claude-code")
#: The leading ``sha256:`` of every document identity (the same length the forms render after).
_HEXAFTER = len("sha256:")


def forged(digest: str) -> str:
    """A digest one hex off (``0`` last unless the real one ends ``0``)."""
    return digest[:-1] + ("0" if digest[-1] != "0" else "1")


class WriteFirstFixture(MediatorFixture):
    """The pending batch, a store injected under the case home, and an ingress that records every
    call — the payload it carried and what the sidecar held at that moment — so the
    fetch → write → present order is asserted, not assumed."""

    def setUp(self) -> None:
        super().setUp()
        self.calls: list[dict] = []
        self.research_added = 0

    def store(self) -> HitlDocuments:
        return HitlDocuments(home=Path(self.empirica_home))

    def files(self) -> list[Path]:
        return sorted(Path(self.empirica_home).rglob("hitl/*.json"))

    def pending(self):
        """A run whose research waits on a human, with the host's surface already recorded."""
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        self.look(drv, run_id)  # Claude records its surface before the presentation it shows
        return drv, run_id

    def record(self, ingress):
        """Wrap a presentation ingress: keep every call's payload, its result, and the number of
        documents the sidecar held when it was made."""
        calls = self.calls

        def wrapped(_profile, run, payload):
            calls.append({"payload": dict(payload), "written": len(self.files())})
            answer = ingress(_profile, run, payload)
            calls[-1]["result"] = answer["result"]
            return answer
        return wrapped

    def direct(self, drv):
        return lambda _profile, run, payload: drv.trusted_evidence_presentation(run, payload)

    def show(self, drv, run_id, ingress, documents=None):
        return confirmation.present(CLAUDE_PROFILE, run_id,
                                    dispatch=lambda req, _p: drv.request(req), ingress=ingress,
                                    documents=documents)

    def artifact_rows(self, drv, run_id, kind):
        """The artifact bodies of one kind in the run's history, freshly snapshotted."""
        self.snapshot(drv, run_id)
        history = drv._service._coordinator.last_snapshot.history
        return [row for row in history if row.get("kind") == kind]


class WriteFirstPresentTests(WriteFirstFixture):
    """The fetch, the write, the digested present — in that order, with the bytes, the mode and
    the artifact bodies pinned."""

    def setUp(self) -> None:
        super().setUp()
        self._drv, self._run_id = self.pending()

    def test_present_fetches_then_writes_then_presents_with_the_digest(self):
        drv, run_id = self._drv, self._run_id
        ingress = self.record(self.direct(drv))
        before = self.left(drv, run_id)
        shown = self.show(drv, run_id, ingress, documents=self.store())
        self.assertNotIsInstance(shown, confirmation.Outcome, "the batch is shown")
        receipt, items, document = shown
        # the two calls, in order: the fetch (nothing written yet), then the bound presentation
        # made after the write — never before it
        self.assertEqual(len(self.calls), 2)
        first, second = self.calls
        self.assertIs(first["payload"]["document_digest"], None, "the first call is the fetch")
        self.assertEqual(first["payload"]["key"], second["payload"]["key"])
        self.assertEqual(first["payload"]["receipt_id"], receipt)
        self.assertEqual(first["written"], 0, "the fetch wrote nothing")
        self.assertEqual(first["result"]["type"], "Block")
        self.assertEqual(first["result"]["reasons"][0]["code"], "evidence.document_mismatch")
        self.assertEqual(second["payload"]["document_digest"], document["digest"])
        self.assertEqual(second["written"], 1, "the document was written before the accepted present")
        # the written file holds exactly the core's bytes, is read-only, and sits at its own name
        written = self.files()
        self.assertEqual(len(written), 1)
        text = first["result"]["document"]["text"]
        self.assertEqual(written[0].read_bytes(), text.encode("utf-8"))
        self.assertEqual(hitl.digest(written[0].read_bytes()), document["digest"])
        self.assertEqual(written[0].name, document["digest"][_HEXAFTER:] + ".json")
        self.assertEqual(stat.S_IMODE(os.stat(written[0]).st_mode), 0o400)
        self.assertTrue(Path(document["path"]).is_absolute())
        self.assertEqual(str(written[0]), document["path"])
        # the presentation artifact records the digest, and exactly one interaction was spent
        rows = self.artifact_rows(drv, run_id, PRESENTATION)
        self.assertEqual([row["receipt_id"] for row in rows], [receipt])
        self.assertEqual(rows[0]["document_digest"], document["digest"])
        self.assertEqual(self.left(drv, run_id), before - 1)

    def test_submit_repeats_the_presentation_digest_and_the_confirmation_records_it(self):
        drv, run_id = self._drv, self._run_id
        ingress = self.record(self.direct(drv))
        receipt, items, document = self.show(drv, run_id, ingress, documents=self.store())
        submitted = confirmation.submit(
            CLAUDE_PROFILE, run_id, receipt, items,
            [{"subject": items[0]["subject"], "id": items[0]["id"], "choice": "confirm", "note": None}],
            ingress=lambda _p, run, payload: drv.trusted_evidence_confirmation(run, payload),
            document_digest=document["digest"])
        self.assertEqual(submitted["type"], "Allow", submitted)
        self.assertEqual(self.artifact_rows(drv, run_id, PRESENTATION)[0]["document_digest"], document["digest"])
        confirmed = [row for row in self.artifact_rows(drv, run_id, KIND) if row["receipt_id"] == receipt]
        self.assertTrue(confirmed, "one receipt of the confirmed item")
        self.assertEqual({row["document_digest"] for row in confirmed}, {document["digest"]})

    # ---- nothing shown, nothing spent ---------------------------------------------------------

    def test_a_refusal_without_a_publishable_document_shows_nothing_and_spends_nothing(self):
        drv, run_id = self._drv, self._run_id
        before = self.left(drv, run_id)
        base = self.direct(drv)

        def without_document(kind):
            def ingress(_profile, run, payload):
                answer = base(_profile, run, payload)
                document = answer["result"].get("document")
                if not (answer["result"].get("type") == "Block"
                        and isinstance(document, dict)):
                    return answer
                if kind == "absent":
                    answer["result"] = {key: value for key, value in answer["result"].items()
                                        if key != "document"}
                elif kind == "wrong digest":
                    document["digest"] = forged(document["digest"])
                elif kind == "wrong bytes":
                    document["text"] = document["text"].replace("}", "}\n")
                return answer
            return ingress

        for label in ("absent", "wrong digest", "wrong bytes"):
            with self.subTest(label):
                self.calls.clear()
                ingress = self.record(without_document(label))
                shown = self.show(drv, run_id, ingress, documents=self.store())
                self.assertIs(shown, confirmation.PRESENTATION_FAILED)
                self.assertEqual(len(self.calls), 1, "the second presentation call never happened")
                self.assertEqual(self.files(), [])
                self.assertEqual(self.artifact_rows(drv, run_id, PRESENTATION), [])
                self.assertEqual(self.left(drv, run_id), before, "the fetch spends nothing")

    def test_a_write_failure_shows_nothing_spends_nothing_and_never_presents(self):
        drv, run_id = self._drv, self._run_id
        before = self.left(drv, run_id)

        class Refused:
            def __init__(self, error):
                self.error = error

            def write(self, _key, _data):
                raise self.error

        for label, error in (("storage refused", OSError("the volume is read-only")),
                             ("different bytes at the name", DocumentMismatch("published before"))):
            with self.subTest(label):
                self.calls.clear()
                ingress = self.record(self.direct(drv))
                shown = self.show(drv, run_id, ingress, documents=Refused(error))
                self.assertIs(shown, confirmation.PRESENTATION_FAILED)
                self.assertEqual(len(self.calls), 1, "the second presentation call never happened")
                self.assertEqual(self.files(), [])
                self.assertEqual(self.artifact_rows(drv, run_id, PRESENTATION), [])
                self.assertEqual(self.left(drv, run_id), before, "nothing was spent")

    # ---- the one retry for a conflicting key, and the two batches that never mix ---------------

    def test_a_conflicting_fetch_gets_exactly_the_one_retry_of_before(self):
        """The batch moves under the host between reading it and fetching it: the fetch is refused
        the same Fault ``conflict`` the single presentation call had, the single retry re-reads the
        batch, fetches, writes and presents; the fetch's own designed refusal (the mismatch) never
        consumes the retry."""
        drv, run_id = self._drv, self._run_id
        base = self.direct(drv)

        def racing(_profile, run, payload):
            if self.research_added == 0:
                self.research_added += 1
                self.research(drv, run_id, "web", URL + "/unseen")  # the batch moves under the fetch
            return base(_profile, run, payload)

        ingress = self.record(racing)
        shown = self.show(drv, run_id, ingress, documents=self.store())
        self.assertNotIsInstance(shown, confirmation.Outcome, "the retry showed the new batch")
        receipt, items, document = shown
        conflicts = [call for call in self.calls if call["result"].get("code") == "conflict"]
        self.assertEqual(len(conflicts), 1, "exactly one conflict, on the stale fetch")
        self.assertEqual(len(self.calls), 3, "conflicting fetch, new fetch, digested present")
        self.assertIs(self.calls[0]["payload"]["document_digest"], None)
        self.assertEqual(self.artifact_rows(drv, run_id, PRESENTATION)[0]["receipt_id"], receipt)
        self.assertEqual(self.artifact_rows(drv, run_id, PRESENTATION)[0]["document_digest"], document["digest"])
        self.assertEqual(len(self.files()), 1)

    def test_a_fetched_document_of_a_moved_batch_is_never_bound_to_the_new_present(self):
        """The batch moves between the fetch and the digested present: the present is refused the
        same ``conflict`` and the single retry starts over — a fresh fetch under the new key and a
        new document. That the accepted presentation shows the batch it fetched is proven by the
        core's own digest check (the receipt would not exist otherwise), and by the artifact's
        recorded key."""
        drv, run_id = self._drv, self._run_id
        base = self.direct(drv)

        def racing(_profile, run, payload):
            if payload.get("document_digest") is not None and self.research_added == 0:
                self.research_added += 1
                self.research(drv, run_id, "web", URL + "/unseen")  # moves the key under the present
            return base(_profile, run, payload)

        ingress = self.record(racing)
        shown = self.show(drv, run_id, ingress, documents=self.store())
        self.assertNotIsInstance(shown, confirmation.Outcome, "the retry showed the new batch")
        receipt, items, document = shown
        fetched = self.calls[0]["result"].get("document")
        self.assertIsInstance(fetched, dict, "the first attempt fetched its document")
        self.assertNotEqual(fetched["digest"], document["digest"], "a new batch is a new document")
        self.assertEqual(len(self.calls), 4, "fetch, conflicting present, new fetch, new present")
        self.assertIs(self.calls[2]["payload"]["document_digest"], None,
                     "the retry fetches the new batch like the first one fetched the old")
        self.assertEqual(self.calls[3]["payload"]["document_digest"], document["digest"])
        self.assertIs(self.calls[3]["result"].get("type"), "Allow")
        accepted = self.artifact_rows(drv, run_id, PRESENTATION)[0]
        self.assertEqual(accepted["receipt_id"], receipt)
        self.assertEqual(accepted["document_digest"], document["digest"])
        self.assertEqual(accepted["key"], self.calls[2]["payload"]["key"],
                         "the accepted presentation names the batch it fetched")
        self.assertEqual(len(self.files()), 2, "both documents were published beside the run")
        self.assertEqual(hitl.digest(Path(document["path"]).read_bytes()), document["digest"])


class ShortFormsTests(WriteFirstFixture):
    """ADR-0072 "What the forms keep": the quotes, the source line, the enums and the notes stay;
    the research records' per-claim texts, the reasons and the residuals are in the document the
    form points at — a needs-decision claim's own text stays in the message, because it is the
    thing the human decides."""

    def document_lines(self, message: str) -> tuple[str, str]:
        """The form's short-digest line and the exact absolute path it points at — the fallback's
        ``File:`` line (A11: with a live observer link the path is dropped, which no test here has
        without a live server). PR 19: the fallback's ``File:`` line now opens the form (within
        the lines Claude Code 2.1.304 shows) while the short digest stays past the items, so each
        line is found by its caption, never by its position."""
        lines = message.splitlines()
        document_line = next(line for line in lines if line.startswith("Document sha256:"))
        file_line = next(line for line in lines if line.startswith("File: "))
        return document_line, file_line[len("File: "):]

    def test_the_evidence_form_keeps_the_quotes_and_moves_the_rest_to_the_document(self):
        drv, run_id = self.start(graph=graph_with_decision())
        self.research(drv, run_id, "runtime", "the deploy pipeline output",
                      claim="C0", spike_unavailable="Needs production credentials.")
        self.research(drv, run_id, "web", URL, claim="C0")
        human = Human(accept())  # every item left on Later: the form is what is judged here
        self.mediate(drv, run_id, human)
        message, schema = human.shown[0]
        self.assertIn(f"web source: {URL}", message)
        self.assertIn("runtime source: the deploy pipeline output", message)
        self.assertEqual(message.count("Quote:"), 2, "each grouped item keeps its quote")
        for gone in ("Needs production credentials.", "For claim C0", "Why no deterministic check",
                     "The public contract is internally consistent."):
            self.assertNotIn(gone, message, f"{gone!r} moved to the document")
        # PR 12: a needs-decision claim's own text is the thing the human decides — it stays in
        # full, wrapped under its decision line, exactly as a research item's quote does.
        lines = message.splitlines()
        self.assertIn("1. Decision needed", message)
        decided = lines.index(next(line for line in lines if line == "1. Decision needed")) + 1
        self.assertEqual(lines[decided].strip(), "Claim C1 supports the root.",
                         "the claim text renders wrapped under its decision line")
        self.assertIn("no-deterministic-check reasons: in the document", message,
                     "one line says where the moved text lives")
        document_line, path_line = self.document_lines(message)
        self.assertRegex(document_line, r"\ADocument sha256:[0-9a-f]{12}\Z")
        file_line = next(line for line in message.splitlines() if line.startswith("File: "))
        self.assertEqual(file_line, f"File: {path_line}",
                         "the path is pasted as-is on its own line — PR 19's opening copy surface")
        self.assertTrue(Path(path_line).is_absolute(), "the path is pasted as-is")
        self.assertTrue(path_line.startswith(str(self.empirica_home)), path_line)
        self.assertTrue(path_line.endswith(".json"))
        self.assertEqual(stat.S_IMODE(os.stat(path_line).st_mode), 0o400)
        for line in message.splitlines():
            if line != file_line:
                self.assertLessEqual(len(line), 72 + 3, line)  # wrapped; the path alone is exempt
        self.assertEqual(set(schema["properties"]), {f"{prefix}{n}" for n in (1, 2, 3) for prefix in "dn"})
        self.assertEqual(schema["properties"]["d1"]["enum"], ["later", "approve", "reject"])  # the claim
        self.assertEqual(schema["properties"]["d2"]["enum"], ["later", "confirm", "reject"])
        self.assertEqual(schema["properties"]["d3"]["enum"], ["later", "confirm", "reject"])
        for number in (1, 2, 3):
            self.assertEqual(schema["properties"][f"n{number}"]["title"], f"{number}. Note (optional)")

    def test_the_terminal_form_keeps_the_decision_worded_and_names_the_residual_count(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        approving = Human(accept(d1="approve"))
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                            confirm=self.mediator(drv, approving))
        ended = tools.call_internal("report_convergence",
                                    {"run_id": run_id, "intent": "stop"})["structuredContent"]
        self.assertEqual(ended["run"]["status"], "stopped_residual", ended)
        message, schema = approving.shown[0]
        # the decision itself stays fully worded
        self.assertIn("End this run as stopped_residual, without converging?", message)
        self.assertIn("Approve to end it now", " ".join(message.split()),
                    "the decision stays fully worded")
        # the residuals are in the document; their count is read out of it
        residual_rows = [row for path in self.files()
                         for row in json.loads(path.read_bytes())["terminal"]["residuals"]]
        self.assertTrue(residual_rows, "the fixture leaves something unresolved")
        counted = (f"{len(residual_rows)} unresolved "
                   f"{'obligation' if len(residual_rows) == 1 else 'obligations'}")
        self.assertIn(counted + " would remain", message)
        self.assertEqual([row["id"] for row in residual_rows],
                         [row["id"] for row in residual_rows if row["id"] != "obligation.terminal"])
        document_line, path_line = self.document_lines(message)
        self.assertRegex(document_line, r"\ADocument sha256:[0-9a-f]{12}\Z")
        self.assertTrue(path_line.startswith(str(self.empirica_home)) and path_line.endswith(".json"))
        self.assertEqual(schema["properties"]["d1"]["enum"], ["later", "approve", "reject"])


class OneRunTwoDocumentsTests(WriteFirstFixture):
    """An evidence batch confirmed, then the terminal consent confirmed: both artifacts carry the
    digest of the file the mediation wrote for that very batch."""

    def test_the_batch_and_the_consent_each_carry_the_digest_of_their_own_written_file(self):
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        confirming = Human(accept(d1="confirm"))
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                            confirm=self.mediator(drv, confirming))
        tools.call_internal("report_convergence", {"run_id": run_id})
        self.assertEqual(self.verification(drv, run_id)["status"], "confirmed_by_human",
                         "the batch was confirmed through the form")

        def digest_rows(terminal: bool) -> list[dict]:
            self.snapshot(drv, run_id)
            rows = [row for row in drv._service._coordinator.last_snapshot.history
                    if row.get("kind") == PRESENTATION]
            return [row for row in rows if ("terminal" in row) == terminal]

        batch = digest_rows(terminal=False)
        self.assertEqual(len(batch), 1, "one batch presentation")
        batch_digest = batch[0]["document_digest"]
        confirmations = [row for row in self.artifact_rows(drv, run_id, KIND) if row["receipt_id"] == batch[0]["receipt_id"]]
        self.assertTrue(confirmations, "one confirmation of the batch")
        self.assertEqual({row["document_digest"] for row in confirmations}, {batch_digest})
        by_digest = {hitl.digest(path.read_bytes()): path for path in self.files()}
        self.assertIn(batch_digest, by_digest, "the batch document was written")
        self.assertEqual(stat.S_IMODE(os.stat(by_digest[batch_digest]).st_mode), 0o400)

        # the same run's terminal consent, through the same shared flow
        approving = Human(accept(d1="approve"))
        tools2 = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                             confirm=self.mediator(drv, approving))
        ended = tools2.call_internal("report_convergence",
                                     {"run_id": run_id, "intent": "stop"})["structuredContent"]
        self.assertEqual(ended["run"]["status"], "stopped_residual", ended)
        consent = digest_rows(terminal=True)
        self.assertEqual(len(consent), 1, "one terminal presentation")
        consent_digest = consent[0]["document_digest"]
        self.assertNotEqual(consent_digest, batch_digest, "the consent is its own document")
        terminal_confirmations = [row for row in self.artifact_rows(drv, run_id, KIND)
                                 if row["receipt_id"] == consent[0]["receipt_id"]]
        self.assertEqual({row["document_digest"] for row in terminal_confirmations}, {consent_digest})
        by_digest_after = {hitl.digest(path.read_bytes()): path for path in self.files()}
        self.assertIn(consent_digest, by_digest_after, "the consent document was written")
        self.assertEqual(hitl.digest(by_digest_after[consent_digest].read_bytes()), consent_digest)


class InjectedStoreTests(WriteFirstFixture):
    """The mediation takes the store as a constructor argument (tests inject one; the runtime
    default is the sidecar under the real ``EMPIRICA_HOME``, built at the presentation)."""

    def test_the_host_confirmation_mediation_uses_the_injected_store(self):
        drv, run_id = self.pending()
        store = self.store()
        human = Human(accept(d1="confirm"))
        mediated = HostConfirmation(
            CLAUDE_PROFILE, elicit=human, timeout=lambda: 900,
            dispatch=lambda req, _p: drv.request(req),
            presentation_ingress=lambda _p, run, payload: drv.trusted_evidence_presentation(run, payload),
            confirmation_ingress=lambda _p, run, payload: drv.trusted_evidence_confirmation(run, payload),
            surface_ingress=lambda _p, run, payload: drv.trusted_human_surface(run, payload["surface"]),
            documents=store)
        blocked = self.report(drv, run_id)
        mediated.observe(run_id)
        mediated(blocked, self.reevaluator(drv, run_id))
        self.assertEqual(self.verification(drv, run_id)["status"], "confirmed_by_human")
        written = self.files()
        self.assertEqual(len(written), 1)
        accepted = self.artifact_rows(drv, run_id, PRESENTATION)[0]
        self.assertEqual(accepted["document_digest"], hitl.digest(written[0].read_bytes()))

class RefusedConfirmationTests(WriteFirstFixture):
    """Review-2 fix 14 (finding m5, decision 7c): a Block or Fault from the submit of an
    accepted form is never the human's decline. The realistic refusal is a consent whose
    terminal residuals move while the dialog is open (here a mid-dialog audit settles), so
    the receipt the form re-sends names a document the core no longer renders and the
    submit is refused with nothing admitted; the fresh evaluation must surface the host's
    own failure (``host_failed``, fixed only by showing the batch again) and record no
    confirmation artifact — never "You declined the running question"."""

    def _confirmed_run_with_pending_audit(self):
        """A run whose research was confirmed by a human and whose audit child sits pending,
        so the terminal consent resolves and the audit obligation is the one residual."""
        drv, run_id = self.start()
        self.research(drv, run_id, "web", URL)
        confirming = Human(accept(d1="confirm"))
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                            confirm=self.mediator(drv, confirming))
        tools.call_internal("report_convergence", {"run_id": run_id})
        self.assertEqual(self.verification(drv, run_id)["status"], "confirmed_by_human",
                         "the batch answered on the way in is confirmed (prerequisite)")
        # The audit child is admitted before any evidence waits on the human again, then
        # held pending across the consent dialog: settling it through the trusted ingress
        # mid-dialog is the residual move the consent document reads out.
        child_id = self.require_pending_audit_child(drv, run_id)
        residuals = [row["id"] for row in hitl.terminal_residuals(self.run_view(drv, run_id))]
        self.assertEqual(residuals, ["obligation.audit"],
                         "the audit obligation is the one residual the consent names (prerequisite)")
        return drv, run_id, child_id

    def _consent(self, drv, run_id, confirmation_ingress):
        """Report a stop mediated by the given confirmation ingress; the capture asserts the
        receipt the form submitted, for the nothing-was-recorded checks."""
        submitted: list[dict] = []

        def recorded(_profile, run, payload):
            submitted.append(dict(payload))
            return confirmation_ingress(_profile, run, payload)

        mediator = HostConfirmation(
            CLAUDE_PROFILE, elicit=Human(accept(d1="approve")), timeout=lambda: 900,
            dispatch=lambda req, _p: drv.request(req),
            presentation_ingress=lambda _p, run, payload: drv.trusted_evidence_presentation(run, payload),
            confirmation_ingress=recorded,
            surface_ingress=lambda _p, run, payload: drv.trusted_human_surface(run, payload["surface"]),
            documents=self.store())
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req), confirm=mediator)
        ended = tools.call_internal("report_convergence",
                                    {"run_id": run_id, "intent": "stop"})["structuredContent"]
        return ended, submitted

    def _terminal_pending_reason(self, ended):
        candidates = [reason for reason in ended.get("reasons", [])
                      if reason.get("code") == "governance.terminal_stop_pending"]
        self.assertEqual(len(candidates), 1, "the consent's refusal is surfaced once")
        reason = candidates[0]
        return reason, reason.get("parameters", {}).get("case")

    def test_a_submit_refused_because_the_residuals_moved_is_never_the_humans_decline(self):
        drv, run_id, child_id = self._confirmed_run_with_pending_audit()

        def settle_mid_dialog(_profile, run, payload):
            # The residuals move while the consent dialog is open: the audit settles, so the
            # receipt the form re-sends names a document the core no longer renders.
            self.require_trusted_audit_attribution(drv, run, child_id, variant="distinct")
            verdict = self.build_audit_verdict_payload(drv, run, verdict="pass")
            settled = drv.trusted_audit_verdict(run, child_id, verdict)["result"]
            self.assertEqual(settled.get("type"), "Allow", "the audit settles mid-dialog (prerequisite)")
            refused = drv.trusted_evidence_confirmation(run, payload)["result"]
            self.assertEqual(refused.get("type"), "Block",
                             "the moved-residuals submit is refused (prerequisite)")
            return {"result": refused}

        ended, submitted = self._consent(drv, run_id, settle_mid_dialog)
        reason, case = self._terminal_pending_reason(ended)
        # The approval was refused, not declined: the fresh evaluation names the host's own
        # failure, never the human's word, and the recovery is a new showing of the batch.
        self.assertEqual(case, "host_failed",
                         "a refused submit is the host's failure to record an answer, not a decline")
        self.assertIn("Stop again", reason.get("message", ""),
                      "the refusal is recovered by showing the batch again, never by repeating the confirm")
        # Nobody's answer was recorded: the run stays active without the consent receipt.
        self.assertEqual(self.run_view(drv, run_id)["status"], "active",
                         "no refused approval ended the run")
        recorded = self.artifact_rows(drv, run_id, KIND)
        self.assertFalse([row for row in recorded if row["receipt_id"] == submitted[0]["receipt_id"]],
                         "the refused consent recorded no confirmation artifact")
        # And the same human, shown the fresh document, still ends the run: the refusal was
        # of the stale receipt, and only a new presentation carries an answer that counts.
        settling = self.mediator(drv, Human(accept(d1="approve")))
        tools = PublicTools(CLAUDE_PROFILE, dispatch=lambda req, _p: drv.request(req),
                            confirm=settling)
        ended_again = tools.call_internal("report_convergence",
                                          {"run_id": run_id, "intent": "stop"})["structuredContent"]
        if ended_again["run"].get("status") == "active":  # the consent opened again: answer it
            ended_again = tools.call_internal("report_convergence",
                                              {"run_id": run_id, "intent": "stop"})["structuredContent"]
        self.assertEqual(ended_again["run"]["status"], "stopped_residual",
                         "the fresh showing's approval ends the run")

    def test_a_fault_from_the_submit_records_nothing_and_never_reads_as_a_decline(self):
        drv, run_id, _child_id = self._confirmed_run_with_pending_audit()

        def faulting(_profile, _run, _payload):
            return {"result": {"type": "Fault", "code": "conflict"}}

        ended, submitted = self._consent(drv, run_id, faulting)
        _reason, case = self._terminal_pending_reason(ended)
        self.assertEqual(case, "host_failed",
                         "a transport fault while submitting is no human's decline")
        self.assertEqual(self.run_view(drv, run_id)["status"], "active")
        recorded = self.artifact_rows(drv, run_id, KIND)
        self.assertFalse([row for row in recorded if row["receipt_id"] == submitted[0]["receipt_id"]],
                         "a faulted submit recorded no confirmation artifact")


if __name__ == "__main__":
    import unittest
    unittest.main()
