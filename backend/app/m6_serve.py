"""Loopback-only M6 test service; no legacy launcher/config fallback."""
import os
from werkzeug.serving import make_server
from . import create_app

LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


def resolve_bind(environ=None) -> tuple[str, int]:
    """Resolve and validate loopback bind host and port from environment."""
    env = os.environ if environ is None else environ
    host = (env.get('MI2_HOST') or '127.0.0.1').strip() or '127.0.0.1'
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError('MI2_HOST must be a loopback address')
    raw_port = (env.get('MI2_PORT') or '5000').strip() or '5000'
    if not (raw_port.isascii() and raw_port.isdigit()):
        raise RuntimeError('MI2_PORT must be an integer between 1 and 65535')
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise RuntimeError('MI2_PORT must be between 1 and 65535')
    return host, port


def main():
    host, port = resolve_bind()
    app = create_app()
    if app.config.get('M6_ISOLATED') is not True:
        raise RuntimeError('M6 service requires explicit isolated configuration')
    make_server(host, port, app, threaded=True).serve_forever()


if __name__ == '__main__':
    main()
