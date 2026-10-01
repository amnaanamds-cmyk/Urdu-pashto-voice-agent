"""Command line.

  python -m awaaz onboard scripts/seed/dr_khan_dental.json
  python -m awaaz list
  python -m awaaz chat "Dr. Khan"            # talk to the agent by text (real Claude + real DB)
  python -m awaaz token "Dr. Khan"           # issue a new dashboard token
  python -m awaaz jobs daily|summary
  python -m awaaz serve                      # HTTP server (Vapi + dashboard)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import jobs, onboarding, timeutil
from .config import Settings
from .db import make_engine
from .repo import Repo


def _repo(settings: Settings) -> Repo:
    return Repo(make_engine(settings.database_url))


def _find_business(repo: Repo, key: str) -> dict:
    matches = [b for b in repo.list_businesses()
               if str(b["id"]) == key or key.lower() in b["name"].lower()]
    if len(matches) != 1:
        names = ", ".join(b["name"] for b in matches) or "none"
        sys.exit(f"business '{key}' matched {len(matches)} ({names}); use the id from `python -m awaaz list`")
    return matches[0]


def cmd_onboard(args, settings):
    repo = _repo(settings)
    biz, token = onboarding.onboard(repo, onboarding.load(args.config))
    print(f"Onboarded {biz['name']}  id={biz['id']}")
    print(f"Dashboard: {settings.public_base_url}/login   owner token: {token}")
    print("  (shown once — store it safely; `python -m awaaz token` issues a new one)")
    if biz.get("ai_phone"):
        print(f"\nVapi: point phone number {biz['ai_phone']} -> Server URL {settings.public_base_url}/vapi/webhook")
        print("      with header x-awaaz-secret = AWAAZ_WEBHOOK_SECRET, and no fixed assistant.")


def cmd_list(args, settings):
    for b in _repo(settings).list_businesses():
        print(f"{b['id']}  {b['type']:<6}  {b['name']}  ai={b.get('ai_phone') or '-'}")


def cmd_token(args, settings):
    repo = _repo(settings)
    biz = _find_business(repo, args.business)
    print(f"New owner token for {biz['name']}: {onboarding.rotate_token(repo, biz['id'])}")


def cmd_jobs(args, settings):
    repo = _repo(settings)
    if args.job == "daily":
        print(f"slots created: {jobs.ensure_all_slots(repo)}")
        print(f"recordings purged: {jobs.purge_recordings(repo, settings)}")
    else:
        from .integrations.whatsapp import WhatsApp
        print(f"summaries sent: {jobs.send_summaries(repo, WhatsApp(settings))}")


def cmd_chat(args, settings):
    from .agent.engine import Engine
    from .agent.session import CallSession, NullControl
    from .agent.tools import ToolBox
    from .integrations.whatsapp import WhatsApp

    import os
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("Set ANTHROPIC_API_KEY (in .env or the environment) to talk to the agent.")
    repo = _repo(settings)
    biz = _find_business(repo, args.business)
    wa = WhatsApp(settings)
    engine = Engine(settings, repo, ToolBox(repo, wa))
    control = NullControl()
    session = CallSession(business=biz, caller_phone=timeutil.normalize_phone(args.caller),
                          channel=args.channel, control=control)
    print(f"Calling {biz['name']} as {session.caller_phone or 'hidden number'} "
          f"(model {settings.model}). Empty line or Ctrl-D to hang up.\n")

    async def loop():
        while not session.ended:
            try:
                line = input("caller> ").strip()
            except EOFError:
                break
            if not line:
                break
            print("agent> ", end="", flush=True)
            async for piece in engine.respond(session, line):
                print(piece, end="", flush=True)
            print()
            if control.transferred_to:
                print(f"   [transferred to {control.transferred_to}]")
                break
        if control.ended_with:
            print(f"agent> {control.ended_with}   [hung up]")
        for m in wa.outbox:
            print(f"   [whatsapp -> {m['to']}: {m.get('text', m.get('template'))}]")
        print(f"\nOutcome: {session.outcome()}")

    asyncio.run(loop())


def cmd_serve(args, settings):
    import uvicorn
    uvicorn.run("awaaz.api.app:create_app", factory=True, host=args.host, port=args.port)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="awaaz")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("onboard"); p.add_argument("config"); p.set_defaults(fn=cmd_onboard)
    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("token"); p.add_argument("business"); p.set_defaults(fn=cmd_token)
    p = sub.add_parser("jobs"); p.add_argument("job", choices=["daily", "summary"]); p.set_defaults(fn=cmd_jobs)
    p = sub.add_parser("chat"); p.add_argument("business")
    p.add_argument("--caller", default="+923331234567", help="caller ID to simulate")
    p.add_argument("--channel", choices=["text", "voice"], default="text",
                   help="'voice' makes the agent write Urdu/Pashto script as it would for TTS")
    p.set_defaults(fn=cmd_chat)
    p = sub.add_parser("serve"); p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000); p.set_defaults(fn=cmd_serve)
    args = ap.parse_args(argv)
    args.fn(args, Settings.from_env())


if __name__ == "__main__":
    main()
