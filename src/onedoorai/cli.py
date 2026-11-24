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
    sub.add_parser("keys", help="make, keep and delete team keys (run by the runner)", add_help=False)
    if argv is None:
        argv = sys.argv[1:]
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
