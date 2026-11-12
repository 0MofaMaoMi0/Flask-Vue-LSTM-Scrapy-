"""Replaceable boundaries. Demo identities never authenticate a real account."""
from typing import Protocol


class MovieProvider(Protocol):
    def __call__(self) -> list[dict]: ...


class BehaviorRepository(Protocol):
    def user(self, user: str) -> None: ...
    def snapshot(self, user: str) -> dict: ...
    def apply(self, user: str, event: dict, visible: set[int]) -> dict: ...


class IdentityProvider(Protocol):
    def resolve(self, request) -> str: ...


class AuthenticationRequired(Exception):
    pass


class DemoIdentityProvider:
    def resolve(self, request):
        from .recommendation import USERS
        user = request.headers.get('X-Demo-User')
        if user not in USERS:
            raise ValueError('Explicit demo identity required')
        return user


class AuthenticatedIdentityProvider:
    """Future server-side session adapter; no callback means deny all access.

    The callback must validate a server-side session, never trust a request ID.
    No authentication implementation or credential is supplied by this module.
    """
    def __init__(self, verified_session=None):
        self.verified_session = verified_session

    def resolve(self, request):
        user = self.verified_session() if self.verified_session else None
        if not isinstance(user, str) or not user or user.startswith('demo-'):
            raise AuthenticationRequired()
        return user


def create_formal_blueprint(movie_provider=None, repository=None, identity=None):
    """Explicit providers required; never substitute synthetic data or users."""
    from .recommendation_api import create_recommendation_blueprint
    if movie_provider is None or repository is None:
        raise ValueError('Real providers are not configured; synthetic fallback prohibited')
    return create_recommendation_blueprint(
        movie_provider, repository, identity or AuthenticatedIdentityProvider())
