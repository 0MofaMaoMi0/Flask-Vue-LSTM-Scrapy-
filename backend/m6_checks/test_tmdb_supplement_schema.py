"""Offline static validation of the TMDB supplement incremental schema.

What this module does NOT do: connect to MySQL, import the Flask app, read
configuration/.env, start a server, or execute any DDL. It only reads
``schema.sql``, ``schema_manifest.json`` and the frozen ``models.py``/pin.

Why it is not a self-copy check: the SQL text is lexed into a structural model
(columns/types/keys/foreign keys/checks) and compared with the manifest, which
is the reviewed design spec; both sides are then re-built as SQLAlchemy tables
and compiled with the installed MySQL dialect, foreign-key targets are resolved
against the frozen 16-table metadata, and invariants that neither artefact can
"self-prove" are asserted (RESTRICT-only foreign keys, no NULL-dependent unique
key, index coverage, no secrets, dedup semantics, frozen hash unchanged).
"""
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, UniqueConstraint, Index, ForeignKeyConstraint, PrimaryKeyConstraint, CheckConstraint

BACKEND = Path(__file__).resolve().parents[1]
SCHEMA_DIR = BACKEND / 'catalog' / 'tmdb_supplement'
SQL_PATH = SCHEMA_DIR / 'schema.sql'
MANIFEST_PATH = SCHEMA_DIR / 'schema_manifest.json'
MODELS_PATH = BACKEND / 'catalog' / 'models.py'
PIN_PATH = BACKEND / 'catalog' / 'm6_frozen_authority.json'

NEW_TABLES = (
    'tmdb_supplement_jobs',
    'tmdb_companies',
    'tmdb_keywords',
    'tmdb_movie_metadata',
    'tmdb_movie_countries',
    'tmdb_movie_companies',
    'tmdb_movie_languages',
    'tmdb_movie_keywords',
    'tmdb_release_records',
    'tmdb_metric_observations',
    'tmdb_board_snapshots',
    'tmdb_board_entries',
)

FORBIDDEN = (
    r'\bCREATE\s+(?:DATABASE|SCHEMA|USER|INDEX|VIEW|TRIGGER|PROCEDURE|FUNCTION|EVENT)\b',
    r'\bALTER\b',
    r'\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)\b',
    r'\bTRUNCATE\b',
    r'\bGRANT\b',
    r'\bREVOKE\b',
    r'\bRENAME\s+TABLE\b',
    r'\bSET\s+(?:GLOBAL|FOREIGN_KEY_CHECKS)\b',
    r'\bDELETE\s+FROM\b',
    r'\bUPDATE\s+\S+\s+SET\b',
    r'\bINSERT\s+INTO\b',
    r'\bREPLACE\s+INTO\b',
    r'\bLOAD\s+DATA\b',
    r'\bLOCK\s+TABLES\b',
    r'\bCALL\b',
)


# --------------------------------------------------------------------------
# lexer / parser: schema.sql -> structural model
# --------------------------------------------------------------------------
def strip_comments(sql):
    """Remove -- and /* */ comments while keeping string literals intact."""
    out, i, n, quote, in_comment = [], 0, len(sql), None, False
    while i < n:
        ch = sql[i]
        if in_comment:
            if ch == '\n':
                in_comment = False
                out.append('\n')
            i += 1
            continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            elif ch == '\\' and i + 1 < n:
                out.append(sql[i + 1])
                i += 2
                continue
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if sql[i:i + 2] == '--':
            in_comment = True
            i += 2
            continue
        if sql[i:i + 2] == '/*':
            j = sql.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    if in_comment or quote:
        raise AssertionError('unterminated comment or string literal in schema.sql')
    return ''.join(out)


def split_statements(text):
    statements, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
            buf.append(ch)
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                raise AssertionError('unbalanced parentheses in schema.sql')
        if ch == ';' and depth == 0:
            chunk = ''.join(buf).strip()
            if chunk:
                statements.append(chunk)
            buf = []
            continue
        buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        statements.append(tail)
    if depth != 0:
        raise AssertionError('unbalanced parentheses at end of schema.sql')
    return statements


def split_top_level(body):
    items, buf, depth, quote = [], [], 0, None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
            buf.append(ch)
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                raise AssertionError('unbalanced column definition parentheses')
        if ch == ',' and depth == 0:
            items.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        items.append(tail)
    return items


def match_paren(text, start):
    depth, quote, i = 0, None, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError('unbalanced parentheses in schema.sql')


def norm_space(value):
    return ' '.join(str(value).split())


def norm_type(value):
    return norm_space(value).upper()


def parse_type(raw):
    """Return dict(base, args, unsigned, charset, collation)."""
    chunk = norm_space(raw)
    m = re.match(r'(?i)^([A-Z]+)\s*(?:\(([^)]*)\))?(.*)$', chunk)
    if not m:
        raise AssertionError('unreadable column type: %r' % raw)
    base = m.group(1).upper()
    args = tuple(int(x.strip()) for x in m.group(2).split(',')) if m.group(2) else ()
    rest = m.group(3)
    unsigned = bool(re.search(r'(?i)\bUNSIGNED\b', rest))
    charset = re.search(r'(?i)\bCHARACTER\s+SET\s+(\w+)', rest)
    collation = re.search(r'(?i)\bCOLLATE\s+(\w+)', rest)
    return {
        'base': base,
        'args': args,
        'unsigned': unsigned,
        'charset': charset.group(1).lower() if charset else None,
        'collation': collation.group(1).lower() if collation else None,
    }


def render_type(spec):
    text = spec['base'] + ('(%s)' % ','.join(str(a) for a in spec['args']) if spec['args'] else '')
    if spec['unsigned']:
        text += ' UNSIGNED'
    if spec['charset']:
        text += ' CHARACTER SET %s' % spec['charset']
    if spec['collation']:
        text += ' COLLATE %s' % spec['collation']
    return text


COLUMN_RE = re.compile(
    r'(?is)^(`[^`]+`|[A-Za-z_][\w$]*)\s+'
    r'([A-Za-z]+(?:\s*\([^)]*\))?(?:\s+UNSIGNED)?(?:\s+CHARACTER\s+SET\s+\w+)?(?:\s+COLLATE\s+\w+)?)'
    r'(.*)$'
)


def parse_column(item):
    m = COLUMN_RE.match(item)
    if not m:
        raise AssertionError('unreadable column definition: %r' % item)
    name = m.group(1).strip('`')
    type_spec = parse_type(m.group(2))
    rest = m.group(3)
    default = re.search(r'(?i)\bDEFAULT\s+(\'[^\']*\'|\S+)', rest)
    return {
        'name': name,
        'type': norm_type(render_type(type_spec)),
        'type_spec': type_spec,
        'nullable': not re.search(r'(?i)\bNOT\s+NULL\b', rest),
        'auto_increment': bool(re.search(r'(?i)\bAUTO_INCREMENT\b', rest)),
        'default': default.group(1).strip("'") if default else None,
    }


def parse_columns(text):
    return [c.strip().strip('`') for c in text.split(',') if c.strip()]


def parse_table(statement):
    head = re.match(r'(?is)^CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(`[^`]+`|[A-Za-z_][\w$]*)\s*\(', statement)
    if not head:
        raise AssertionError('statement is not a plain CREATE TABLE: %r' % statement[:80])
    name = head.group(2).strip('`')
    open_at = statement.index('(', head.end() - 1)
    close_at = match_paren(statement, open_at)
    body = statement[open_at + 1:close_at]
    options = statement[close_at + 1:]
    table = {
        'name': name,
        'if_not_exists': bool(head.group(1)),
        'columns': [],
        'primary_key': [],
        'unique_keys': [],
        'indexes': [],
        'foreign_keys': [],
        'checks': [],
        'raw_statement': statement,
    }
    for item in split_top_level(body):
        upper = item.upper()
        if upper.startswith('PRIMARY KEY'):
            table['primary_key'] = parse_columns(item[item.index('(') + 1:item.rindex(')')])
        elif upper.startswith('UNIQUE KEY'):
            m = re.match(r'(?is)^UNIQUE\s+KEY\s+(\w+)\s*\((.*)\)$', item)
            table['unique_keys'].append({'name': m.group(1), 'columns': parse_columns(m.group(2))})
        elif upper.startswith('KEY ') or upper.startswith('INDEX '):
            m = re.match(r'(?is)^(?:KEY|INDEX)\s+(\w+)\s*\((.*)\)$', item)
            table['indexes'].append({'name': m.group(1), 'columns': parse_columns(m.group(2))})
        elif upper.startswith('CONSTRAINT') and 'FOREIGN KEY' in upper:
            m = re.match(r'(?is)^CONSTRAINT\s+(\w+)\s+FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)(.*)$', item)
            if not m:
                raise AssertionError('unreadable foreign key: %r' % item)
            tail = m.group(5) or ''
            on_delete = re.search(r'(?i)ON\s+DELETE\s+(\w+)', tail)
            on_update = re.search(r'(?i)ON\s+UPDATE\s+(\w+)', tail)
            table['foreign_keys'].append({
                'name': m.group(1),
                'columns': parse_columns(m.group(2)),
                'references': {'table': m.group(3), 'columns': parse_columns(m.group(4))},
                'on_delete': on_delete.group(1).upper() if on_delete else None,
                'on_update': on_update.group(1).upper() if on_update else None,
            })
        elif upper.startswith('CONSTRAINT') and 'CHECK' in upper:
            m = re.match(r'(?is)^CONSTRAINT\s+(\w+)\s+CHECK\s*\((.*)\)$', item)
            if not m:
                raise AssertionError('unreadable check constraint: %r' % item)
            table['checks'].append({'name': m.group(1), 'expression': norm_space(m.group(2))})
        else:
            table['columns'].append(parse_column(item))
    engine = re.search(r'(?i)\bENGINE\s*=\s*(\w+)', options)
    charset = re.search(r'(?i)\b(?:DEFAULT\s+)?CHARSET\s*=\s*(\w+)', options)
    collate = re.search(r'(?i)\bCOLLATE\s*=\s*(\w+)', options)
    table['engine'] = engine.group(1) if engine else None
    table['charset'] = charset.group(1) if charset else None
    table['collation'] = collate.group(1) if collate else None
    names = [c['name'] for c in table['columns']]
    if len(names) != len(set(names)):
        raise AssertionError('duplicate column in %s' % name)
    return table


def load_sql():
    text = SQL_PATH.read_text(encoding='utf-8')
    statements = split_statements(strip_comments(text))
    return text, statements, [parse_table(s) for s in statements]


def canonical_spec(table):
    """Structure used for comparison and for the preflight fingerprint.

    Documentation-only keys (``purpose``/``note``/``role``) are projected away so
    the manifest, the SQL parser and the fingerprint describe the same thing.
    """
    return {
        'columns': [
            {
                'name': c['name'],
                'type': normalized_type(c['type']),
                'nullable': c['nullable'],
                'auto_increment': c['auto_increment'],
                'default': c['default'],
            }
            for c in table['columns']
        ],
        'primary_key': list(table['primary_key']),
        'unique_keys': sorted(
            [{'name': u['name'], 'columns': list(u['columns'])} for u in table['unique_keys']],
            key=lambda u: u['name'],
        ),
        'indexes': sorted(
            [{'name': i['name'], 'columns': list(i['columns'])} for i in table['indexes']],
            key=lambda i: i['name'],
        ),
        'foreign_keys': sorted(
            [
                {
                    'name': f['name'],
                    'columns': list(f['columns']),
                    'references': {'table': f['references']['table'], 'columns': list(f['references']['columns'])},
                    'on_delete': f['on_delete'],
                    'on_update': f['on_update'],
                }
                for f in table['foreign_keys']
            ],
            key=lambda f: f['name'],
        ),
    }


def fingerprint(table):
    payload = json.dumps(canonical_spec(table), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


def normalized_type(value):
    return norm_type(value)


def model_metadata():
    """Execute the frozen models.py in an isolated namespace (no app import)."""
    namespace = {'__name__': 'offline_schema_validation_models'}
    exec(compile(MODELS_PATH.read_text(encoding='utf-8'), 'models.py', 'exec'), namespace)
    return namespace['CatalogBase'].metadata


def build_table_spec(column, spec):
    return {'name': column['name'], 'type': spec, 'nullable': column['nullable'],
            'auto_increment': column['auto_increment'], 'default': column['default']}


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
class SchemaFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql_text, cls.statements, cls.tables = load_sql()
        cls.manifest = load_manifest()
        cls.by_name = {t['name']: t for t in cls.tables}

    # -- file shape ------------------------------------------------------
    def test_statement_set_is_create_table_only(self):
        self.assertEqual(len(self.statements), 12)
        for table in self.tables:
            self.assertFalse(table['if_not_exists'], 'IF NOT EXISTS silently accepts a prior partial run: %s' % table['name'])
        self.assertEqual([t['name'] for t in self.tables], list(NEW_TABLES))
        self.assertEqual(len(set(NEW_TABLES)), 12)

    def test_no_forbidden_or_destructive_statement(self):
        stripped = strip_comments(self.sql_text)
        for pattern in FORBIDDEN:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, stripped, re.IGNORECASE))
        # no external CREATE INDEX: every index must live inside a CREATE TABLE
        self.assertNotIn('CREATE INDEX', stripped.upper())

    def test_table_options_are_explicit(self):
        for table in self.tables:
            with self.subTest(table=table['name']):
                self.assertEqual(table['engine'], 'InnoDB')
                self.assertEqual(table['charset'], 'utf8mb4')
                self.assertEqual(table['collation'], 'utf8mb4_bin')
                self.assertIn("COMMENT='", table['raw_statement'])

    # -- manifest <-> sql ------------------------------------------------
    def test_manifest_tables_match_sql_structure(self):
        manifest_names = [t['name'] for t in self.manifest['tables']]
        self.assertEqual(manifest_names, list(NEW_TABLES))
        for spec in self.manifest['tables']:
            parsed = self.by_name[spec['name']]
            with self.subTest(table=spec['name']):
                self.assertEqual(
                    [c['name'] for c in spec['columns']],
                    [c['name'] for c in parsed['columns']],
                )
                for expected, actual in zip(spec['columns'], parsed['columns']):
                    self.assertEqual(expected['name'], actual['name'])
                    self.assertEqual(normalized_type(expected['type']), actual['type'],
                                     'type mismatch for %s.%s' % (spec['name'], expected['name']))
                    self.assertEqual(expected['nullable'], actual['nullable'],
                                     'nullability mismatch for %s.%s' % (spec['name'], expected['name']))
                    self.assertEqual(expected['auto_increment'], actual['auto_increment'])
                    self.assertEqual(expected['default'], actual['default'])
                self.assertEqual(spec['primary_key'], parsed['primary_key'])
                expected_spec = canonical_spec(dict(spec, name=spec['name']))
                actual_spec = canonical_spec(parsed)
                self.assertEqual(expected_spec['unique_keys'], actual_spec['unique_keys'])
                self.assertEqual(expected_spec['indexes'], actual_spec['indexes'])
                self.assertEqual(expected_spec['foreign_keys'], actual_spec['foreign_keys'])
                self.assertEqual(
                    sorted([c['name'] for c in spec['checks']]),
                    sorted([c['name'] for c in parsed['checks']]),
                )
                for expected in spec['checks']:
                    actual = next(c for c in parsed['checks'] if c['name'] == expected['name'])
                    self.assertEqual(norm_space(expected['expression']), actual['expression'],
                                     'check text mismatch for %s' % expected['name'])

    def test_manifest_metadata_matches_sql_file(self):
        self.assertEqual(self.manifest['status'], 'PROPOSED_NOT_EXECUTED')
        self.assertFalse(self.manifest['target']['executed'])
        self.assertFalse(self.manifest['target']['creates_database'])
        self.assertFalse(self.manifest['target']['alters_or_drops_existing_tables'])
        self.assertFalse(self.manifest['target']['grants_privileges'])
        self.assertFalse(self.manifest['target']['disables_foreign_key_checks'])
        self.assertEqual(self.manifest['execution']['statement_count'], 12)
        self.assertEqual(self.manifest['execution']['statement_order'], list(NEW_TABLES))
        self.assertEqual(self.manifest['tables'][0]['name'], NEW_TABLES[0])

    def test_logical_responsibilities_cover_every_table(self):
        responsibilities = self.manifest['logical_responsibilities']
        self.assertEqual(len(responsibilities), 13)
        self.assertEqual([r['id'] for r in responsibilities], ['R%02d' % i for i in range(1, 14)])
        covered = set()
        for item in responsibilities:
            self.assertTrue(item['tables'], item['id'])
            self.assertTrue(item['merge_or_split_note'].strip(), item['id'])
            for name in item['tables']:
                self.assertIn(name, NEW_TABLES)
                covered.add(name)
        self.assertEqual(covered, set(NEW_TABLES))
        merged = {r['id'] for r in responsibilities if '合并' in r['merge_or_split_note']}
        self.assertIn('R06', merged, 'the language dictionary merge must be documented')
        language_tables = {r['id']: r['tables'] for r in responsibilities if r['id'] in ('R06', 'R07')}
        self.assertEqual(language_tables, {'R06': ['tmdb_movie_languages'], 'R07': ['tmdb_movie_languages']})

    # -- semantics -------------------------------------------------------
    def test_foreign_keys_are_restrict_only_and_typed(self):
        new = set(NEW_TABLES)
        frozen = set(model_metadata().tables)
        for table in self.tables:
            for fk in table['foreign_keys']:
                with self.subTest(fk=fk['name']):
                    self.assertEqual(fk['on_delete'], 'RESTRICT')
                    self.assertEqual(fk['on_update'], 'RESTRICT')
                    target = fk['references']['table']
                    self.assertIn(target, frozen | new)
                    if target in new:
                        self.assertLess(NEW_TABLES.index(target), NEW_TABLES.index(table['name']))
                    parent = self.by_name[target] if target in new else None
                    if parent is not None:
                        parent_types = {c['name']: c['type'] for c in parent['columns']}
                        for child_col, parent_col in zip(fk['columns'], fk['references']['columns']):
                            child_type = next(c['type'] for c in table['columns'] if c['name'] == child_col)
                            self.assertEqual(child_type, parent_types[parent_col])

    def test_unique_keys_never_rely_on_nullable_columns(self):
        for table in self.tables:
            nullable = {c['name'] for c in table['columns'] if c['nullable']}
            keys = [('PRIMARY KEY', table['primary_key'])] + [
                (u['name'], u['columns']) for u in table['unique_keys']
            ]
            for name, columns in keys:
                self.assertTrue(columns, '%s.%s has no columns' % (table['name'], name))
                self.assertFalse(set(columns) & nullable,
                                 '%s.%s includes nullable column' % (table['name'], name))

    def test_primary_keys_and_auto_increment(self):
        for table in self.tables:
            with self.subTest(table=table['name']):
                self.assertTrue(table['primary_key'])
                pk_types = {c['name']: c['type'] for c in table['columns'] if c['name'] in table['primary_key']}
                self.assertTrue(all(t.startswith('BIGINT') or t.startswith('INT') or t.startswith('CHAR') or t.startswith('VARCHAR') for t in pk_types.values()))
                for column in table['columns']:
                    if column['name'] in table['primary_key']:
                        self.assertFalse(column['nullable'])
                    if column['auto_increment']:
                        self.assertIn(column['name'], table['primary_key'])
                        self.assertTrue(column['type'].startswith('BIGINT'))

    def test_statistics_index_coverage(self):
        required = {
            'tmdb_movie_countries': ['country_iso2', 'movie_id'],
            'tmdb_movie_companies': ['company_id', 'movie_id'],
            'tmdb_movie_languages': ['language_code', 'movie_id'],
            'tmdb_movie_keywords': ['keyword_id', 'movie_id'],
        }
        for table, columns in required.items():
            indexes = self.by_name[table]['indexes'] + self.by_name[table]['unique_keys']
            self.assertTrue(
                any(idx['columns'][:len(columns)] == columns for idx in indexes),
                '%s missing leading index %s' % (table, columns),
            )
        metric = self.by_name['tmdb_metric_observations']
        self.assertIn({'name': 'uq_tmdb_metric_observations_movie_time', 'columns': ['movie_id', 'observed_at']},
                      metric['unique_keys'])
        board = self.by_name['tmdb_board_snapshots']
        self.assertIn({'name': 'uq_tmdb_board_snapshots_channel_time', 'columns': ['board_channel', 'captured_at']},
                      board['unique_keys'])

    def test_foreign_key_child_indexes_exist(self):
        for table in self.tables:
            available = [table['primary_key']] + [u['columns'] for u in table['unique_keys']] + [i['columns'] for i in table['indexes']]
            for fk in table['foreign_keys']:
                self.assertTrue(
                    any(cols[:len(fk['columns'])] == fk['columns'] for cols in available),
                    '%s: no leading index for foreign key %s' % (table['name'], fk['name']),
                )

    def test_metric_observation_semantics(self):
        table = self.by_name['tmdb_metric_observations']
        columns = {c['name']: c for c in table['columns']}
        for name in ('vote_average', 'vote_count', 'popularity'):
            self.assertTrue(columns[name]['nullable'], '%s must allow unknown NULL' % name)
        self.assertFalse(columns['observed_at']['nullable'])
        self.assertFalse(columns['source_record_pk']['nullable'])
        unique_columns = [u['columns'] for u in table['unique_keys']]
        self.assertEqual(unique_columns, [['movie_id', 'observed_at']])
        for cols in unique_columns:
            self.assertNotIn('payload_sha256', cols)
            self.assertNotIn('vote_average', cols)
            self.assertNotIn('popularity', cols)
        checks = ' '.join(c['expression'] for c in table['checks'])
        self.assertIn('vote_average >= 0', checks)
        self.assertIn('vote_count >= 0', checks)
        self.assertIn('popularity >= 0', checks)

    def test_release_dedup_does_not_use_nullable_unique(self):
        table = self.by_name['tmdb_release_records']
        columns = {c['name']: c for c in table['columns']}
        self.assertFalse(columns['record_fingerprint']['nullable'])
        self.assertEqual([u['columns'] for u in table['unique_keys']], [['record_fingerprint']])
        for name in ('release_type', 'release_date', 'release_date_raw', 'certification', 'note', 'certification_language'):
            self.assertTrue(columns[name]['nullable'], '%s should stay nullable' % name)
        checks = ' '.join(c['expression'] for c in table['checks'])
        self.assertIn('UPPER(country_iso2)', checks)
        self.assertIn('release_type BETWEEN 1 AND 6', checks)

    def test_board_entries_allow_off_library_movies(self):
        table = self.by_name['tmdb_board_entries']
        columns = {c['name']: c for c in table['columns']}
        self.assertTrue(columns['movie_id']['nullable'])
        self.assertFalse(columns['tmdb_id']['nullable'])
        movie_fk = [fk for fk in table['foreign_keys'] if fk['references']['table'] == 'movies']
        self.assertEqual(len(movie_fk), 1)
        self.assertEqual(movie_fk[0]['columns'], ['movie_id'])
        self.assertEqual(movie_fk[0]['on_delete'], 'RESTRICT')

    def test_board_channel_vocabulary_matches_public_boards(self):
        table = self.by_name['tmdb_board_snapshots']
        check = next(c for c in table['checks'] if c['name'] == 'ck_tmdb_board_snapshots_channel')
        channels = re.findall(r"'([a-z_]+)'", check['expression'])
        self.assertEqual(channels, ['popular', 'trending_day', 'trending_week'])
        note = next(t for t in self.manifest['tables'] if t['name'] == 'tmdb_board_snapshots')['channel_policy']
        self.assertIn('PUBLIC', note)

    def test_current_state_protection_fields(self):
        table = self.by_name['tmdb_movie_metadata']
        columns = {c['name']: c for c in table['columns']}
        self.assertFalse(columns['fetched_at']['nullable'])
        for json_column in ('field_status_json', 'last_attempt_json', 'provenance_json'):
            self.assertEqual(columns[json_column]['type'], 'JSON')
            self.assertFalse(columns[json_column]['nullable'])
        for topic in ('base', 'countries', 'companies', 'spoken_languages', 'keywords', 'releases', 'credits', 'metrics'):
            self.assertIn(topic + '_at', columns, 'missing per-topic timestamp %s_at' % topic)
            self.assertTrue(columns[topic + '_at']['nullable'])
            self.assertTrue(columns[topic + '_at']['type'].startswith('DATETIME(6)'))
        self.assertIn({'name': 'uq_tmdb_movie_metadata_tmdb', 'columns': ['tmdb_id']}, table['unique_keys'])

    def test_jobs_table_has_no_secret_columns(self):
        secret = re.compile(r'(?i)(token|secret|password|passwd|credential|cookie|api[_-]?key|private|dsn|connection|conn_str)')
        for table in self.tables:
            for column in table['columns']:
                self.assertIsNone(secret.search(column['name']),
                                  'secret-like column %s.%s' % (table['name'], column['name']))
        jobs = self.by_name['tmdb_supplement_jobs']
        columns = {c['name'] for c in jobs['columns']}
        for expected in ('request_budget', 'request_count', 'checkpoint_json', 'status'):
            self.assertIn(expected, columns)
        self.assertNotIn('total_tokens', columns)

    def test_names_are_bounded_and_conventional(self):
        conventions = (('uq_', 'unique_keys'), ('ix_', 'indexes'), ('fk_', 'foreign_keys'), ('ck_', 'checks'))
        for table in self.tables:
            for column in table['columns']:
                self.assertLessEqual(len(column['name']), 64)
            for prefix, key in conventions:
                for item in table[key]:
                    with self.subTest(name=item['name']):
                        self.assertTrue(item['name'].startswith(prefix), item['name'])
                        self.assertIn(table['name'], item['name'])
                        self.assertLessEqual(len(item['name']), 64)

    # -- preflight / rollback -------------------------------------------
    def test_preflight_fingerprints_match_manifest_structure(self):
        tables = self.manifest['preflight']['fingerprints']['tables']
        self.assertEqual([t['table'] for t in tables], list(NEW_TABLES))
        for entry in tables:
            spec = next(t for t in self.manifest['tables'] if t['name'] == entry['table'])
            self.assertEqual(entry['fingerprint'], fingerprint(spec),
                             'stale fingerprint for %s' % entry['table'])
            self.assertEqual(entry['fingerprint'], fingerprint(self.by_name[entry['table']]),
                             'sql/manifest fingerprint mismatch for %s' % entry['table'])

    def test_preflight_covers_new_tables_and_has_no_if_not_exists_shortcut(self):
        preflight = self.manifest['preflight']
        query = next(q for q in preflight['queries'] if q['id'] == 'new_tables_absent')['sql']
        for name in NEW_TABLES:
            self.assertIn(name, query)
        self.assertIn('SUPPLEMENT_SCHEMA_PARTIAL', preflight['rules'][0])
        self.assertIn('SUPPLEMENT_SCHEMA_DRIFT', preflight['rules'][1])
        joined = preflight['why'] + ' ' + ' '.join(preflight['rules'])
        self.assertIn('IF NOT EXISTS', joined)
        self.assertIn('禁止', joined)
        self.assertIn('逐表比对', joined)

    def test_rollback_is_manual_and_keeps_data(self):
        rollback = self.manifest['rollback']
        self.assertFalse(rollback['automatic'])
        self.assertFalse(rollback['scripts_generated'])
        self.assertEqual(rollback['order'], list(reversed(NEW_TABLES)))
        self.assertIn('保留', rollback['precondition'])
        self.assertTrue(any('DROP DATABASE' in item for item in rollback['forbidden']))

    def test_unverified_items_are_explicit(self):
        self.assertTrue(self.manifest['unverified'])
        self.assertTrue(any('SHOW CREATE' in item for item in self.manifest['unverified']))
        preconditions = {item['id']: item for item in self.manifest['preconditions']}
        self.assertIn('PC-01', preconditions)
        self.assertIn('BIGINT', preconditions['PC-01']['expectation'])
        self.assertFalse(any(item['verified_on_instance'] for item in self.manifest['preconditions']))

    def test_reused_tables_are_frozen_tables(self):
        frozen = set(model_metadata().tables)
        reused = set(self.manifest['reused_existing_tables'])
        self.assertTrue(reused <= frozen, reused - frozen)
        for name in ('movies', 'movie_external_ids', 'movie_credits',
                     'movie_source_records', 'ingest_batches', 'movie_ratings_summary', 'people'):
            self.assertIn(name, reused)
        for item in self.manifest['reused_existing_tables'].values():
            self.assertEqual(item['mutations'], 'none')

    def test_reused_table_columns_exist_in_frozen_models(self):
        metadata = model_metadata()
        for table, item in self.manifest['reused_existing_tables'].items():
            with self.subTest(table=table):
                self.assertIn(table, metadata.tables)
                missing = set(item['reference_columns']) - set(metadata.tables[table].columns.keys())
                self.assertFalse(missing, '%s references unknown columns %s' % (table, sorted(missing)))

    def test_lookup_preconditions_exist_in_frozen_models(self):
        metadata = model_metadata()

        def unique_keys(table):
            return [tuple(c.columns.keys()) for c in metadata.tables[table].constraints
                    if isinstance(c, UniqueConstraint)]

        # tmdb_id -> movie_id resolution path claimed by board entries
        self.assertIn(('source', 'external_id'), unique_keys('movie_external_ids'))
        # composite foreign key target claimed by metadata/release/observation tables
        self.assertIn(('id', 'movie_id'), unique_keys('movie_source_records'))
        self.assertEqual(str(metadata.tables['movie_source_records'].c.id.type), 'BIGINT')
        self.assertEqual(str(metadata.tables['movie_source_records'].c.movie_id.type), 'BIGINT')
        self.assertEqual(str(metadata.tables['movies'].c.movie_id.type), 'BIGINT')


class ContractAlignmentTests(unittest.TestCase):
    """Alignment with the shipped normalizer (read-only import of normalize.py)."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()
        _, _, cls.tables = load_sql()
        cls.by_name = {t['name']: t for t in cls.tables}
        cls.widths = {t['name']: {} for t in cls.tables}
        for table in cls.tables:
            for column in table['columns']:
                match = re.match(r'VARCHAR\((\d+)\)', column['type'])
                if match:
                    cls.widths[table['name']][column['name']] = int(match.group(1))

    @staticmethod
    def normalizer():
        spec = importlib.util.spec_from_file_location(
            'tmdb_supplement_normalize_offline', SCHEMA_DIR / 'normalize.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @classmethod
    def normalize(cls, payload, *, movie_id=1, job_id='job-1'):
        normalizer = cls.normalizer()
        return normalizer, normalizer.normalize_movie(
            payload, movie_id=movie_id, tmdb_id=payload['id'],
            fetched_at='2026-01-01T00:00:00Z', job_id=job_id)

    # -- topic vocabulary -------------------------------------------------
    def test_topic_vocabulary_matches_normalizer_output(self):
        _, document = self.normalize({'id': 1, 'title': 'T'})
        topics = list(document['field_status'].keys())
        self.assertEqual(topics, ['base', 'countries', 'companies', 'spoken_languages',
                                  'keywords', 'releases', 'credits', 'metrics'])
        normalizer = self.normalizer()
        self.assertEqual(list(normalizer.STATUSES),
                         ['NOT_FETCHED', 'MISSING', 'EMPTY', 'PRESENT', 'FAILED'])
        model = self.manifest['topic_state_model']
        self.assertEqual(model['topics'], topics)
        self.assertEqual(model['status_vocabulary'], list(normalizer.STATUSES))
        self.assertEqual(set(model['success_time_columns']), set(topics))
        metadata = {c['name']: c for c in self.by_name['tmdb_movie_metadata']['columns']}
        for topic, column in model['success_time_columns'].items():
            self.assertIn(column, metadata, 'topic %s has no success-time column' % topic)
            self.assertTrue(metadata[column]['type'].startswith('DATETIME(6)'))
        # base must have its own time column, not only the global fetched_at
        self.assertIn('base_at', metadata)
        self.assertNotEqual(model['success_time_columns']['base'], 'fetched_at')

    def test_attempt_state_is_separate_from_retained_facts(self):
        model = self.manifest['topic_state_model']
        self.assertEqual(model['fields']['attempt_status'], 'field_status_json')
        self.assertEqual(model['fields']['attempt_provenance'], 'last_attempt_json')
        self.assertEqual(model['fields']['retained_fact_provenance'], 'provenance_json')
        self.assertEqual(model['fields']['global_high_water'], 'fetched_at')
        self.assertEqual(model['fields']['attempt_conflict_details'],
                         'last_attempt_json[topic].conflict_details（仅 TOPIC_VERSION_CONFLICT）')
        self.assertEqual(set(model['provenance_entry_schema']),
                         {'source_record_pk', 'payload_sha256', 'fetched_at', 'state'})
        self.assertEqual(set(model['last_attempt_entry_schema']),
                         {'status', 'attempt_at', 'source_record_pk', 'payload_sha256',
                          'error_code', 'conflict_details'})
        rules = model['rules']
        required = ('per_topic_comparison', 'example_not_short_circuit', 'replace_rule', 'empty_rule',
                    'equal_time_same_payload', 'equal_time_different_payload', 'older_response',
                    'attempt_monotonic', 'failure_rule', 'partial_facts_policy',
                    'global_fetched_at_rule')
        for key in required:
            with self.subTest(rule=key):
                self.assertIn(key, rules)
                self.assertTrue(rules[key].strip())
        self.assertNotIn('partial_rule', rules)
        self.assertIn('不得用全局 fetched_at', rules['per_topic_comparison'])
        self.assertIn('t15', rules['example_not_short_circuit'])
        self.assertIn('TOPIC_VERSION_CONFLICT', rules['equal_time_different_payload'])
        self.assertIn('conflict_details', rules['equal_time_different_payload'])
        self.assertIn('不静默覆盖', rules['equal_time_different_payload'])
        self.assertIn("field_status_json[topic]='FAILED'", rules['equal_time_different_payload'])
        self.assertNotIn("field_status_json[topic]='CONFLICT'", rules['equal_time_different_payload'])
        self.assertIn('不得清空', rules['failure_rule'])
        self.assertIn('不得改写', rules['failure_rule'])
        self.assertIn('永不倒退', rules['global_fetched_at_rule'])
        self.assertIn('attempt_at', rules['attempt_monotonic'])
        self.assertIn('旧的尝试', rules['attempt_monotonic'])
        self.assertIn('不得回退', rules['attempt_monotonic'])
        # provenance columns exist in the DDL and the projection columns stay optional
        metadata = {c['name']: c for c in self.by_name['tmdb_movie_metadata']['columns']}
        self.assertIn('last_attempt_json', metadata)
        self.assertIn('provenance_json', metadata)

    def test_conflict_is_reported_in_attempt_layer_not_as_sixth_state(self):
        model = self.manifest['topic_state_model']
        statuses = model['status_vocabulary']
        self.assertEqual(statuses, ['NOT_FETCHED', 'MISSING', 'EMPTY', 'PRESENT', 'FAILED'])
        self.assertNotIn('CONFLICT', statuses, 'CONFLICT must not become a sixth status')
        signal = model['conflict_signal']
        self.assertEqual(signal['attempt_status'], 'FAILED')
        self.assertIn(signal['attempt_status'], statuses)
        self.assertEqual(signal['error_code'], 'TOPIC_VERSION_CONFLICT')
        self.assertEqual(signal['details_field'], 'last_attempt_json[topic].conflict_details')
        self.assertTrue(signal['retained_provenance_unchanged'])
        self.assertEqual(set(signal['details_shape']),
                         {'retained_payload_sha256', 'incoming_payload_sha256',
                          'retained_source_record_pk', 'incoming_source_record_pk', 'attempt_at'})
        self.assertEqual(model['provenance_entry_schema']['state'], 'PRESENT | EMPTY')
        self.assertNotIn('CONFLICT', model['provenance_entry_schema']['state'])
        self.assertEqual(model['last_attempt_entry_schema']['status'],
                         'NOT_FETCHED | MISSING | EMPTY | PRESENT | FAILED')
        self.assertNotIn('CONFLICT', model['last_attempt_entry_schema']['status'])
        # In manifest and DDL, the uppercase token may only appear as the error code.
        for name in ('schema_manifest.json', 'schema.sql'):
            text = (SCHEMA_DIR / name).read_text(encoding='utf-8')
            stray = [text[max(0, i - 24):i + 12]
                     for i in (match.start() for match in re.finditer('CONFLICT', text))
                     if text[max(0, i - 14):i] != 'TOPIC_VERSION_']
            self.assertEqual(stray, [], '%s reintroduced a CONFLICT state: %s' % (name, stray))
            self.assertIn('TOPIC_VERSION_CONFLICT', text)
        sql_text = (SCHEMA_DIR / 'schema.sql').read_text(encoding='utf-8')
        self.assertNotIn("field_status_json[topic]='CONFLICT'", sql_text)
        self.assertNotIn('provenance_json[topic].conflict', sql_text)

    def test_failed_facts_are_never_partially_accepted(self):
        model = self.manifest['topic_state_model']
        self.assertIs(model['no_partial_acceptance'], True)
        self.assertEqual(model['provenance_entry_schema']['state'], 'PRESENT | EMPTY')
        self.assertNotIn('PARTIAL', model['status_vocabulary'])
        self.assertNotIn('PARTIAL', model['last_attempt_entry_schema']['status'])
        self.assertNotIn('PARTIAL', model['provenance_entry_schema']['state'])
        rule = model['rules']['partial_facts_policy']
        self.assertIn('fail-closed', rule)
        self.assertIn('不更新关系', rule)
        self.assertIn('不推进成功时间', rule)
        self.assertIn('不存在', rule)
        self.assertNotIn('PARTIAL', json.dumps(model, ensure_ascii=False),
                         'no PARTIAL state may survive anywhere in the topic model')
        self.assertNotIn('PARTIAL', (SCHEMA_DIR / 'schema.sql').read_text(encoding='utf-8'))

    # -- inbound widths ---------------------------------------------------
    def test_new_column_widths_cover_normalizer_limits(self):
        normalizer = self.normalizer()
        self.assertIsNotNone(normalizer.JOB_RE.fullmatch('a' * 128))
        self.assertIsNone(normalizer.JOB_RE.fullmatch('a' * 129))
        self.assertGreaterEqual(self.widths['tmdb_supplement_jobs']['job_ref'], 128)

        def country_name(length):
            _, document = self.normalize({'id': 1, 'title': 'T', 'production_countries': [
                {'iso_3166_1': 'US', 'name': 'x' * length}]})
            return document['facts']['countries'][0]['name']

        self.assertEqual(len(country_name(255)), 255)
        self.assertIsNone(country_name(256), 'normalizer must reject 256-char country name')
        self.assertGreaterEqual(self.widths['tmdb_movie_countries']['country_name'], 255)

        def keyword_count(length):
            _, document = self.normalize({'id': 1, 'title': 'T', 'keywords': [
                {'id': 7, 'name': 'k' * length}]})
            return len(document['facts']['keywords'])

        self.assertEqual(keyword_count(500), 1)
        self.assertEqual(keyword_count(501), 0)
        self.assertGreaterEqual(self.widths['tmdb_keywords']['name'], 500)

        def language_names(length):
            _, document = self.normalize({'id': 1, 'title': 'T', 'spoken_languages': [
                {'iso_639_1': 'en', 'english_name': 'e' * length, 'name': 'n' * length}]})
            return document['facts']['spoken_languages'][0]

        kept = language_names(500)
        self.assertEqual((len(kept['english_name']), len(kept['name'])), (500, 500))
        dropped = language_names(501)
        self.assertIsNone(dropped['english_name'])
        self.assertIsNone(dropped['name'])
        self.assertGreaterEqual(self.widths['tmdb_movie_languages']['english_name'], 500)
        self.assertGreaterEqual(self.widths['tmdb_movie_languages']['name'], 500)

    def test_manifest_records_width_alignment(self):
        entries = self.manifest['inbound_width_limits']['entries']
        by_column = {(e['table'], e['column']): e for e in entries}
        for key in (('tmdb_supplement_jobs', 'job_ref'),
                    ('tmdb_movie_countries', 'country_name'),
                    ('tmdb_keywords', 'name'),
                    ('tmdb_movie_languages', 'english_name'),
                    ('tmdb_movie_languages', 'name')):
            with self.subTest(column=key):
                self.assertIn(key, by_column)
                entry = by_column[key]
                self.assertIsInstance(entry['normalize_limit'], int)
                self.assertGreaterEqual(self.widths[key[0]][key[1]], entry['normalize_limit'])
                self.assertRegex(entry['on_overflow'], r'issue|INVALID_')
        policy = self.manifest['inbound_width_limits']['policy']
        self.assertIn('不得静默截断', policy)
        self.assertIn('禁止 ALTER 原表', policy)
        popularity = by_column[('tmdb_metric_observations', 'popularity')]
        column = next(c for c in self.by_name['tmdb_metric_observations']['columns']
                      if c['name'] == 'popularity')
        args = column['type_spec']['args']
        self.assertEqual(args, (20, 4))
        self.assertGreaterEqual(10 ** (args[0] - args[1]), 10 ** 15)
        self.assertIn('10^15', popularity['normalize_note'])

    # -- release dedupe ---------------------------------------------------
    def test_release_fingerprint_separates_identical_releases_across_movies(self):
        payload = {'id': 11, 'title': 'T', 'release_dates': {'results': [
            {'iso_3166_1': 'US', 'release_dates': [
                {'type': 3, 'release_date': '2020-01-01', 'certification': 'PG',
                 'note': '', 'iso_639_1': 'en'}]}]}}
        _, first = self.normalize(payload, movie_id=1)
        _, second = self.normalize(payload, movie_id=2)
        key_one = first['facts']['releases'][0]['release_dates'][0]['dedupe_key']
        key_two = second['facts']['releases'][0]['release_dates'][0]['dedupe_key']
        self.assertEqual(key_one, key_two, 'normalize.dedupe_key must not include movie_id')

        spec = self.manifest['release_dedupe']
        self.assertFalse(spec['includes_movie_id'])
        self.assertEqual(spec['separator'], '\x1f')
        self.assertEqual(spec['encoding'], 'UTF-8')
        self.assertIn('movie_id', spec['record_fingerprint_formula'])

        def fingerprint(movie_id, dedupe_key):
            return hashlib.sha256(
                ('%d%s%s' % (movie_id, spec['separator'], dedupe_key)).encode(spec['encoding'])
            ).hexdigest()

        self.assertNotEqual(fingerprint(1, key_one), fingerprint(2, key_two))
        self.assertEqual(fingerprint(1, key_one),
                         hashlib.sha256(('1' + chr(31) + key_one).encode('utf-8')).hexdigest())
        table = self.by_name['tmdb_release_records']
        self.assertEqual([u['columns'] for u in table['unique_keys']], [['record_fingerprint']])
        column = next(c for c in table['columns'] if c['name'] == 'record_fingerprint')
        self.assertFalse(column['nullable'])
        requirements = ' '.join(spec['writer_requirements'])
        self.assertIn('禁止只写 dedupe_key', requirements)

class SQLAlchemyDialectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, _, cls.tables = load_sql()
        cls.manifest = load_manifest()
        cls.metadata = model_metadata()
        cls.frozen_tables = set(cls.metadata.tables)
        cls.new_tables = {}
        cls._build()

    @classmethod
    def _build(cls):
        for parsed in cls.tables:
            columns = []
            for column in parsed['columns']:
                spec = column['type_spec']
                columns.append(sa.Column(column['name'], build_type(spec),
                                         nullable=column['nullable'],
                                         autoincrement=column['auto_increment'] if column['auto_increment'] else 'auto'))
            constraints = [PrimaryKeyConstraint(*parsed['primary_key'])]
            for unique in parsed['unique_keys']:
                constraints.append(UniqueConstraint(*unique['columns'], name=unique['name']))
            for index in parsed['indexes']:
                constraints.append(Index(index['name'], *[cls._column(columns, c) for c in index['columns']]))
            for fk in parsed['foreign_keys']:
                constraints.append(ForeignKeyConstraint(
                    fk['columns'],
                    ['%s.%s' % (fk['references']['table'], c) for c in fk['references']['columns']],
                    name=fk['name'], ondelete=fk['on_delete'], onupdate=fk['on_update'],
                ))
            for check in parsed['checks']:
                constraints.append(CheckConstraint(check['expression'], name=check['name']))
            table = sa.Table(
                parsed['name'], cls.metadata, *columns, *constraints,
                mysql_engine='InnoDB',
                mysql_default_charset='utf8mb4',
                mysql_collate='utf8mb4_bin',
            )
            cls.new_tables[table.name] = table

    @staticmethod
    def _column(columns, name):
        return next(c for c in columns if c.name == name)

    def test_all_tables_compile_with_mysql_dialect(self):
        dialect = mysql.dialect()
        for name in NEW_TABLES:
            with self.subTest(table=name):
                ddl = str(CreateTable(self.new_tables[name]).compile(dialect=dialect))
                self.assertIn('ENGINE=InnoDB', ddl)
                self.assertRegex(ddl, r'DEFAULT CHARSET=utf8mb4')
                self.assertRegex(ddl, r'COLLATE[= ]utf8mb4_bin')
                self.assertNotIn('CASCADE', ddl.upper())

    def test_foreign_key_targets_are_unique_and_typed(self):
        for name in NEW_TABLES:
            table = self.new_tables[name]
            for fk in table.foreign_key_constraints:
                with self.subTest(fk=fk.name):
                    parent = fk.elements[0].column.table
                    parent_cols = [element.column.name for element in fk.elements]
                    target_keys = [tuple(parent.primary_key.columns.keys())]
                    target_keys += [tuple(c.columns.keys()) for c in parent.constraints
                                    if isinstance(c, UniqueConstraint)]
                    self.assertIn(tuple(parent_cols), target_keys,
                                  '%s references non-unique %s(%s)' % (fk.name, parent.name, parent_cols))
                    for element in fk.elements:
                        self.assertEqual(str(element.parent.type), str(element.column.type),
                                         'type mismatch in %s' % fk.name)
                    self.assertEqual(fk.ondelete, 'RESTRICT')
                    self.assertEqual(fk.onupdate, 'RESTRICT')

    def test_no_new_table_redefines_frozen_table(self):
        self.assertTrue(set(NEW_TABLES).isdisjoint(self.frozen_tables))
        self.assertEqual(len(self.frozen_tables), 16)

    def test_autoincrement_columns_compile_as_bigint(self):
        manifest = {t['name']: t for t in load_manifest()['tables']}
        for name in NEW_TABLES:
            ddl = str(CreateTable(self.new_tables[name]).compile(dialect=mysql.dialect()))
            expected = sum(1 for c in manifest[name]['columns'] if c['auto_increment'])
            self.assertEqual(ddl.count('AUTO_INCREMENT'), expected,
                             '%s must expose exactly %d auto increment columns' % (name, expected))
            for column in manifest[name]['columns']:
                if column['auto_increment']:
                    self.assertIn(column['name'], manifest[name]['primary_key'])


class FrozenAuthorityTests(unittest.TestCase):
    def test_models_hash_matches_pin(self):
        pins = json.loads(PIN_PATH.read_text(encoding='utf-8'))
        digest = hashlib.sha256(MODELS_PATH.read_bytes()).hexdigest()
        self.assertEqual(digest, pins['files']['models.py'])

    def test_manifest_does_not_claim_live_compatibility(self):
        manifest = load_manifest()
        text = json.dumps(manifest, ensure_ascii=False)
        self.assertIn('未连接任何 MySQL 实例', text)
        for claim in ('已兼容实际实例', '已在实例验证', '已验证通过'):
            self.assertNotIn(claim, text)


def build_type(spec):
    base, args, unsigned, charset, collation = spec['base'], spec['args'], spec['unsigned'], spec['charset'], spec['collation']
    if base == 'BIGINT':
        return sa.BigInteger()
    if base == 'INT':
        return sa.Integer()
    if base == 'TINYINT':
        return mysql.TINYINT(unsigned=unsigned)
    if base == 'DATE':
        return sa.Date()
    if base == 'DATETIME':
        return mysql.DATETIME(fsp=args[0] if args else None)
    if base == 'DECIMAL':
        return sa.Numeric(*args)
    if base == 'JSON':
        return sa.JSON()
    if base == 'VARCHAR':
        if charset:
            return mysql.VARCHAR(args[0], charset=charset, collation=collation)
        return sa.VARCHAR(args[0])
    if base == 'CHAR':
        if charset:
            return mysql.CHAR(args[0], charset=charset, collation=collation)
        return sa.CHAR(args[0])
    raise AssertionError('unsupported type %r' % (spec,))


if __name__ == '__main__':
    unittest.main(verbosity=2)