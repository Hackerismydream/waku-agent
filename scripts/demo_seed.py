"""Reset .jarvis to a clean, curated state for a demo / recording.

    python scripts/demo_seed.py

What it does (your old state is backed up first, never just deleted):
  1. moves the current .jarvis aside to .jarvis.bak-<timestamp>
  2. creates a fresh state.db + calendar.ics
  3. seeds a small, clean memory (a few facts + one episode) and ONE calendar
     event — Sergey's standing Saturday 5 PM swim
  4. leaves traces/, outbox/ and the chat log EMPTY, so when you type live on
     camera the loop, traces and Gateway inbox fill up in front of the viewer

Everything it writes is the same data the app writes — open state.db afterwards
and it looks exactly like real use, just tidy.
"""

from __future__ import annotations

import shutil
from datetime import datetime

from jarvis.config import load_settings
from jarvis.db import connect
from jarvis.memory.episodic.store import SqliteEpisodeStore
from jarvis.memory.semantic.store import SqliteFactStore
from jarvis.tools.calendar import make_tool

# Curated seed — clean, no duplicates. Edit these to taste before recording.
FACTS = [
    ("sergey", "Sergey is the user's swim buddy; they have a standing swim every Saturday at 5 PM."),
    ("raj", "Raj is a friend the user plays tennis with."),
    ("user", "The user runs the YouTube channel 'Sean's AI Stories' and films implementation walkthroughs."),
]
EPISODE = ("2026-07-11", "Confirmed the standing Saturday 5 PM swim with Sergey.")
EVENT = {"title": "Swim with Sergey", "start": "2026-07-11T17:00",
         "end": "2026-07-11T18:00", "attendees": "Sergey"}


def main() -> None:
    settings = load_settings()
    home = settings.home

    if home.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = home.with_name(f"{home.name}.bak-{stamp}")
        shutil.copytree(home, backup)
        print(f"backed up {home} -> {backup}")
        # calendar.ics + these dirs are plain files no process holds open.
        # NOTE: we deliberately KEEP traces/ and usage.jsonl — the trace history
        # and the money/token spend ledger are a permanent record, not demo data,
        # so a reset never erases what you've actually spent.
        (home / "calendar.ics").unlink(missing_ok=True)
        for sub in ("outbox", "skills"):
            d = home / sub
            if d.exists():
                shutil.rmtree(d)

    settings.ensure_home()
    conn = connect(home)

    # Clear the DB rows IN PLACE — never delete state.db. Deleting the file
    # would leave any live gateway (a running `make telegram`, the dashboard,
    # an open CLI) holding a broken, read-only connection to the old inode.
    for table in ("chat_log", "calendar_events", "facts", "episodes"):
        conn.execute(f"DELETE FROM {table}")   # triggers keep the FTS index in sync
    conn.commit()

    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    for subject, content in FACTS:
        facts.add(subject, content, source="user")
    episodes.add(EPISODE[1], happened_at=EPISODE[0])

    create_event = make_tool(conn, home).fn
    print(create_event(**EVENT))

    # regenerate the human-readable MEMORY.md mirror for the fresh state
    from jarvis.memory import Memory

    Memory(conn, settings, None).export_markdown()

    print(f"\nclean demo state ready in {home}")
    print(f"  facts: {len(FACTS)}  ·  episodes: 1  ·  events: 1  ·  chat log: empty  ·  traces: empty")
    print("  SOUL.md and eval reports kept. Run `make dashboard` and start filming.")


if __name__ == "__main__":
    main()
