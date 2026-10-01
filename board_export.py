"""
board_export.py - writes the message board as handoff.md, to paste at the start of a new chat.

    python board_export.py                    # from SQL (agent.Messages)
    python board_export.py --from-seed        # straight from board_seed.py, no database needed
    python board_export.py --out C:\\NM\\handoff.md

Superseded posts are left out. Sections: context, state, decisions, build agent, dental agent,
proposals, open tasks, open questions.
"""

import argparse
from datetime import date

SECTIONS = [
    ("Context", lambda p: p["author"] == "owner" and p["mtype"] == "Fact"),
    ("Current state", lambda p: p["mtype"] == "Status"),
    ("Decisions made", lambda p: p["mtype"] == "Decision"),
    ("build_agent - how the application works", lambda p: p["author"] == "build_agent" and p["mtype"] == "Fact"),
    ("dental_agent - facts and findings", lambda p: p["author"] == "dental_agent" and p["mtype"] in ("Fact", "Finding")),
    ("Proposals awaiting a decision", lambda p: p["mtype"] == "Proposal"),
    ("Open tasks", lambda p: p["mtype"] == "Task" and p["status"] == "Open"),
    ("Open questions", lambda p: p["mtype"] == "Question" and p["status"] == "Open"),
]


def load_from_db():
    from sqlalchemy import text
    from sql_helper import get_engine
    with get_engine().connect() as conn:
        agents = conn.execute(text("SELECT AgentKey, Persona FROM agent.Agents ORDER BY AgentKey")).fetchall()
        rows = conn.execute(text("""
            SELECT ThreadKey, Author, MsgType, Area, Company, FiscalYear, Title, Body, Amount, Evidence, Status, Priority
            FROM agent.Messages WHERE Status <> 'Superseded' ORDER BY Priority, MessageID""")).fetchall()
    keys = ["thread", "author", "mtype", "area", "company", "fy", "title", "body", "amount", "evidence", "status", "priority"]
    return [(a[0], a[1]) for a in agents], [dict(zip(keys, r)) for r in rows]


def load_from_seed():
    import board_seed
    posts = sorted(board_seed.P, key=lambda p: p["priority"])
    return [(k, persona) for k, persona, _ in board_seed.AGENTS], posts


def render(agents, posts) -> str:
    out = [f"# QBAccountingV1 - handoff ({date.today():%Y-%m-%d})", "",
           "Paste this at the start of a new chat. It is the project's message board: decisions, facts, findings, "
           "proposals, open tasks and questions, posted by the agents below. Full detail lives in SQL "
           "(agent.Messages, fin.Findings) and in the review workbooks.", "",
           "**Agents:** " + "; ".join(f"`{k}` - {p}" for k, p in agents), ""]
    used = set()
    for title, rule in SECTIONS:
        items = [p for p in posts if rule(p) and id(p) not in used]
        if not items:
            continue
        out += [f"## {title}", ""]
        for p in items:
            used.add(id(p))
            tags = " · ".join(str(x) for x in (p["author"], p["mtype"], p["area"], p["company"],
                                                f"FY{p['fy']}" if p["fy"] else None) if x)
            prio = " ⚑" if p["priority"] == 1 and p["status"] == "Open" else ""
            out += [f"### {p['title']}{prio}", f"*{tags}*", "", p["body"]]
            if p["evidence"]:
                out.append(f"\n_Evidence: {p['evidence']}_")
            out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Export the agent message board to handoff.md")
    ap.add_argument("--from-seed", action="store_true", help="Render board_seed.py directly (no database)")
    ap.add_argument("--out", default="handoff.md")
    args = ap.parse_args()
    agents, posts = load_from_seed() if args.from_seed else load_from_db()
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(agents, posts))
    print(f"📝 {args.out}: {len(posts)} posts")


if __name__ == "__main__":
    main()
