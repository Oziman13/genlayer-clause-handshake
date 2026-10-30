"""Mutation check: change the contract on a disposable copy, one small edit at a time, and see whether the tests notice.

Run from the repository root:  python3 scripts/mutation_check.py
It first proves that the unmodified contract passes, then runs the tests against every mutant (stopping at the first
failing test). A mutant that survives is either a missing test or an equivalent change; survivors are listed.
Each entry is (label, text to find, replacement, which occurrence of the text to change: 1 = first).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/clause_handshake.py"

MUTANTS = [
    ("clause min 16", "CLAUSE_MIN, CLAUSE_MAX = 15, 300", "CLAUSE_MIN, CLAUSE_MAX = 16, 300", 1),
    ("clause max 301", "CLAUSE_MIN, CLAUSE_MAX = 15, 300", "CLAUSE_MIN, CLAUSE_MAX = 15, 301", 1),
    ("topic min 6", "TOPIC_MIN, TOPIC_MAX = 5, 80", "TOPIC_MIN, TOPIC_MAX = 6, 80", 1),
    ("topic max 81", "TOPIC_MIN, TOPIC_MAX = 5, 80", "TOPIC_MIN, TOPIC_MAX = 5, 81", 1),
    ("seven locked clauses", "MAX_LOCKED = 6", "MAX_LOCKED = 7", 1),
    ("thirty-one offers", "MAX_ROUNDS = 30", "MAX_ROUNDS = 31", 1),
    ("101 deals", "MAX_DEALS = 100", "MAX_DEALS = 101", 1),
    ("eight day deals", "DEAL_SECONDS = 7 * 86400", "DEAL_SECONDS = 8 * 86400", 1),
    ("page limit 51", "PAGE_LIMIT = 50", "PAGE_LIMIT = 51", 1),
    ("model answer cap", "MAX_MODEL = 4000", "MAX_MODEL = 4001", 1),
    ("quote min 3", "QUOTE_MIN, QUOTE_MAX = 4, 300", "QUOTE_MIN, QUOTE_MAX = 3, 300", 1),
    ("quote max 301", "QUOTE_MIN, QUOTE_MAX = 4, 300", "QUOTE_MIN, QUOTE_MAX = 4, 301", 1),
    ("quote case sensitive", "_squash(quote).lower() in _squash(clause).lower()", "_squash(quote) in _squash(clause).lower()", 1),
    ("quote spacing sensitive", "_squash(quote).lower() in _squash(clause).lower()", "quote.lower() in clause.lower()", 1),
    ("party with self", "if b == a:", "if False:", 1),
    ("every party is a", 'if who == deal["b"]:\n        return "b"', 'if who == deal["b"]:\n        return "a"', 1),
    ("anyone is a party", 'raise gl.vm.UserError("not a party to this deal")', "return \"a\"", 1),
    ("waiting clause ignored", 'if deal["pending"] is not None:\n            raise gl.vm.UserError("a clause is waiting for an answer")\n        if role', 'if False:\n            raise gl.vm.UserError("a clause is waiting for an answer")\n        if role', 1),
    ("turn ignored", 'if role != deal["turn"]:', "if False:", 1),
    ("full deal off by one", 'if len(deal["locked"]) >= MAX_LOCKED:\n            raise gl.vm.UserError("the deal is full")\n        if any', 'if len(deal["locked"]) > MAX_LOCKED:\n            raise gl.vm.UserError("the deal is full")\n        if any', 1),
    ("repeat of agreed clause", 'if any(_squash(c["text"]).lower() == text.lower() for c in deal["locked"]):', "if False:", 1),
    ("repeat is case sensitive", 'if any(_squash(c["text"]).lower() == text.lower() for c in deal["locked"]):', 'if any(_squash(c["text"]) == text for c in deal["locked"]):', 1),
    ("round cap off by one", 'if deal["rounds"] >= MAX_ROUNDS:', 'if deal["rounds"] > MAX_ROUNDS:', 1),
    ("model called for the first clause", '        if deal["locked"]:\n            reading', '        if True:\n            reading', 1),
    ("conflict ignored", 'if reading["conflict"]:', "if False:", 1),
    ("blocked offer is free", 'deal["rounds"] += 1\n                _log(deal, "BLOCKED"', '_log(deal, "BLOCKED"', 1),
    ("proposal is free", 'deal["rounds"] += 1\n        _log(deal, "PROPOSED"', '_log(deal, "PROPOSED"', 1),
    ("unreadable check passes", 'if reading["state"] != "OK":', "if False:", 1),
    ("own clause answered", 'if who == pending["by"]:', "if False:", 1),
    ("rejection locks", "        if accept:\n            if len", "        if True:\n            if len", 1),
    ("lock does not clear signatures", '            deal["signatures"] = {"a": None, "b": None}\n', "            pass\n", 1),
    ("turn not handed over", 'deal["turn"] = role', 'deal["turn"] = deal["turn"]', 1),
    ("answer type unchecked", "if type(accept) is not bool:", "if False:", 1),
    ("lock past six", 'if len(deal["locked"]) >= MAX_LOCKED:\n                raise gl.vm.UserError("the deal is full")', "if False:\n                raise gl.vm.UserError(\"the deal is full\")", 1),
    ("double signature", 'if deal["signatures"][role] == digest:', "if False:", 1),
    ("sign with nothing locked", "if not deal[\"locked\"]:\n            raise gl.vm.UserError(\"nothing is locked yet\")", "if False:\n            raise gl.vm.UserError(\"nothing is locked yet\")", 1),
    ("sign with a clause waiting", 'if deal["pending"] is not None:\n            raise gl.vm.UserError("a clause is waiting for an answer")\n        if not deal', 'if False:\n            raise gl.vm.UserError("a clause is waiting for an answer")\n        if not deal', 1),
    ("one signature agrees", 'if deal["signatures"]["a"] == digest and deal["signatures"]["b"] == digest:', 'if deal["signatures"]["a"] == digest or deal["signatures"]["b"] == digest:', 1),
    ("hash of the first clause only", '_canonical([c["text"] for c in clauses])', '_canonical([c["text"] for c in clauses][:1])', 1),
    ("hash ignores order", '_canonical([c["text"] for c in clauses])', '_canonical(sorted(c["text"] for c in clauses))', 1),
    ("withdraw after closing", 'if deal["phase"] != "NEGOTIATING":\n            raise gl.vm.UserError("the deal is closed")\n        if now >= deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n        _log', 'if False:\n            raise gl.vm.UserError("the deal is closed")\n        if now >= deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n        _log', 1),
    ("withdraw after expiry", 'if now >= deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n        _log', 'if False:\n            raise gl.vm.UserError("the deal has expired")\n        _log', 1),
    ("withdraw keeps the waiting clause", '        deal["phase"] = "WITHDRAWN"\n        deal["pending"] = None', '        deal["phase"] = "WITHDRAWN"', 1),
    ("closed deal accepts writes", 'if deal["phase"] != "NEGOTIATING":\n            raise gl.vm.UserError("the deal is closed")\n        if now >= deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n\n    @gl', 'if False:\n            raise gl.vm.UserError("the deal is closed")\n        if now >= deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n\n    @gl', 1),
    ("expiry off by one", 'if now >= deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n\n    @gl', 'if now > deal["expires"]:\n            raise gl.vm.UserError("the deal has expired")\n\n    @gl', 1),
    ("validator ignores the state", 'if parsed["state"] != mine["state"]:', "if False:", 1),
    ("validator ignores the verdict", 'if parsed["conflict"] != mine["conflict"]:', "if False:", 1),
    ("validator ignores the clause", 'return not parsed["conflict"] or parsed["clause"] == mine["clause"]', 'return True', 1),
    ("validator takes any text", 'or proposed != _canonical(parsed):', "or False:", 1),
    ("validator size cap", "len(proposed) > 2000", "len(proposed) > 2001", 1),
    ("validator skips the anchor", "and isinstance(quote, str) and quote == _squash(quote) and _anchored(quote, locked[number]))", "and isinstance(quote, str) and quote == _squash(quote))", 1),
    ("validator takes loose quotes", "isinstance(quote, str) and quote == _squash(quote) and _anchored", "isinstance(quote, str) and _anchored", 1),
    ("validator takes any clause number", "type(number) is int and 0 <= number < len(locked)", "type(number) is int and 0 <= number", 1),
    ("no-conflict answer may name a clause", 'if raw["clause"] != 0 or raw["quote"] != "":', "if False:", 1),
    ("clause number unbounded above", "or not 1 <= number <= len(locked) or", "or not 1 <= number or", 1),
    ("clause number type unchecked", "if type(number) is not int or not 1", "if not 1", 1),
    ("quote anchor skipped by the parser", 'if not _anchored(quote, locked[number - 1]):', "if False:", 1),
    ("conflict flag type unchecked", ' or type(raw["conflict"]) is not bool:', ":", 1),
    ("agreement shown before signing", 'if deal["phase"] == "AGREED" else "NONE"', 'if True else "NONE"', 1),
    ("deal list only for a", 'if who in (deal["a"], deal["b"]):', 'if who == deal["a"]:', 1),
    ("address case sensitive", "value = value.strip().lower()", "value = value.strip()", 1),
    ("page limit unchecked", "or not 1 <= limit <= PAGE_LIMIT:", "or False:", 1),
    ("deal id type unchecked", "if type(deal_id) is not int or deal_id < 0 or deal_id >= int(self.deal_count):\n            raise", "if deal_id >= int(self.deal_count):\n            raise", 1),
]


def run_tests(directory: Path) -> bool:
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/direct", "-q", "-x", "-p", "no:cacheprovider"],
                            cwd=directory, capture_output=True, text=True)
    return result.returncode == 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mutation-") as tmp:
        work = Path(tmp)
        shutil.copytree(ROOT / "contracts", work / "contracts")
        shutil.copytree(ROOT / "tests", work / "tests")
        source = (ROOT / CONTRACT).read_text()
        if not run_tests(work):
            print("The unmodified contract does not pass its tests; stopping.")
            return 2
        print("base: tests pass on the unmodified contract")
        killed, survived = 0, []
        for label, old, new, nth in MUTANTS:
            if source.count(old) < nth:
                print("ANCHOR MISSING:", label)
                return 3
            parts = source.split(old)
            mutated = old.join(parts[:nth]) + new + old.join(parts[nth:])
            (work / CONTRACT).write_text(mutated)
            if run_tests(work):
                survived.append(label)
                print("SURVIVED:", label)
            else:
                killed += 1
        print("mutants: %d, killed: %d, survived: %d" % (len(MUTANTS), killed, len(survived)))
        for label in survived:
            print(" -", label)
        return 0


if __name__ == "__main__":
    sys.exit(main())
