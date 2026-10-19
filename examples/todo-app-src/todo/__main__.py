"""python3 -m todo serve|add|list|done|rm."""
import argparse
import json

from .api import serve
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Todo JSON API and CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--data", default="data")
    add = commands.add_parser("add")
    add.add_argument("title")
    add.add_argument("--data", default="data")
    listing = commands.add_parser("list")
    listing.add_argument("--data", default="data")
    done = commands.add_parser("done")
    done.add_argument("id", type=int)
    done.add_argument("--data", default="data")
    remove = commands.add_parser("rm")
    remove.add_argument("id", type=int)
    remove.add_argument("--data", default="data")
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.port, args.data)
        return
    store = Store(args.data)
    try:
        if args.command == "add":
            result = store.add(args.title)
        elif args.command == "list":
            result = store.list_all()
        elif args.command == "done":
            result = store.transition(args.id, "done")
        else:
            result = store.transition(args.id, "archived")
    except (KeyError, ValueError) as error:
        parser.exit(1, f"todo: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
