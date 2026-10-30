"""Clause Handshake on a local GenLayer network (glsim, 5 validators): real leader/validator rounds, model mocked.

Every validator of the local network gets the same mocked model answers, so these tests show that the contract runs
through real consensus rounds, not how a real model reads a contradiction. glsim keeps mocks for the whole run, so each
test uses its own simulator and its own tokens.
"""
import hashlib
import json

import pytest

from rpc import RpcError, Sim

ALICE = "0x1111111111111111111111111111111111111111"
BOB = "0x2222222222222222222222222222222222222222"
CARL = "0x3333333333333333333333333333333333333333"
T0 = "2026-11-01T10:00:00Z"
T8 = "2026-11-08T10:00:00Z"
FIRST = "The supplier delivers the finished work within thirty days (T1)."
SECOND = "The delivery is made in sixty days after the order (T2)."
THIRD = "The supplier may deliver in two parts inside that time (T3)."


@pytest.fixture
def sim():
    simulator = Sim()
    try:
        yield simulator
    finally:
        simulator.close()


def mocks(sim, table):
    sim.rpc("sim_installMocks", web_mocks={}, llm_mocks={"BEGIN NEW CLAUSE ---\\n[^\\n]*" + k: v for k, v in table.items()})


def start(sim):
    sim.set_time(T0)
    contract = sim.deploy("contracts/clause_handshake.py", [], ALICE)
    deal = json.loads(sim.call(contract, "open_deal", [BOB, "Delivery terms for the autumn website job"], ALICE))["id"]
    return contract, deal


def say(sim, contract, deal, method, who, *args):
    out = sim.call(contract, method, [deal, *args], who)
    return json.loads(out) if isinstance(out, str) and out[:1] == "{" else out


def test_a_deal_runs_from_opening_to_agreement_through_five_validators(sim):
    contract, deal = start(sim)
    mocks(sim, {"T2": json.dumps({"conflict": True, "clause": 1, "quote": "within thirty days"}),
                "T3": json.dumps({"conflict": False, "clause": 0, "quote": ""})})
    assert say(sim, contract, deal, "propose", ALICE, FIRST) == {"status": "PENDING"}        # nothing locked: no model call
    assert say(sim, contract, deal, "respond", BOB, True) == {"status": "LOCKED"}
    blocked = say(sim, contract, deal, "propose", BOB, SECOND)
    assert blocked == {"status": "BLOCKED", "clause": 0, "quote": "within thirty days"}
    assert say(sim, contract, deal, "propose", BOB, THIRD) == {"status": "PENDING"}
    assert say(sim, contract, deal, "respond", ALICE, True) == {"status": "LOCKED"}
    digest = hashlib.sha256(json.dumps([FIRST, THIRD], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert say(sim, contract, deal, "sign", ALICE) == {"status": "SIGNED", "hash": digest}
    assert say(sim, contract, deal, "sign", BOB) == {"status": "AGREED", "hash": digest}
    state = sim.read_json(contract, "get_deal_json", [deal])
    assert state["phase"] == "AGREED" and [c["text"] for c in state["locked"]] == [FIRST, THIRD]
    assert [h["kind"] for h in state["history"]] == ["PROPOSED", "LOCKED", "BLOCKED", "PROPOSED", "LOCKED", "SIGNED", "SIGNED"]
    agreement = sim.read_json(contract, "get_agreement_json", [deal])
    assert agreement["hash"] == digest and agreement["clauses"] == [FIRST, THIRD]


def test_a_badly_formed_model_answer_changes_nothing(sim):
    contract, deal = start(sim)
    mocks(sim, {"T2": "I am not sure about this one."})
    say(sim, contract, deal, "propose", ALICE, FIRST)
    say(sim, contract, deal, "respond", BOB, True)
    before = sim.read(contract, "get_deal_json", [deal])
    with pytest.raises(RpcError):
        say(sim, contract, deal, "propose", BOB, SECOND)
    assert sim.read(contract, "get_deal_json", [deal]) == before


def test_a_quote_that_is_not_in_the_clause_is_not_a_verdict(sim):
    contract, deal = start(sim)
    mocks(sim, {"T2": json.dumps({"conflict": True, "clause": 1, "quote": "sixty days"})})
    say(sim, contract, deal, "propose", ALICE, FIRST)
    say(sim, contract, deal, "respond", BOB, True)
    with pytest.raises(RpcError):
        say(sim, contract, deal, "propose", BOB, SECOND)
    assert sim.read_json(contract, "get_deal_json", [deal])["rounds"] == 1


def test_turns_and_parties_are_enforced(sim):
    contract, deal = start(sim)
    with pytest.raises(RpcError):
        say(sim, contract, deal, "propose", BOB, FIRST)            # Alice's turn
    with pytest.raises(RpcError):
        say(sim, contract, deal, "propose", CARL, FIRST)           # not a party
    say(sim, contract, deal, "propose", ALICE, FIRST)
    with pytest.raises(RpcError):
        say(sim, contract, deal, "respond", ALICE, True)           # her own clause
    assert sim.read_json(contract, "get_deal_json", [deal])["locked"] == []


def test_a_deal_expires_after_seven_days(sim):
    contract, deal = start(sim)
    say(sim, contract, deal, "propose", ALICE, FIRST)
    sim.set_time(T8)
    with pytest.raises(RpcError):
        say(sim, contract, deal, "respond", BOB, True)
    with pytest.raises(RpcError):
        say(sim, contract, deal, "withdraw", ALICE)
    assert sim.read_json(contract, "get_deal_json", [deal])["locked"] == []
