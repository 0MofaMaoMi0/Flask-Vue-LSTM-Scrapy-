"""Pinned frozen contract and executable OFFLINE value/relationship checks.

SQLite is used only for scalar SELECT evaluation of trusted frozen CHECK text;
this is not a MySQL semantics or isolation proof. No engine or live connector.
"""
import ast
import copy
import hashlib
import json
import pathlib
import re
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import Boolean, Integer, BigInteger, SmallInteger, Numeric, String, JSON, Date, DateTime, CheckConstraint, UniqueConstraint
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import mysql

ROOT = pathlib.Path(__file__).parent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()


def authority(root=ROOT):
    pins = json.loads((ROOT / 'm6_frozen_authority.json').read_text(encoding='utf-8'))
    hashes = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in pins['files']}
    if hashes != pins['files']:
        raise ValueError('FROZEN_AUTHORITY_HASH_MISMATCH')
    # These helpers and constructor expressions are executed only after hashes.
    from .offline_contract_check import signature, load_models, canonical_ddl
    ns = load_models((root / 'models.py').read_text(encoding='utf-8'))
    metadata = ns['CatalogBase'].metadata
    expected = json.loads((root / 'design_expected_columns.json').read_text(encoding='utf-8'))
    for t in expected['structure'].values():
        t['ddl'] = canonical_ddl(t['ddl'])
    if signature(metadata) != expected['structure']:
        raise ValueError('FROZEN_MODEL_CONTRACT_MISMATCH')
    import sqlalchemy as sa
    tree = ast.parse((root / 'drafts/0002_catalog_draft.py').read_text(encoding='utf-8'))
    up = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'upgrade')
    down = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'downgrade')
    if not isinstance(up.body[0], ast.Raise) or not isinstance(down.body[0], ast.Raise):
        raise ValueError('FROZEN_GUARD_MISSING')
    mm, order, calls = sa.MetaData(), [], []
    for node in up.body[1:]:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            raise ValueError('UNEXPECTED_MIGRATION_NODE')
        call = node.value
        if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != 'op':
            raise ValueError('UNEXPECTED_MIGRATION_CALL')
        args = [eval(compile(ast.Expression(a), 'pinned-declaration', 'eval'), ns) for a in call.args]
        kw = {k.arg: eval(compile(ast.Expression(k.value), 'pinned-declaration', 'eval'), ns) for k in call.keywords}
        if call.func.attr == 'create_table':
            sa.Table(args[0], mm, *args[1:], **kw);order.append(args[0]);calls.append(('table', args[0]))
        elif call.func.attr == 'create_index':
            sa.Index(args[0], *[mm.tables[args[1]].c[n] for n in args[2]], **kw);calls.append(('index', (args[1], args[0])))
        else:
            raise ValueError('UNEXPECTED_MIGRATION_OPERATION')
    if signature(mm) != expected['structure']:
        raise ValueError('FROZEN_MIGRATION_CONTRACT_MISMATCH')
    positions = {n: i for i, n in enumerate(order)}
    if any(positions[e.column.table.name] >= positions[t.name] for t in mm.tables.values() for fk in t.foreign_key_constraints for e in fk.elements):
        raise ValueError('INVALID_PARENT_ORDER')
    dropped = [ast.literal_eval(n.value.args[0]) for n in down.body[1:]]
    if dropped != order[::-1]:
        raise ValueError('INVALID_FROZEN_REVERSE_ORDER')
    statements = []
    for kind, key in calls:
        if kind == 'table':
            sql = canonical_ddl(str(CreateTable(mm.tables[key]).compile(dialect=mysql.dialect())))
            obj = key
        else:
            table, name = key
            sql = str(CreateIndex(next(i for i in mm.tables[table].indexes if i.name == name)).compile(dialect=mysql.dialect()))
            obj = name
        statements.append(dict(kind=kind, object=obj, sql=sql, sha256=hashlib.sha256(sql.encode()).hexdigest()))
    return dict(metadata=metadata, order=order, statements=statements, hashes=hashes, contract_hash=digest(hashes))


def _json_object(value):
    if isinstance(value, str):
        def constant(_):
            raise ValueError('NONFINITE_JSON')
        def pairs(items):
            result = {}
            for key, v in items:
                if key in result:
                    raise ValueError('DUPLICATE_JSON_KEY')
                result[key] = v
            return result
        value = json.loads(value, parse_constant=constant, object_pairs_hook=pairs)
    if type(value) is not dict:
        raise ValueError('JSON_OBJECT_REQUIRED')
    json.dumps(value, allow_nan=False, ensure_ascii=False).encode('utf-8')
    return copy.deepcopy(value)


def verify_bundle(bundle):
    fresh=authority()
    from .offline_contract_check import signature
    if (any(bundle.get(k)!=fresh[k] for k in ('hashes','order','statements','contract_hash'))
            or signature(bundle['metadata'])!=signature(fresh['metadata'])):
        raise ValueError('IN_MEMORY_AUTHORITY_TAMPER')
    return fresh


def scalar(column, value, *, source=False):
    if value is None:
        if not column.nullable:
            raise ValueError('NOT_NULL')
        return None
    typ = column.type
    if isinstance(typ, Boolean):
        if type(value) not in (int, bool) or value not in (0, 1):
            raise ValueError('BOOLEAN')
        return bool(value)
    if isinstance(typ, Integer):
        bits = 64 if isinstance(typ, BigInteger) else 16 if isinstance(typ, SmallInteger) else 32
        if type(value) is not int or not -(2**(bits-1)) <= value < 2**(bits-1):
            raise ValueError('SIGNED_INTEGER_RANGE')
        if column.primary_key and value <= 0:
            raise ValueError('POSITIVE_INTERNAL_ID')
        return value
    if isinstance(typ, Numeric):
        if type(value) not in (str, int, Decimal):
            raise ValueError('DECIMAL_EXACT_INPUT_REQUIRED')
        try:
            result = Decimal(value)
        except InvalidOperation:
            raise ValueError('DECIMAL') from None
        if not result.is_finite() or result.as_tuple().exponent < -typ.scale or abs(result) >= Decimal(10)**(typ.precision-typ.scale):
            raise ValueError('DECIMAL_PRECISION_SCALE')
        return result
    if isinstance(typ, JSON):
        return _json_object(value)
    if isinstance(typ, DateTime):
        fraction=re.search(r'[T ]\d{2}:\d{2}:\d{2}\.(\d+)',value) if isinstance(value,str) else None
        if fraction and len(fraction.group(1))>6:
            raise ValueError('DATETIME_MICROSECOND_PRECISION')
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if type(result) is not datetime:
            raise ValueError('DATETIME')
        if source and isinstance(value, str) and result.tzinfo is None:
            raise ValueError('SOURCE_TIMEZONE_REQUIRED')
        return result.astimezone(timezone.utc).replace(tzinfo=None) if result.tzinfo else result
    if isinstance(typ, Date):
        if isinstance(value, str):
            result = date.fromisoformat(value)
            if result.isoformat() != value:
                raise ValueError('DATE_CANONICAL')
            return result
        if type(value) is not date:
            raise ValueError('DATE')
        return value
    if isinstance(typ, String):
        if type(value) is not str:
            raise ValueError('STRING')
        value.encode('utf-8')
        if typ.length is not None and len(value) > typ.length:
            raise ValueError('STRING_LENGTH')
        if column.name in ('source', 'external_id', 'source_record_id', 'tmdb_genre_id', 'source_credit_key', 'source_path', 'title_normalized') and value != value.strip():
            raise ValueError('IDENTIFIER_WHITESPACE')
        if getattr(typ, 'charset', None) == 'ascii':
            value.encode('ascii')
            if typ.length==64 and not re.fullmatch(r'[0-9a-f]{64}',value):
                raise ValueError('SHA256_HEX')
        return value
    raise ValueError('UNSUPPORTED_FROZEN_TYPE')


def generated(name, row):
    if name == 'core_source_slot':
        return row['source'] if row['source'] in ('douban','tmdb','imdb') else None
    if name == 'approved_source_record_id':
        return row['source_record_id'] if row['status'] == 'approved' else None
    if name == 'primary_slot':
        return row['asset_type'] if row['is_primary'] else None
    raise ValueError('UNKNOWN_GENERATED_COLUMN')


def validate_rows(bundle, rows, *, source_projection=False):
    """Returns typed copies and issues. Never modifies input or writes SQL."""
    md, issues, typed = bundle['metadata'], [], {n: [] for n in bundle['metadata'].tables}
    positions={n:[] for n in md.tables}
    def issue(table, index, code, detail):
        issues.append(dict(table=table, row=index, code=code, detail=detail))
    for unknown in sorted(set(rows)-set(md.tables)):
        issue(unknown, None, 'UNKNOWN_TABLE', unknown)
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA query_only=ON')
    conn.create_function('CHAR_LENGTH', 1, lambda s: None if s is None else len(s))
    try:
        for name, table in md.tables.items():
            for index, raw in enumerate(rows.get(name, [])):
                if type(raw) is not dict or set(raw) != set(table.c.keys()):
                    issue(name,index,'COLUMN_SET','full explicit column set required');continue
                result, bad = {}, False
                for col in table.c:
                    if col.computed is not None:
                        continue
                    if source_projection and name=='movie_source_records' and col.name=='ingest_batch_id' and raw[col.name] is None:
                        result[col.name]=None;continue  # Explicit new-target-batch conversion only.
                    try:
                        result[col.name]=scalar(col,raw[col.name],source=source_projection)
                    except (ValueError,TypeError,ArithmeticError,UnicodeError) as exc:
                        issue(name,index,'TYPE_OR_VALUE',col.name)
                        issues[-1]['reason']=str(exc) if re.fullmatch('[A-Z][A-Z0-9_]+',str(exc)) else 'INVALID_LITERAL'
                        bad=True
                if bad:
                    continue
                for col in table.c:
                    if col.computed is not None:
                        result[col.name]=generated(col.name,result)
                        if raw[col.name] is not None and raw[col.name]!=result[col.name]:
                            issue(name,index,'GENERATED_VALUE',col.name)
                select, params = [], []
                for col in table.c:
                    value=result[col.name]
                    if isinstance(value,(date,datetime,Decimal)):
                        value=str(value)
                    elif isinstance(value,dict):
                        value=json.dumps(value,allow_nan=False)
                    cast='NUMERIC' if isinstance(col.type,Numeric) else 'INTEGER' if isinstance(col.type,(Integer,Boolean)) else 'TEXT'
                    select.append('CAST(? AS '+cast+') AS "'+col.name+'"');params.append(value)
                for check in table.constraints:
                    if isinstance(check,CheckConstraint):
                        answer=conn.execute('SELECT ('+str(check.sqltext)+') FROM (SELECT '+','.join(select)+')',params).fetchone()[0]
                        if answer==0:
                            issue(name,index,'CHECK',check.name)
                typed[name].append(result)
                positions[name].append(index)
        for name, table in md.tables.items():
            for constraint in [table.primary_key]+[c for c in table.constraints if isinstance(c,UniqueConstraint)]:
                keys=list(constraint.columns.keys());seen=set()
                for index,row in enumerate(typed[name]):
                    parts=[]
                    for key_name in keys:
                        value=row[key_name];column=table.c[key_name]
                        if isinstance(value,str):
                            value=value.rstrip(' ')
                            if not getattr(column.type,'collation',None) or str(column.type.collation).endswith('_ci'):
                                # Default server collation is not proven here.
                                # Do not claim Unicode uniqueness equivalence.
                                if not value.isascii():
                                    issue(name,positions[name][index],'COLLATION_UNVERIFIED',key_name)
                                value=value.casefold()  # Conservative ASCII collision review.
                        parts.append(value)
                    key=tuple(parts)
                    if not keys or any(v is None for v in key):
                        continue
                    if key in seen:
                        issue(name,positions[name][index],'UNIQUE',constraint.name or 'PRIMARY_KEY')
                    seen.add(key)
            for fk in table.foreign_key_constraints:
                dest=fk.elements[0].column.table.name
                allowed={tuple(row[e.column.name] for e in fk.elements) for row in typed[dest]}
                for index,row in enumerate(typed[name]):
                    key=tuple(row[e.parent.name] for e in fk.elements)
                    if not any(v is None for v in key) and key not in allowed:
                        issue(name,positions[name][index],'FK',','.join(e.parent.name for e in fk.elements)+'->'+dest)
    finally:
        conn.close()
    return typed, issues


def full_row(bundle, table, values):
    row={c.name:None for c in bundle['metadata'].tables[table].c}
    for col in bundle['metadata'].tables[table].c:
        if col.server_default is not None and col.computed is None:
            value=str(col.server_default.arg).strip("'")
            row[col.name]=int(value) if isinstance(col.type,(Integer,Boolean)) else value
    row.update(values)
    return row
