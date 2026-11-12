"""Explicit M5 LOCAL AUTHORIZED TMDB read entry. Never mounts auth/behavior."""
import argparse
import json
import pathlib
from flask import Flask, jsonify
from .api_read import create_read_blueprint
from .local_catalog_store import LocalCatalogStore
from .tmdb_local import MODE


def records_from_capture(directory):
    directory = pathlib.Path(directory).resolve()
    if directory.drive.upper() == 'C:':
        raise ValueError('Non-C capture directory required')
    evidence = json.loads((directory / 'Capture-Manifest.json').read_text(encoding='utf-8'))
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('Explicit acquisition evidence required')
    records = []
    for entry in evidence:
        mid = entry.get('tmdb_id')
        if type(mid) is not int or mid <= 0:
            raise ValueError('Invalid capture movie ID')
        records.append(((directory / 'raw' / f'tmdb-{mid}.json').read_bytes(), entry))
    return records


def create_local_app(path):
    repository = LocalCatalogStore(path)
    app = Flask('movieinsight_m5_local_readonly')
    app.register_blueprint(create_read_blueprint(repository))
    @app.get('/api/catalog/genres')
    def genres():
        return jsonify(code='OK', data={'items': repository.summary()['genres']})
    @app.get('/api/catalog/demo-info')
    def info():
        summary = repository.summary()
        return jsonify(mode=MODE, data_source='LOCAL AUTHORIZED / TMDB', identity='NONE_READ_ONLY',
                       persistence='M5_ISOLATED_SQLITE', real_database='MYSQL_BLOCKED',
                       dataset_id=summary['dataset_id'], movie_count=len(summary['movies']))
    # A demo header never activates real-catalog behavior or recommendation APIs.
    @app.route('/api/catalog/behavior', methods=['GET', 'POST'])
    @app.get('/api/catalog/recommendations')
    def no_behavior():
        return jsonify(code='REAL_IDENTITY_AND_BEHAVIOR_NOT_CONFIGURED', data=None), 403
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--import-capture')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--port', type=int, default=8877)
    args = parser.parse_args()
    if args.import_capture:
        store = LocalCatalogStore(args.database, initialize=args.initialize)
        print(json.dumps(store.import_batch(records_from_capture(args.import_capture)), ensure_ascii=False))
    elif args.initialize:
        raise ValueError('Initialization requires explicit capture import')
    create_local_app(args.database).run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
