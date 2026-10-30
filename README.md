# Clause Handshake

**Two parties write an agreement one clause at a time. The contract keeps the order, and the validators refuse a clause that contradicts one already agreed.**

Party A opens a deal and names party B. They take turns: one offers a clause, the other accepts or rejects it. Accepted clauses are
locked. Before a new clause is offered, the validators check that it does not contradict any locked clause; if it does, the clause
is **blocked**, the clash is written to the history together with the words that clash, and the same party can try another wording.
When both sign the same list of locked clauses, the deal is agreed and the contract stores its SHA-256 hash.

* `contracts/clause_handshake.py`: the Intelligent Contract (one Python file, no funds, no web access).
* `tests/direct`: 95 direct-mode tests with the model mocked. `tests/glsim`: 5 tests on a local 5-validator network.
* `scripts/mutation_check.py`: breaks the contract on purpose in 63 ways (57 caught, the 6 that survive cannot change a result; see `evidence/mutation-checks.txt`).
* `evidence/`: the outputs of those runs.

Status: **v0.1.0, tested in memory and on a local network with a mocked model; not deployed to any public network yet.** See [Live run](#live-run) and [What it does not show](#what-it-does-not-show).

## Why a contract for this

Two people agreeing terms in a chat can end up with two clauses that cannot both be followed, and nobody notices until later. Here the
clauses are written in order, each one is accepted by the other side, nobody can edit what is locked, and a new clause is checked
against what was already agreed by validators rather than by either party. The contradiction check is the one subjective step, so it is
the only place a model is used. Everything else (turns, signatures, hashes, limits) is plain code.

## How it works

| Step | Call | Rule |
|---|---|---|
| 1. Open | `open_deal(party_b, topic)` | Topic 5 to 80 characters; B must be a different address. A proposes first. The deal lasts 7 days. |
| 2. Offer | `propose(deal_id, clause)` | Only the party whose turn it is, and only when no clause is waiting. Clause 15 to 300 characters. At most 6 locked clauses and 30 offers (blocked ones count). If clauses are locked, the validators check for a contradiction first. |
| 3. Answer | `respond(deal_id, accept)` | Only the other party. Accept locks the clause and clears both signatures; reject keeps the text in the history. Either way the responder offers next. |
| 4. Sign | `sign(deal_id)` | Each party signs the exact list of locked clauses (SHA-256 of the clause texts, in order). Both signatures on the same list make the deal `AGREED`. |
| 5. Leave | `withdraw(deal_id)` | Either party, while the deal is open. Final. |

After the expiry time every write is refused (`the deal has expired`); the stored phase stays `NEGOTIATING`.

### The contradiction check

When at least one clause is locked, `propose` asks the model: does the new clause contradict an agreed clause? The model must answer
`{"conflict": bool, "clause": n, "quote": "..."}` naming the lowest numbered agreed clause that clashes and copying the clashing words
from it. A reading is accepted only if

1. the answer has exactly that shape,
2. the clause number exists, and
3. the quoted words really appear in that clause (checked by the contract after squashing spaces and ignoring case, with no model).

Validators must agree **exactly** on whether there is a conflict and on which clause; they do not need the same words. If the answer
does not follow the format the proposal is refused (`the conflict check could not be read, try again`) and nothing is stored. An
unreadable answer is never treated as "no conflict" or as "conflict". The first clause of a deal needs no check and no model call.

## Contract

`ClauseHandshake` has no constructor arguments.

| Method | Kind | Purpose |
|---|---|---|
| `open_deal(party_b, topic)` | write | Returns `{"id": n}`. |
| `propose(deal_id, clause)` | write | `{"status": "PENDING"}` or `{"status": "BLOCKED", "clause": i, "quote": "..."}` (clause index starts at 0). |
| `respond(deal_id, accept)` | write | `{"status": "LOCKED"}` or `{"status": "REJECTED"}`. |
| `sign(deal_id)` | write | `{"status": "SIGNED"|"AGREED", "hash": "..."}`. |
| `withdraw(deal_id)` | write | Leave the deal. |
| `get_deal_json(deal_id)` | view | Parties, phase, whose turn, the waiting clause, locked clauses, signatures, round count and the full history; `NONE` if unknown. |
| `get_agreement_json(deal_id)` | view | `{"hash", "at", "clauses"}` of an agreed deal; `NONE` otherwise. |
| `get_deals_for_json(address)`, `get_deal_count()`, `get_deals_page(offset, limit)` | view | Find and list deals. |

## What it does not show

These are limits of the idea, not bugs.

* **The check is a model's reading.** "Contradicts" is a judgement. A clause can be blocked that two people would have accepted, or pass
  although a careful lawyer would object. The instructions tell the model to answer "no conflict" when in doubt; that lowers false blocks
  and raises missed ones. Rewording a blocked clause is expected.
* **Exact agreement is demanded.** Validators must agree exactly on the verdict and the clause. If they cannot, the
  transaction does not settle and can be sent again; nothing is stored in between.
* **It checks new clauses against locked ones only.** It does not check a clause against the waiting one, and locked clauses are never
  re-examined.
* **The model sees only the clause texts.** It has no idea what the parties meant, and the clause text is untrusted: the prompt tells it
  not to obey instructions inside it, and the quote rule means a clash must be shown in the agreed clause's own words.
* **This is a record, not a contract in law.** The agreement is two signatures over a hash on a test network. Addresses are not identities.
* **Time comes from the transaction.** The 7 days run from the transaction timestamp that opened the deal.
* No funds are held.

## Tests

Run from the repository root (Python 3.12+, `pip install genlayer-test`).

```
python3 -m pytest tests/direct     # 95 tests, in memory, the model is mocked
python3 -m pytest tests/glsim      # 5 tests on a local 5-validator network (pip install glsim fastapi uvicorn)
python3 scripts/mutation_check.py  # 63 deliberate breakages
```

The tests mock the model, so they show that the contract handles each kind of answer correctly (conflict, no conflict, badly formed,
quote not in the clause, forged validator readings), **not** how well a real model spots contradictions. The mock answers on the local
network are identical for every validator.

## Live run

None yet. The contract has not been deployed to Studionet or any other public network, so there is no address and there are no
transaction hashes, and **it has never been run with a real model**. What exists: 95 in-memory tests and 5 tests on a local
5-validator simulator (the model is mocked in both), and the mutation check (outputs in `evidence/`). Whether a real model spots a
contradiction, and whether real validators settle on the same verdict and clause, is untested.

## Try it

Open [GenLayer Studio](https://studio.genlayer.com), load `contracts/clause_handshake.py`, deploy it with no constructor arguments,
call `open_deal` from one account naming a second account, then alternate `propose` and `respond`. The conflict check runs from the
second clause on and can take a few minutes.

## Where each claim is in the code

| Claim | Where |
|---|---|
| Turns, one waiting clause, 6 locked clauses, 30 offers | `ClauseHandshake.propose`, `respond` |
| A blocked clause is recorded and the turn does not change | `ClauseHandshake.propose` |
| The reading must name an existing clause and quote its words | `_parse`, `_anchored` |
| Validators agree exactly on verdict and clause, and re-check the quote | `_consensus` (`validator`), `_valid_reading` |
| An unreadable answer is never a verdict | `_read`, `propose` |
| Signatures cover the exact clause list; locking clears them | `_digest`, `respond`, `sign` |
| Deal expiry | `_require_open` |
| Time is read from the transaction timestamp and parsed by hand | `_now` |

## Licence

MIT, see `LICENSE`.
