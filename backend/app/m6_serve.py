"""Loopback-only M6 test service; no legacy launcher/config fallback."""
from werkzeug.serving import make_server
from . import create_app


def main():
    app = create_app()
    if app.config.get('M6_ISOLATED') is not True:
        raise RuntimeError('M6 service requires explicit isolated configuration')
    make_server('127.0.0.1', 5000, app, threaded=True).serve_forever()


if __name__ == '__main__':
    main()
