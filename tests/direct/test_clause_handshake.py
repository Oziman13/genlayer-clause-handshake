"""Clause Handshake v0.1 tests (genlayer-test direct mode: in-memory, model mocked).

Run from the repository root:  python3 -m pytest tests/direct
"""

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest
from gltest.direct import create_address

CONTRACT = "contracts/clause_handshake.py"
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
HOUR, DAY = 3600, 86400


def set_time(vm, seconds=0):
    stamp = (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    vm.warp(stamp)
    module = sys.modules.get("genlayer.gl")
    if module is not None:
        module.message_raw["datetime"] = stamp


def low(address):
    return "0x" + bytes(address).hex() if isinstance(address, (bytes, bytearray)) else str(address).lower()


def deploy(vm, deploy_fn, owner):
    vm.sender = owner
    set_time(vm, 0)
    c = deploy_fn(CONTRACT)
    set_time(vm, 0)
    return c


class Deal:
    """Helper around one deal with two parties; every call sets the sender and the time first."""

    def __init__(self, vm, c, a, b, topic="Delivery terms for the autumn website job"):
        self.vm, self.c, self.a, self.b = vm, c, a, b
        vm.sender = a
        set_time(vm, 0)
        self.id = json.loads(c.open_deal(low(b), topic))["id"]
        self.now = 0

    def at(self, seconds):
        self.now = seconds
        return self

    def call(self, who, name, *args):
        self.vm.sender = who
        set_time(self.vm, self.now)
        return getattr(self.c, name)(self.id, *args)

    def propose(self, who, text):
        return json.loads(self.call(who, "propose", text))

    def respond(self, who, accept):
        return json.loads(self.call(who, "respond", accept))

    def sign(self, who):
        return json.loads(self.call(who, "sign"))

    def state(self):
        return json.loads(self.c.get_deal_json(self.id))


def clause(token, body="The supplier delivers the finished work"):
    return "%s (%s)." % (body, token)


def answer(conflict=False, number=0, quote=""):
    return json.dumps({"conflict": conflict, "clause": number, "quote": quote})


def mock_for(vm, token, reply):
    vm.mock_llm("BEGIN NEW CLAUSE ---\\n[^\\n]*" + token, reply)


@pytest.fixture
def two(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    return Deal(direct_vm, c, direct_alice, direct_bob)


def lock_one(d, text="The supplier delivers the finished work within thirty days (T1)."):
    d.propose(d.a, text)
    d.respond(d.b, True)
    return text


# -- opening ----------------------------------------------------------------------------------------------------------------

def test_deploy_is_empty(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    assert c.get_deal_count() == 0
    assert json.loads(c.get_deals_page(0, 10)) == {"items": [], "total": 0}
    assert c.get_deal_json(0) == "NONE" and c.get_agreement_json(0) == "NONE" and c.get_deal_json(-1) == "NONE"
    assert json.loads(c.get_deals_for_json(low(direct_owner))) == []


def test_a_new_deal(two, direct_alice, direct_bob):
    deal = two.state()
    assert deal["id"] == 0 and deal["a"] == low(direct_alice) and deal["b"] == low(direct_bob)
    assert deal["phase"] == "NEGOTIATING" and deal["turn"] == "a" and deal["pending"] is None
    assert deal["locked"] == [] and deal["history"] == [] and deal["signatures"] == {"a": None, "b": None}
    assert deal["rounds"] == 0 and deal["agreement"] is None
    assert deal["expires"] == deal["opened"] + 7 * DAY and deal["opened"] == int(T0.timestamp())
    assert deal["topic"] == "Delivery terms for the autumn website job"


def test_topic_is_squashed_and_limited(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.sender = direct_alice
    c.open_deal(low(direct_bob), "  A   topic\nwith   gaps  ")
    assert json.loads(c.get_deal_json(0))["topic"] == "A topic with gaps"
    c.open_deal(low(direct_bob), "x" * 5)
    c.open_deal(low(direct_bob), "y" * 80)
    for bad in ("abcd", "z" * 81, "", "   ", "bad\x01topic here", 12):
        with direct_vm.expect_revert("invalid topic"):
            c.open_deal(low(direct_bob), bad)


@pytest.mark.parametrize("other", ["", "0x12", "bob", "0x" + "z" * 40, 5])
def test_the_other_party_must_be_an_address(direct_vm, direct_deploy, direct_owner, direct_alice, other):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("invalid address"):
        c.open_deal(other, "A topic that is long enough")


def test_you_cannot_open_a_deal_with_yourself_in_any_letter_case(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("the other party must be a different address"):
        c.open_deal("  " + low(direct_alice).upper().replace("0X", "0x") + " ", "A topic that is long enough")


def test_the_board_holds_one_hundred_deals(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.sender = direct_alice
    for _ in range(100):
        c.open_deal(low(direct_bob), "A topic that is long enough")
    with direct_vm.expect_revert("the board is full"):
        c.open_deal(low(direct_bob), "A topic that is long enough")


# -- proposing and answering (no model while nothing is locked) --------------------------------------------------------------------

def test_the_first_clause_needs_no_model(two, direct_alice, direct_bob):
    out = two.propose(direct_alice, "The supplier delivers the finished work within thirty days (T1).")
    assert out == {"status": "PENDING"}
    deal = two.state()
    assert deal["pending"]["by"] == low(direct_alice) and deal["pending"]["text"].startswith("The supplier delivers")
    assert deal["rounds"] == 1 and [h["kind"] for h in deal["history"]] == ["PROPOSED"]


def test_only_the_party_on_turn_proposes(two, direct_alice, direct_bob, direct_charlie):
    with two.vm.expect_revert("not your turn"):
        two.propose(direct_bob, "The buyer pays within fourteen days of delivery (T2).")
    with two.vm.expect_revert("not a party to this deal"):
        two.propose(direct_charlie, "The buyer pays within fourteen days of delivery (T2).")
    assert two.state()["history"] == []


def test_a_waiting_clause_blocks_a_second_one(two, direct_alice, direct_bob):
    two.propose(direct_alice, "The supplier delivers the finished work within thirty days (T1).")
    with two.vm.expect_revert("a clause is waiting for an answer"):
        two.propose(direct_alice, "The buyer pays within fourteen days of delivery (T2).")
    with two.vm.expect_revert("a clause is waiting for an answer"):
        two.propose(direct_bob, "The buyer pays within fourteen days of delivery (T2).")


def test_you_cannot_answer_your_own_clause(two, direct_alice, direct_bob, direct_charlie):
    two.propose(direct_alice, "The supplier delivers the finished work within thirty days (T1).")
    with two.vm.expect_revert("you cannot answer your own clause"):
        two.respond(direct_alice, True)
    with two.vm.expect_revert("not a party to this deal"):
        two.respond(direct_charlie, True)


def test_nothing_to_answer(two, direct_bob):
    with two.vm.expect_revert("nothing is waiting for an answer"):
        two.respond(direct_bob, True)


@pytest.mark.parametrize("value", [1, 0, "yes", None])
def test_the_answer_must_be_a_real_boolean(two, direct_alice, direct_bob, value):
    two.propose(direct_alice, "The supplier delivers the finished work within thirty days (T1).")
    with two.vm.expect_revert("answer must be true or false"):
        two.call(direct_bob, "respond", value)


def test_accepting_locks_the_clause_and_hands_the_turn_to_the_responder(two, direct_alice, direct_bob):
    text = "The supplier delivers the finished work within thirty days (T1)."
    two.propose(direct_alice, text)
    assert two.respond(direct_bob, True) == {"status": "LOCKED"}
    deal = two.state()
    assert deal["locked"] == [{"text": text, "proposed_by": low(direct_alice), "accepted_by": low(direct_bob), "at": int(T0.timestamp())}]
    assert deal["pending"] is None and deal["turn"] == "b"
    assert [h["kind"] for h in deal["history"]] == ["PROPOSED", "LOCKED"]
    with two.vm.expect_revert("not your turn"):
        two.propose(direct_alice, "The buyer pays within fourteen days of delivery (T2).")


def test_rejecting_keeps_the_text_in_the_history_and_gives_the_responder_the_turn(two, direct_alice, direct_bob):
    text = "The supplier delivers the finished work within thirty days (T1)."
    two.propose(direct_alice, text)
    assert two.respond(direct_bob, False) == {"status": "REJECTED"}
    deal = two.state()
    assert deal["locked"] == [] and deal["pending"] is None and deal["turn"] == "b"
    assert deal["history"][-1]["kind"] == "REJECTED" and deal["history"][-1]["text"] == text
    assert two.propose(direct_bob, "The supplier delivers within forty-five days (T9).") == {"status": "PENDING"}


def test_clause_text_is_squashed_and_limited(two, direct_alice):
    two.propose(direct_alice, "  The   supplier\ndelivers   the work (T1).  ")
    assert two.state()["pending"]["text"] == "The supplier delivers the work (T1)."


@pytest.mark.parametrize("bad", ["short clause", "x" * 301, "", "   ", "A clause with a control\x07 character in it", 99])
def test_bad_clauses_are_refused(two, direct_alice, bad):
    with two.vm.expect_revert("invalid clause"):
        two.propose(direct_alice, bad)


def test_clause_length_limits_are_inclusive(two, direct_alice, direct_bob):
    two.propose(direct_alice, "x" * 15)
    two.respond(direct_bob, False)
    two.propose(direct_bob, "y" * 300)
    assert len(two.state()["pending"]["text"]) == 300


def test_an_agreed_clause_cannot_be_proposed_again(two, direct_alice, direct_bob):
    text = lock_one(two)
    mock_for(two.vm, "T1", answer())
    with two.vm.expect_revert("already agreed"):
        two.propose(direct_bob, "  " + text.upper() + "  ")
    two.vm.clear_mocks()


def test_unknown_deals_are_refused(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.sender = direct_alice
    for deal_id in (0, 4, -1, "0"):
        for call in (lambda: c.propose(deal_id, "A clause that is long enough"), lambda: c.respond(deal_id, True),
                     lambda: c.sign(deal_id), lambda: c.withdraw(deal_id)):
            with direct_vm.expect_revert("unknown deal"):
                call()


# -- the conflict check -------------------------------------------------------------------------------------------------------------

LOCKED_1 = "The supplier delivers the finished work within thirty days (T1)."


def second_clause(two, token="T2", reply=None, text=None):
    """T1 is locked with Bob to move; Bob offers a second clause, the model answers `reply`."""
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    if reply is not None:
        mock_for(two.vm, token, reply)
    return two.propose(two.b, text or "The delivery is made in sixty days after the order (%s)." % token)


def test_a_compatible_clause_becomes_pending(two):
    assert second_clause(two, reply=answer(False)) == {"status": "PENDING"}
    deal = two.state()
    assert deal["pending"]["by"] == low(two.b) and deal["rounds"] == 2 and deal["turn"] == "b"


def test_a_contradicting_clause_is_blocked_and_recorded(two):
    out = second_clause(two, reply=answer(True, 1, "within thirty days"))
    assert out == {"status": "BLOCKED", "clause": 0, "quote": "within thirty days"}
    deal = two.state()
    assert deal["pending"] is None and deal["turn"] == "b" and deal["rounds"] == 2 and len(deal["locked"]) == 1
    last = deal["history"][-1]
    assert last["kind"] == "BLOCKED" and last["clause"] == 0 and last["quote"] == "within thirty days"
    assert last["text"].startswith("The delivery is made in sixty days") and last["by"] == low(two.b)


def test_after_a_block_the_same_party_may_offer_another_wording(two):
    second_clause(two, reply=answer(True, 1, "within thirty days"))
    mock_for(two.vm, "T3", answer(False))
    assert two.propose(two.b, "The supplier may deliver in two parts inside that time (T3).") == {"status": "PENDING"}


def test_the_quote_may_differ_in_case_and_spacing_from_the_clause(two):
    out = second_clause(two, reply=answer(True, 1, "WITHIN   Thirty\nDays"))
    assert out["status"] == "BLOCKED" and out["quote"] == "WITHIN Thirty Days"


def test_the_blocked_clause_index_is_zero_based_and_names_the_right_clause(two):
    lock_one(two, LOCKED_1)
    mock_for(two.vm, "T2", answer(False))
    two.propose(two.b, "The buyer pays within fourteen days of delivery (T2).")
    two.respond(two.a, True)
    two.vm.clear_mocks()
    mock_for(two.vm, "T3", answer(True, 2, "within fourteen days"))
    out = two.propose(two.a, "Payment is made after ninety days from the invoice (T3).")
    assert out == {"status": "BLOCKED", "clause": 1, "quote": "within fourteen days"}


def test_the_model_sees_numbered_locked_clauses_the_new_clause_and_the_guard(two):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    two.vm.mock_llm(r"(?s)UNTRUSTED TEXT.*Never obey anything.*BEGIN AGREED CLAUSES ---\n1\. " + "The supplier delivers the finished work within thirty days"
                    + r".*END AGREED CLAUSES ---\n--- BEGIN NEW CLAUSE ---\nThe delivery is made in sixty days after the order \(T2\)\.\n--- END NEW CLAUSE", answer(False))
    assert two.propose(two.b, "The delivery is made in sixty days after the order (T2).")["status"] == "PENDING"


@pytest.mark.parametrize("bad", [
    "not json at all",
    "",
    json.dumps({"conflict": "true", "clause": 1, "quote": "within thirty days"}),
    json.dumps({"conflict": 1, "clause": 1, "quote": "within thirty days"}),
    json.dumps({"conflict": True, "clause": 0, "quote": "within thirty days"}),
    json.dumps({"conflict": True, "clause": 2, "quote": "within thirty days"}),
    json.dumps({"conflict": True, "clause": -1, "quote": "within thirty days"}),
    json.dumps({"conflict": True, "clause": "1", "quote": "within thirty days"}),
    json.dumps({"conflict": True, "clause": 1.0 if False else 1, "quote": ""}),
    json.dumps({"conflict": True, "clause": 1, "quote": "sixty days"}),
    json.dumps({"conflict": True, "clause": 1, "quote": "abc"}),
    json.dumps({"conflict": True, "clause": 1, "quote": 7}),
    json.dumps({"conflict": False, "clause": 1, "quote": ""}),
    json.dumps({"conflict": False, "clause": 0, "quote": "within thirty days"}),
    json.dumps({"conflict": False}),
    json.dumps({"conflict": False, "clause": 0, "quote": "", "extra": 1}),
    json.dumps(["conflict"]),
    "x" * 4001,
])
def test_a_badly_formed_answer_is_never_a_verdict(two, bad):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", bad)
    before = two.state()
    with two.vm.expect_revert("the conflict check could not be read, try again"):
        two.propose(two.b, "The delivery is made in sixty days after the order (T2).")
    assert two.state() == before


def test_the_quote_must_be_four_characters_or_more(two):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(True, 1, "day"))
    with two.vm.expect_revert("could not be read"):
        two.propose(two.b, "The delivery is made in sixty days after the order (T2).")
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(True, 1, "days"))
    assert two.propose(two.b, "The delivery is made in sixty days after the order (T2).")["status"] == "BLOCKED"


def test_the_quote_may_be_three_hundred_characters_but_not_more(two):
    long_clause = "Word " * 59 + "end (T1)."          # 304 characters is too long for a clause, so build one of 300
    long_clause = (("Delivery rule " * 30)[:290] + " (T1).").strip()
    assert 15 <= len(long_clause) <= 300
    lock_one(two, long_clause)
    quote = " ".join(long_clause.split())[:300]
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(True, 1, quote))
    assert two.propose(two.b, "The delivery is made in sixty days after the order (T2).")["status"] == "BLOCKED"
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(True, 1, quote + " and more"))
    with two.vm.expect_revert("could not be read"):
        two.propose(two.b, "The delivery is made in sixty days after the order (T2).")


def test_a_boolean_is_not_a_clause_number(two):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", json.dumps({"conflict": True, "clause": True, "quote": "within thirty days"}))
    with two.vm.expect_revert("could not be read"):
        two.propose(two.b, "The delivery is made in sixty days after the order (T2).")


def test_a_quote_cannot_be_borrowed_from_another_clause(two):
    lock_one(two, LOCKED_1)
    mock_for(two.vm, "T5", answer(False))
    two.propose(two.b, "The buyer pays within fourteen days of delivery (T5).")
    two.respond(two.a, True)
    two.vm.clear_mocks()
    mock_for(two.vm, "T3", answer(True, 1, "within fourteen days"))      # those words are in clause 2, not clause 1
    with two.vm.expect_revert("the conflict check could not be read, try again"):
        two.propose(two.a, "Payment is made after ninety days from the invoice (T3).")


def test_the_model_can_answer_with_an_object_instead_of_text(two):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", {"conflict": False, "clause": 0, "quote": ""})
    assert two.propose(two.b, "The delivery is made in sixty days after the order (T2).")["status"] == "PENDING"


def test_a_failed_check_costs_no_round(two):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", "garbage")
    with two.vm.expect_revert("could not be read"):
        two.propose(two.b, "The delivery is made in sixty days after the order (T2).")
    assert two.state()["rounds"] == 1


# -- the validators ------------------------------------------------------------------------------------------------------------------

def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


NONE = {"state": "OK", "conflict": False}
HIT = {"state": "OK", "conflict": True, "clause": 0, "quote": "within thirty days"}


def checked_proposal(two, reply):
    lock_one(two, LOCKED_1)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", reply)
    two.propose(two.b, "The delivery is made in sixty days after the order (T2).")


def test_validator_accepts_the_same_no_conflict(two):
    checked_proposal(two, answer(False))
    assert two.vm.run_validator() is True


def test_validator_accepts_the_same_conflict_even_with_other_words(two):
    checked_proposal(two, answer(True, 1, "within thirty days"))
    assert two.vm.run_validator() is True
    assert two.vm.run_validator(leader_result=canon({"state": "OK", "conflict": True, "clause": 0, "quote": "thirty days"})) is True


def test_validator_rejects_a_conflict_its_own_model_does_not_see(two):
    checked_proposal(two, answer(False))
    assert two.vm.run_validator(leader_result=canon(HIT)) is False


def test_validator_rejects_a_missed_conflict(two):
    checked_proposal(two, answer(True, 1, "within thirty days"))
    assert two.vm.run_validator(leader_result=canon(NONE)) is False


def test_validator_rejects_a_different_clause(two):
    lock_one(two, LOCKED_1)
    mock_for(two.vm, "T5", answer(False))
    two.propose(two.b, "The buyer pays within fourteen days of delivery (T5).")
    two.respond(two.a, True)
    two.vm.clear_mocks()
    mock_for(two.vm, "T3", answer(True, 2, "within fourteen days"))
    two.propose(two.a, "Payment is made after ninety days from the invoice (T3).")
    assert two.vm.run_validator() is True
    other = {"state": "OK", "conflict": True, "clause": 0, "quote": "within thirty days"}
    assert two.vm.run_validator(leader_result=canon(other)) is False


@pytest.mark.parametrize("forged", [
    {"state": "OK", "conflict": True, "clause": 0, "quote": "sixty days"},           # words not in the clause
    {"state": "OK", "conflict": True, "clause": 3, "quote": "within thirty days"},   # no such clause
    {"state": "OK", "conflict": True, "clause": -1, "quote": "within thirty days"},
    {"state": "OK", "conflict": True, "clause": 0, "quote": ""},
    {"state": "OK", "conflict": True, "clause": 0},
    {"state": "OK", "conflict": False, "clause": 0, "quote": ""},
    {"state": "OK", "conflict": "yes"},
    {"state": "MAYBE"},
    {"state": "OK", "conflict": True, "clause": 0, "quote": "Within  thirty days"},   # not in canonical form
])
def test_validator_rejects_forged_readings(two, forged):
    checked_proposal(two, answer(True, 1, "within thirty days"))
    assert two.vm.run_validator(leader_result=canon(forged)) is False


def test_validator_rejects_text_that_is_not_the_canonical_json(two):
    checked_proposal(two, answer(False))
    assert two.vm.run_validator(leader_result=json.dumps(NONE, indent=2)) is False
    assert two.vm.run_validator(leader_result="not json") is False
    assert two.vm.run_validator(leader_result=5) is False
    assert two.vm.run_validator(leader_result="x" * 2001) is False


def test_validator_rejects_an_error_when_it_can_read(two):
    checked_proposal(two, answer(False))
    assert two.vm.run_validator(leader_result=canon({"state": "ERROR"})) is False


def test_validator_rejects_a_reading_when_its_own_model_fails(two):
    checked_proposal(two, answer(False))
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", "garbage")
    assert two.vm.run_validator() is False


def test_validator_accepts_error_when_it_cannot_read_either(two):
    checked_proposal(two, answer(False))
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", "garbage")
    assert two.vm.run_validator(leader_result=canon({"state": "ERROR"})) is True


# -- signing and agreement --------------------------------------------------------------------------------------------------------

def digest(texts):
    return hashlib.sha256(json.dumps(texts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_nothing_to_sign_before_a_clause_is_locked(two, direct_alice):
    with two.vm.expect_revert("nothing is locked yet"):
        two.sign(direct_alice)


def test_no_signing_while_a_clause_waits(two, direct_alice, direct_bob):
    lock_one(two)
    two.propose(two.b, "The delivery is made in sixty days after the order (T2).") if False else None
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(False))
    two.propose(two.b, "The buyer pays within fourteen days of delivery (T2).")
    with two.vm.expect_revert("a clause is waiting for an answer"):
        two.sign(direct_alice)


def test_both_signatures_on_the_same_list_agree_the_deal(two, direct_alice, direct_bob):
    text = lock_one(two)
    expected = digest([text])
    assert two.sign(direct_alice) == {"status": "SIGNED", "hash": expected}
    assert two.state()["phase"] == "NEGOTIATING" and two.c.get_agreement_json(two.id) == "NONE"
    two.at(HOUR)
    assert two.sign(direct_bob) == {"status": "AGREED", "hash": expected}
    deal = two.state()
    assert deal["phase"] == "AGREED" and deal["signatures"] == {"a": expected, "b": expected}
    assert json.loads(two.c.get_agreement_json(two.id)) == {"hash": expected, "at": int(T0.timestamp()) + HOUR, "clauses": [text]}
    assert [h["kind"] for h in deal["history"]] == ["PROPOSED", "LOCKED", "SIGNED", "SIGNED"]


def test_the_hash_covers_every_clause_in_order(two):
    lock_one(two)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(False))
    second = "The buyer pays within fourteen days of delivery (T2)."
    two.propose(two.b, second)
    two.respond(two.a, True)
    assert two.sign(two.a)["hash"] == digest([LOCKED_1, second])
    assert two.sign(two.a if False else two.b)["status"] == "AGREED"


def test_one_party_cannot_sign_twice(two, direct_alice):
    lock_one(two)
    two.sign(direct_alice)
    with two.vm.expect_revert("already signed"):
        two.sign(direct_alice)


def test_locking_a_new_clause_clears_both_signatures(two, direct_alice, direct_bob):
    lock_one(two)
    two.sign(direct_alice)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(False))
    two.propose(two.b, "The buyer pays within fourteen days of delivery (T2).")
    with two.vm.expect_revert("a clause is waiting for an answer"):
        two.sign(direct_bob)
    two.respond(two.a, True)
    assert two.state()["signatures"] == {"a": None, "b": None}
    assert two.sign(direct_bob)["status"] == "SIGNED"          # Bob alone is not enough: Alice signed the old list
    assert two.state()["phase"] == "NEGOTIATING"
    assert two.sign(direct_alice)["status"] == "AGREED"


def test_a_rejected_clause_does_not_clear_signatures(two, direct_alice, direct_bob):
    lock_one(two)
    two.sign(direct_alice)
    two.vm.clear_mocks()
    mock_for(two.vm, "T2", answer(False))
    two.propose(two.b, "The buyer pays within fourteen days of delivery (T2).")
    two.respond(two.a, False)
    assert two.state()["signatures"]["a"] == digest([LOCKED_1])
    assert two.sign(direct_bob)["status"] == "AGREED"


def test_an_agreed_deal_is_closed(two, direct_alice, direct_bob):
    lock_one(two)
    two.sign(direct_alice)
    two.sign(direct_bob)
    for call in (lambda: two.propose(two.b, "The delivery is made in sixty days (T2)."), lambda: two.respond(two.a, True),
                 lambda: two.sign(two.a), lambda: two.call(two.a, "withdraw")):
        with two.vm.expect_revert("the deal is closed"):
            call()


def test_a_non_party_cannot_sign_or_withdraw(two, direct_charlie):
    lock_one(two)
    with two.vm.expect_revert("not a party to this deal"):
        two.sign(direct_charlie)
    with two.vm.expect_revert("not a party to this deal"):
        two.call(direct_charlie, "withdraw")


# -- withdrawing, expiry and limits --------------------------------------------------------------------------------------------------

def test_either_party_can_withdraw_and_it_is_final(two, direct_alice, direct_bob):
    two.propose(direct_alice, LOCKED_1)
    two.call(direct_bob, "withdraw")
    deal = two.state()
    assert deal["phase"] == "WITHDRAWN" and deal["pending"] is None and deal["history"][-1]["kind"] == "WITHDRAWN"
    assert deal["history"][-1]["by"] == low(direct_bob)
    with two.vm.expect_revert("the deal is closed"):
        two.respond(direct_bob, True)
    with two.vm.expect_revert("the deal is closed"):
        two.call(direct_alice, "withdraw")


def test_the_last_second_of_a_deal(two, direct_alice, direct_bob):
    two.at(7 * DAY - 1)
    assert two.propose(direct_alice, LOCKED_1) == {"status": "PENDING"}
    two.at(7 * DAY)
    for call in (lambda: two.respond(direct_bob, True), lambda: two.propose(direct_alice, "Another clause that is long enough"),
                 lambda: two.sign(direct_alice), lambda: two.call(direct_alice, "withdraw")):
        with two.vm.expect_revert("the deal has expired"):
            call()
    assert two.state()["phase"] == "NEGOTIATING"


def test_a_deal_holds_six_locked_clauses(two, direct_alice, direct_bob):
    two.vm.clear_mocks()
    parties = [two.a, two.b]
    for i in range(6):
        who = parties[i % 2]
        other = parties[(i + 1) % 2]
        if i:
            mock_for(two.vm, "N%d" % i, answer(False))
        # the responder of the previous round has the turn: a locks first (b answers), then b proposes, a answers, ...
        proposer = two.a if i == 0 else (two.b if i % 2 == 1 else two.a)
        responder = two.b if proposer == two.a else two.a
        assert two.propose(proposer, "Distinct clause number %d about the job (N%d)." % (i, i))["status"] == "PENDING"
        two.respond(responder, True)
    assert len(two.state()["locked"]) == 6
    with two.vm.expect_revert("the deal is full"):
        mock_for(two.vm, "N7", answer(False))
        two.propose(two.state()["turn"] == "a" and two.a or two.b, "Distinct clause number seven about the job (N7).")


def test_offers_are_limited_to_thirty_but_answers_and_signatures_never_are(two, direct_alice, direct_bob):
    two.vm.clear_mocks()
    for i in range(29):                                  # 29 offers that are rejected: the turn passes each time
        proposer = two.a if i % 2 == 0 else two.b
        responder = two.b if proposer == two.a else two.a
        two.propose(proposer, "Rejected offer number %d for the job (R%d)." % (i, i))
        two.respond(responder, False)
    assert two.state()["rounds"] == 29
    proposer = two.a if two.state()["turn"] == "a" else two.b
    responder = two.b if proposer == two.a else two.a
    two.propose(proposer, "The thirtieth and last offer for the job (R30).")
    assert two.state()["rounds"] == 30
    assert two.respond(responder, True) == {"status": "LOCKED"}      # the answer is never refused
    with two.vm.expect_revert("round limit reached"):
        two.propose(responder, "A thirty-first offer for the job (R31).")
    assert two.sign(two.a)["status"] == "SIGNED" and two.sign(two.b)["status"] == "AGREED"


def test_a_blocked_clause_counts_as_an_offer(two):
    second_clause(two, reply=answer(True, 1, "within thirty days"))
    assert two.state()["rounds"] == 2


# -- reading --------------------------------------------------------------------------------------------------------------------------

def test_summaries_pages_and_party_lookup(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob, direct_charlie):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    first = Deal(direct_vm, c, direct_alice, direct_bob, topic="First topic for the pages")
    second = Deal(direct_vm, c, direct_bob, direct_charlie, topic="Second topic for the pages")
    first.propose(direct_alice, LOCKED_1)
    page = json.loads(c.get_deals_page(0, 10))
    assert page["total"] == 2 and [d["id"] for d in page["items"]] == [0, 1]
    assert page["items"][0]["pending"] is True and page["items"][0]["locked"] == 0 and "history" not in page["items"][0]
    assert [d["id"] for d in json.loads(c.get_deals_for_json(low(direct_alice)))] == [0]
    assert [d["id"] for d in json.loads(c.get_deals_for_json(low(direct_bob)))] == [0, 1]
    assert [d["id"] for d in json.loads(c.get_deals_for_json(low(direct_charlie)))] == [1]
    assert json.loads(c.get_deals_page(1, 1))["items"][0]["id"] == 1 and json.loads(c.get_deals_page(5, 1))["items"] == []
    for offset, limit in ((-1, 5), (0, 0), (0, 51), ("0", 5)):
        with direct_vm.expect_revert("bad page"):
            c.get_deals_page(offset, limit)
    with direct_vm.expect_revert("invalid address"):
        c.get_deals_for_json("0x12")


def test_a_refused_call_changes_nothing(two, direct_alice, direct_bob):
    two.propose(direct_alice, LOCKED_1)
    before = two.state()
    with two.vm.expect_revert("you cannot answer your own clause"):
        two.respond(direct_alice, True)
    with two.vm.expect_revert("a clause is waiting for an answer"):
        two.propose(direct_bob, "Another clause that is long enough")
    assert two.state() == before
