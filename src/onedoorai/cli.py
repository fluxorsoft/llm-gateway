"""The onedoorai command."""
import argparse
import sys

from . import __version__


def main(argv=None):
    p = argparse.ArgumentParser(prog="onedoorai", description="One door to AI: tools for the LLM gateway.")
    p.add_argument("--version", action="version", version=f"onedoorai {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("admin", help="open a local door to the gateway's admin service (adds your Google identity)")
    a.add_argument("--url", help="the admin service's address (default: ADMIN_URL)")
    a.add_argument("--port", type=int, help="local port (default 9000)")
    sub.add_parser("key", help="print your own key for the developers' door, using your Google sign-in", add_help=False)
    sub.add_parser("rotate", help="propose the next key-rotation step as edits to the team files", add_help=False)
    sub.add_parser("pricecheck", help="the gateway's cost figures against Google's bill (run by the runner)", add_help=False)
    sub.add_parser("budgets", help="one line per team: how much of its budget is spent (run by the runner)")
    sub.add_parser("keys", help="make, keep and delete team keys (run by the runner)", add_help=False)
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["key"]:
        from .key import main as key
        return key(argv[1:])
    if argv[:1] == ["rotate"]:
        from .rotate import main as rotate
        return rotate(argv[1:])
    if argv[:1] == ["pricecheck"]:
        from .pricecheck import main as pricecheck
        return pricecheck(argv[1:])
    if argv[:1] == ["budgets"]:
        from .budgets import main as budgets
        return budgets(argv[1:])
    if argv[:1] == ["keys"]:
        from .keys import main as keys
        return keys(argv[1:])
    args = p.parse_args(argv)
    if args.command == "admin":
        from .admin import main as admin
        admin(args.url, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
