"""
Offline tests for load_yearend.py hard checks (synthetic figures; the real CSVs stay out of the repo).
    python -m pytest tests     (or: python tests/test_load_yearend.py)
To also check the real files:  set YEAREND_TB / YEAREND_AJE to their paths.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import load_yearend as y


def _files():
    tb = pd.DataFrame([
        # Company, FY, AccountNo, AccountName, FSLine, Prelim, Adj, Reclass, Final, PriorYearFinal, Annotation, SourceDoc
        ["dntl", 2025, "10000", "Bank", "A Cash", 1000.00, 0, 0, 1000.00, 900.00, None, "tb.pdf"],
        ["dntl", 2025, "22200", "Card", "CC Advances from related party", 500.00, -300.00, 0, 200.00, 100.00, None, "tb.pdf"],
        ["dntl", 2025, "22100", "Interco", "CC Advances from related party", -200.00, 300.00, 0, 100.00, 0.00, None, "tb.pdf"],
        ["dntl", 2025, "5000", "A/R", "C Accts. rec.", 0.00, 50.00, 0, 50.00, 0.00, None, "tb.pdf"],
        ["dntl", 2025, "40000", "Fees", "20 Sales", -2000.00, -50.00, 0, -2050.00, -1800.00, None, "tb.pdf"],
        ["dntl", 2025, "64400", "Supplies", "40. 5 Supplies", 700.00, 0, 0, 700.00, 800.00, None, "tb.pdf"],
    ], columns=y.TB_COLS)
    aje = pd.DataFrame([
        ["dntl", 2025, 1, "2025-08-31", "22100", "Interco", 300.00, 0, "reallocate", 0, "aje.pdf"],
        ["dntl", 2025, 1, "2025-08-31", "22200", "Card", 0, 300.00, "reallocate", 0, "aje.pdf"],
        ["dntl", 2025, 2, "2025-08-31", "5000", "A/R", 50.00, 0, "production", 0, "aje.pdf"],
        ["dntl", 2025, 2, "2025-08-31", "40000", "Fees", 0, 50.00, "production", 0, "aje.pdf"],
        ["dntl", 2025, 3, "2025-08-31", "61500", "Due to Shareholders", 20.00, 0, "in and out", 0, "aje.pdf"],
        ["dntl", 2025, 3, "2025-08-31", "61500", "Due to Shareholders", 0, 20.00, "in and out", 0, "aje.pdf"],
    ], columns=y.AJE_COLS)
    return y.normalise(tb, aje)


def test_clean_files_pass():
    tb, aje = _files()
    assert y.hard_checks(tb, aje, "dntl", 2025) == []
    assert "Net income (Final) 1,350.00" in y.summary(tb, aje)[1]


def test_each_hard_check_fires():
    tb, aje = _files()
    a = aje.copy(); a.loc[0, "Debit"] = 301.00                       # 1. AJE out of balance
    assert any("AJE 1: does not balance" in p for p in y.hard_checks(tb, a, "dntl", 2025))
    t = tb.copy(); t.loc[0, "Prelim"] = 1001.00; t.loc[0, "Final"] = 1001.00   # 2. column off
    assert any("Prelim column sums" in p for p in y.hard_checks(t, aje, "dntl", 2025))
    t = tb.copy(); t.loc[0, "Final"] = 999.00; t.loc[5, "Final"] = 701.00      # 3. row arithmetic
    assert sum("differs from Final" in p for p in y.hard_checks(t, aje, "dntl", 2025)) == 2
    t = tb.copy(); t.loc[1, "Adj"] = -250.00; t.loc[2, "Adj"] = 250.00         # 4. Adj vs AJE net
    t.loc[1, "Final"] = 250.00; t.loc[2, "Final"] = 50.00
    assert sum("but AJE net" in p for p in y.hard_checks(t, aje, "dntl", 2025)) == 2
    a = aje.copy(); a.loc[4, "AccountNo"] = "70000"; a.loc[5, "AccountNo"] = "70001"   # AJE account not in TB, not netting
    assert any("not in the TB" in p for p in y.hard_checks(tb, a, "dntl", 2025))
    assert any("not dntl FY2024" in p for p in y.hard_checks(tb, aje, "dntl", 2024))


def test_account_numbers_are_text():
    tb, _ = _files()
    t = tb.copy(); t["AccountNo"] = [10000.0, 22200, "22100", "1010.25", 40000, "64400"]
    t2, _ = y.normalise(t, pd.DataFrame(columns=y.AJE_COLS))
    assert t2["AccountNo"].tolist() == ["10000", "22200", "22100", "1010.25", "40000", "64400"]


def test_real_files_if_given():
    tbp, ajep = os.environ.get("YEAREND_TB"), os.environ.get("YEAREND_AJE")
    if not tbp:
        return
    tb, aje = y.read_files(tbp, ajep)
    co, fy = tb["Company"].iloc[0], int(tb["FiscalYearEnd"].iloc[0])
    assert y.hard_checks(tb, aje, co, fy) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
