"""Move the anonymous Local Owner's inbox to a real (Google) account.

In plain local dev mode, everything you capture belongs to the built-in
"Local Owner" (local@life-inbox.local). After switching to Google sign-in,
that data is no longer reachable. This script hands it to your account.

1. Back up the database first.
2. Sign in with Google once, so your account exists.
3. Preview:  python -m scripts.claim_local_data --email you@gmail.com
4. Apply:    python -m scripts.claim_local_data --email you@gmail.com --yes

Uses the same DATABASE_URL as the app. Run it while the server is stopped.
"""

from __future__ import annotations

import argparse
import sys

from app.database import (
    DEFAULT_OWNER_EMAIL,
    count_owned_data,
    get_user_by_email,
    transfer_owned_data,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--email", required=True, help="Account that should receive the data."
    )
    parser.add_argument(
        "--from-email",
        default=DEFAULT_OWNER_EMAIL,
        help=f"Account to move data from (default: {DEFAULT_OWNER_EMAIL}).",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Apply the move (default: preview only)."
    )
    args = parser.parse_args(argv)

    source = get_user_by_email(args.from_email)
    if source is None:
        print(f"No account {args.from_email!r} in this database. Nothing to move.")
        return 1
    target = get_user_by_email(args.email)
    if target is None:
        print(
            f"No account {args.email!r} yet. Sign in to Life Inbox with that "
            "Google account once, then run this again."
        )
        return 1
    if source["id"] == target["id"]:
        print("Source and target are the same account. Nothing to move.")
        return 1

    counts = count_owned_data(source["id"])
    print(f"From: {source['email']} (user {source['id']})")
    print(f"To:   {target['email']} (user {target['id']})")
    for name, count in counts.items():
        print(f"  {name}: {count}")
    if not any(counts.values()):
        print("Nothing to move.")
        return 0
    if not args.yes:
        print("Preview only. Back up the database, then rerun with --yes.")
        return 0

    moved = transfer_owned_data(source["id"], target["id"])
    print("Done:", ", ".join(f"{key}={value}" for key, value in moved.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
