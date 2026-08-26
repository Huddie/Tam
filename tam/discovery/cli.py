"""upload-discovery -- the CLI entry point for tam.discovery (see
[project.scripts] in the root pyproject.toml). Same publish() mechanics as
tam.discovery.upload(), plus login/list/info/versions for everyday use from a
plain shell (a notebook uses tam.discovery.upload() directly instead).
"""
from __future__ import annotations

import argparse
import getpass
import json
import stat
import sys
from pathlib import Path
from typing import List, Optional

from .auth import resolve_token, token_file_path
from .http import DiscoveryClient
from .upload import upload

_SUBCOMMANDS = {"publish", "login", "list", "info", "versions"}


def _parse_metadata(raw: Optional[str]) -> dict:
    """`--metadata-json` accepts either a literal JSON object, or `@path` to
    read that JSON from a file -- the latter for metadata too large/awkward
    to type inline (e.g. a config dict already written to disk)."""
    if not raw:
        return {}
    text = Path(raw[1:]).read_text() if raw.startswith("@") else raw
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--metadata-json is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise SystemExit("--metadata-json must decode to a JSON object")
    return parsed


def _add_publish_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="Path to a self-contained .html file to publish")
    parser.add_argument("--title", required=True, help="Human-readable title shown in the catalog")
    parser.add_argument("--type", default="dashboard", help="Groups this with other discoveries of the same kind (default: dashboard)")
    parser.add_argument("--name", help="Stable slug -- publishing again with the same --name adds a new version instead of a new discovery")
    parser.add_argument("--description", help="Longer free-text description")
    parser.add_argument("--tag", dest="tags", action="append", default=[], help="Repeatable, e.g. --tag earnings --tag q3")
    parser.add_argument("--source", dest="source_file", help="Recorded verbatim as provenance -- e.g. the notebook/script that generated this artifact")
    parser.add_argument("--metadata-json", help="A JSON object (or @path to a file containing one) stored verbatim as this version's metadata")
    parser.add_argument("--no-git", action="store_true", help="Skip auto-capturing git commit/branch/repo/dirty-tree provenance")
    parser.add_argument("--token", help="Overrides the usual token resolution (env var / Colab secret / saved login)")
    parser.add_argument("--api-url", help=f"Overrides {os_env_hint()}")


def os_env_hint() -> str:
    return "the TAM_DISCOVERY_API_URL environment variable"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upload-discovery", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    publish_parser = subparsers.add_parser("publish", help="Publish a .html file as a new discovery/version")
    _add_publish_arguments(publish_parser)
    publish_parser.set_defaults(handler=cmd_publish)

    login_parser = subparsers.add_parser("login", help="Save a publishing token for future commands")
    login_parser.add_argument("--token", help="Skip the prompt and use this token directly")
    login_parser.add_argument("--api-url", help=f"Overrides {os_env_hint()}")
    login_parser.set_defaults(handler=cmd_login)

    list_parser = subparsers.add_parser("list", help="List discoveries")
    list_parser.add_argument("-q", dest="q", help="Free-text search")
    list_parser.add_argument("--tag", help="Filter by tag")
    list_parser.add_argument("--type", help="Filter by type")
    list_parser.add_argument("--creator", help="Filter by uploader")
    list_parser.add_argument("--sort", choices=["newest", "updated"], default="updated")
    list_parser.add_argument("--token", help="Overrides the usual token resolution")
    list_parser.add_argument("--api-url", help=f"Overrides {os_env_hint()}")
    list_parser.set_defaults(handler=cmd_list)

    info_parser = subparsers.add_parser("info", help="Show one discovery's details")
    info_parser.add_argument("name", help="A discovery's slug or id")
    info_parser.add_argument("--token", help="Overrides the usual token resolution")
    info_parser.add_argument("--api-url", help=f"Overrides {os_env_hint()}")
    info_parser.set_defaults(handler=cmd_info)

    versions_parser = subparsers.add_parser("versions", help="List one discovery's versions")
    versions_parser.add_argument("name", help="A discovery's slug or id")
    versions_parser.add_argument("--token", help="Overrides the usual token resolution")
    versions_parser.add_argument("--api-url", help=f"Overrides {os_env_hint()}")
    versions_parser.set_defaults(handler=cmd_versions)

    return parser


def cmd_publish(args: argparse.Namespace) -> int:
    result = upload(
        args.path,
        title=args.title,
        type=args.type,
        name=args.name,
        description=args.description,
        tags=args.tags,
        metadata=_parse_metadata(args.metadata_json),
        source_file=args.source_file,
        token=args.token,
        api_url=args.api_url,
        capture_git=not args.no_git,
    )
    print(f"Published {result.title!r} (type={result.type}, version {result.version})")
    print(result.url)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    token = args.token or getpass.getpass("Discovery publishing token (from /settings/tokens): ").strip()
    if not token:
        print("No token entered, aborting.", file=sys.stderr)
        return 1

    client = DiscoveryClient(token, api_url=args.api_url)
    who = client.whoami()

    path = token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Non-POSIX filesystems (some Windows setups) may not support chmod
        # -- the token is still saved, just without the permission
        # tightening; not worth failing `login` over.
        pass

    print(f"Logged in as {who.get('user', '?')}. Token saved to {path}.")
    return 0


def _print_table(rows: List[dict], columns: List[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {column: max(len(column), *(len(str(row.get(column, ""))) for row in rows)) for column in columns}
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))


def cmd_list(args: argparse.Namespace) -> int:
    client = DiscoveryClient(resolve_token(args.token), api_url=args.api_url)
    params = {k: v for k, v in {"q": args.q, "tag": args.tag, "type": args.type, "creator": args.creator, "sort": args.sort}.items() if v}
    result = client.list_discoveries(**params)
    _print_table(result.get("discoveries", []), ["name", "type", "title", "created_by", "updated_at"])
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    client = DiscoveryClient(resolve_token(args.token), api_url=args.api_url)
    discovery = client.get_discovery(args.name)
    for key, value in discovery.items():
        print(f"{key}: {value}")
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    client = DiscoveryClient(resolve_token(args.token), api_url=args.api_url)
    result = client.get_versions(args.name)
    _print_table(result.get("versions", []), ["version", "title", "uploaded_by", "created_at"])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in _SUBCOMMANDS and not argv[0].startswith("-"):
        argv = ["publish", *argv]

    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 1

    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001 -- CLI boundary: report and exit cleanly, no traceback noise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
