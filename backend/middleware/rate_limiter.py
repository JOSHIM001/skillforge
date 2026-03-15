from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_user_id_or_ip(request: Request) -> str:
    """
    Rate limit key: use authenticated user ID if available,
    fall back to IP address for unauthenticated requests.
    This prevents a single user from bypassing limits by rotating IPs.
    """
    user = getattr(request.state, "user", None)
    if user and hasattr(user, "id"):
        return f"user:{user.id}"
    return get_remote_address(request)


# Shared limiter instance — import and apply as a decorator in routers
limiter = Limiter(key_func=_get_user_id_or_ip, default_limits=["60/minute"])
