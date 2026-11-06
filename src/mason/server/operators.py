"""`mason operator add|remove|list`: who may open the operator panel. Runs on the host, against the
database, so only whoever runs Mason (and can read its data) makes someone an operator.

WHO is a login the user signed in with (`github:mike`, `gitlab:ann`, or a bare login) or a user id
(`#3`, as `mason serve --users` shows). The user must have signed in once, so their account exists.
"""

from __future__ import annotations

from sqlalchemy import func

from mason.server.models import Audit, Identity, Operator, User, db


def find(who: str) -> User:
    who = who.strip()
    if who.lstrip("#").isdigit():
        user = db.get(User, int(who.lstrip("#")))
        if user is None:
            raise LookupError(f"no user #{who.lstrip('#')}")
        return user
    provider, _, login = who.rpartition(":")
    q = db.query(Identity).filter(func.lower(Identity.login) == login.lower())
    if provider:
        q = q.filter(Identity.provider == provider.lower())
    users = {i.user_id for i in q}
    if not users:
        raise LookupError(f"no user signed in as {who} yet: they sign in to Mason once, then you add them")
    if len(users) > 1:
        raise LookupError(f"{who} matches more than one user; say github:{login} or gitlab:{login}, or #id")
    return db.get(User, users.pop())


def _log(action: str, user: User) -> None:
    db.add(Audit(user_id=None, via="host", action=action, target=f"user {user.id} ({user.name})"))


def add(who: str, note: str = "") -> str:
    user = find(who)
    if db.get(Operator, user.id) is not None:
        return f"{user.name} (#{user.id}) is already an operator"
    db.add(Operator(user_id=user.id, note=note[:200]))
    _log("operator.add", user)
    db.commit()
    return f"{user.name} (#{user.id}) is an operator now: the operator page shows at their next page load"


def remove(who: str) -> str:
    user = find(who)
    row = db.get(Operator, user.id)
    if row is None:
        return f"{user.name} (#{user.id}) isn't an operator"
    db.delete(row)
    _log("operator.remove", user)
    db.commit()
    return f"{user.name} (#{user.id}) is no longer an operator (from their next request)"


def listing() -> list[str]:
    rows = db.query(Operator, User).join(User, User.id == Operator.user_id).order_by(User.id).all()
    out = []
    for o, u in rows:
        logins = ", ".join(f"{i.provider}:{i.login}" for i in u.identities) or "-"
        out.append(f"#{u.id}  {u.name}  {logins}  since {o.added_at:%Y-%m-%d}{'  ' + o.note if o.note else ''}")
    return out or ["no operators yet: mason operator add github:LOGIN"]
