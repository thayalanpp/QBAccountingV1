"""
Offline tests (no database): the proposed entries balance, land on the reconciliation report's figures,
and the IIF writer exports Approved entries only.    python -m pytest tests   (or: python tests/test_proposed_entries.py)
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proposed_entries as pe
from iif_export import IIFEntry, IIFError, IIFLine, render

QB = {2023: 9502.48, 2024: 12466.03, 2025: 35260.97}       # multi-year GL, debit +
CARD = {2023: 6270.35, 2024: 3430.48, 2025: 408.99}        # TD statements


def test_definitions_are_clean():
    assert pe.check_entries() == []


def test_effects_match_report():
    by = lambda fy: round(sum(e.card_effect() for e in pe.ENTRIES if e.fiscal_year_end == fy and e.kind == "Correction"), 2)
    assert by(2023) == -15772.83
    assert by(2024) == -210.98          # JE-2..JE-4 net Cr 210.98
    assert by(2025) == -21287.11        # JE-5..JE-7 Cr 21,287.11
    je5 = next(e for e in pe.ENTRIES if e.key == "JE-5")
    assert {l.account: l.amount for l in je5.lines} == {"22100": 14141.87, "64400": 3535.48, "22200": -17677.35}


def test_pro_forma_matches_report():
    pf = pe.pro_forma(QB, CARD)
    for fy, exp in pe.EXPECTED.items():
        assert {k: round(v, 2) for k, v in pf[fy].items()} == exp, fy


def _accounts():
    return {"22200": ("Credit Card-NM interco", "Credit Card"), "64400": ("Dental Supplies", "Expense"),
            "22100": ("NM Hygiene interco", "Other Current Liability"), "51000": ("Lab Fees", "Expense")}


def test_iif_general_journal_and_new_account():
    e = IIFEntry("JE-1", date(2023, 8, 31), "General Journal", "JE-1 park", None,
                 [IIFLine("39900", 15772.83), IIFLine("22200", -15772.83)])
    body, notes = render([e], _accounts())
    rows = body.split("\r\n")
    assert rows[0].startswith("!ACCNT") and rows[1].split("\t")[:4] == ["ACCNT", "Suspense - pre-FY2024 differences", "EQUITY", "39900"]
    trns = [r.split("\t") for r in rows if r.startswith(("TRNS", "SPL"))]
    assert trns[0][:7] == ["TRNS", "", "GENERAL JOURNAL", "08/31/2023", "Suspense - pre-FY2024 differences", "", "15772.83"]
    assert trns[1][4] == "Credit Card-NM interco" and trns[1][6] == "-15772.83" and trns[1][7] == "JE-1"
    assert "ENDTRNS" in rows


def test_iif_credit_card_charge_card_leg_first():
    e = IIFEntry("JE-6", date(2025, 2, 3), "Credit Card Charge", "Align", "Align Technology",
                 [IIFLine("51000", 3534.52), IIFLine("22200", -3534.52)])
    body, _ = render([e], _accounts(), date_format="dmy")
    trns = [r.split("\t") for r in body.split("\r\n") if r.startswith(("TRNS", "SPL"))]
    assert trns[0][2] == "CREDIT CARD" and trns[0][3] == "03/02/2025"
    assert trns[0][4] == "Credit Card-NM interco" and trns[0][5] == "Align Technology" and trns[0][6] == "-3534.52"
    assert trns[1][4] == "Lab Fees" and trns[1][6] == "3534.52"


def test_iif_refuses_unapproved_unresolved_unbalanced():
    good = [IIFLine("64400", 2.02), IIFLine("22200", -2.02)]
    for bad in (IIFEntry("JE-4", date(2024, 8, 31), "General Journal", None, None, good, status="Draft"),
                IIFEntry("JE-7", date(2025, 8, 31), "General Journal", None, None,
                         [IIFLine(None, 75.24, hint="to identify"), IIFLine("22200", -75.24)]),
                IIFEntry("JE-X", date(2025, 8, 31), "General Journal", None, None,
                         [IIFLine("64400", 1.00), IIFLine("22200", -0.99)])):
        try:
            render([bad], _accounts())
        except IIFError:
            continue
        raise AssertionError(f"{bad.key} should have been refused")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
