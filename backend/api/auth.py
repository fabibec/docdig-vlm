"""Auth hook. Every router depends on ``current_user``.

Today: no login, everybody is the local user. Later (Nextcloud): ``POST /api/auth/login``
checks username + app password against Nextcloud (OCS), stores the credentials server-side
for WebDAV and sets an HttpOnly session cookie; ``current_user`` then reads that session and
raises 401 if missing.
"""

from pydantic import BaseModel


class User(BaseModel):
    name: str


LOCAL_USER = User(name="local")


async def current_user() -> User:
    return LOCAL_USER
