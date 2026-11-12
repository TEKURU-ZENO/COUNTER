"""Print an OWNER_PASSWORD_HASH value: python -m infra.scripts.hash_password"""

from __future__ import annotations

import getpass

from apps.api.security import hash_password

if __name__ == "__main__":
    pw = getpass.getpass("Owner password: ")
    if len(pw) < 12:
        raise SystemExit("Use at least 12 characters")
    print(hash_password(pw))
