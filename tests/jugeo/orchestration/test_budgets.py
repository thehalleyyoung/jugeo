from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.orchestration.budgets import BudgetLedger


def test_budget_ledger_consumes_and_releases() -> None:
    ledger = BudgetLedger({'frontier': 2})
    assert ledger.consume('frontier', 1) is True
    ledger.release('frontier', 1)
    assert ledger.remaining('frontier') == 2
