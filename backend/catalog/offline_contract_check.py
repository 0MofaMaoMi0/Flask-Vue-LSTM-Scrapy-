"""Offline full frozen contract comparison; no engine or migration invocation."""
import ast,json,pathlib,sys,argparse
import sqlalchemy as sa
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects import mysql
ROOT=pathlib.Path(__file__).parent

def canonical_ddl(sql):
    # MySQL dialect emits table options in hash-dependent order; preserve all values.
    lines=sql.strip().splitlines()
    options=lines[-1]
    if options.startswith(')'):
        lines[-1]=')'+' '.join(sorted(options[1:].split()))
    return '\n'.join(lines)

def signature(metadata):
    dialect=mysql.dialect()
    return {t.name:{'columns':[{'name':c.name,'type':str(c.type.compile(dialect=dialect)),'nullable':c.nullable,'primary_key':c.primary_key,'autoincrement':c.autoincrement,'default':str(c.server_default.arg) if c.server_default is not None and c.computed is None else None,'computed':str(c.computed.sqltext) if c.computed is not None else None,'persisted':c.computed.persisted if c.computed is not None else None} for c in t.c], 'ddl':canonical_ddl(str(CreateTable(t).compile(dialect=dialect))), 'indexes':sorted(str(CreateIndex(i).compile(dialect=dialect)) for i in t.indexes)} for t in metadata.sorted_tables}

def load_models(source):
    ns={'__name__':'offline_catalog_declarations'}
    exec(compile(source,'catalog-models','exec'),ns)
    return ns

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);parser.add_argument('--negative-runtime',action='store_true');parser.add_argument('--negative-index',action='store_true');args=parser.parse_args()
    out=pathlib.Path(args.output_dir).resolve()
    if not out.is_dir() or out.drive.upper()=='C:':raise ValueError('Approved existing non-C output required')
    expected=json.loads((ROOT/'design_expected_columns.json').read_text(encoding='utf-8'))
    for frozen in expected['structure'].values():frozen['ddl']=canonical_ddl(frozen['ddl'])
    source=(ROOT/'models.py').read_text(encoding='utf-8')
    if args.negative_runtime:source=source.replace('runtime_minutes<=1500','runtime_minutes<=15000')
    ns=load_models(source);md=ns['CatalogBase'].metadata;actual=signature(md);results=[]
    def check(name,ok):results.append({'case':name,'expected':'equal/true','actual':bool(ok),'status':'PASS' if ok else 'FAIL'})
    check('exact table set',set(actual)==set(expected['structure']))
    for table,frozen in expected['structure'].items():
        check(table+' full columns and metadata',actual.get(table,{}).get('columns')==frozen['columns'])
        check(table+' full PK/UQ/FK/CHECK/type/options SQL',actual.get(table,{}).get('ddl')==frozen['ddl'])
        check(table+' all independent indexes',actual.get(table,{}).get('indexes')==frozen['indexes'])
    migration=(ROOT/'drafts/0002_catalog_draft.py').read_text(encoding='utf-8');tree=ast.parse(migration)
    upgrade=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='upgrade');down=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='downgrade')
    check('upgrade and downgrade guard',isinstance(upgrade.body[0],ast.Raise) and isinstance(down.body[0],ast.Raise))
    mm=sa.MetaData();created=[];dropped=[];env=dict(ns)
    for stmt in upgrade.body[1:]:
        if not isinstance(stmt,ast.Expr) or not isinstance(stmt.value,ast.Call):raise ValueError('Unknown migration statement')
        call=stmt.value
        if not isinstance(call.func,ast.Attribute) or not isinstance(call.func.value,ast.Name) or call.func.value.id!='op':raise ValueError('Unknown migration call')
        values=[eval(compile(ast.Expression(x),'declaration','eval'),env) for x in call.args];kw={x.arg:eval(compile(ast.Expression(x.value),'declaration','eval'),env) for x in call.keywords}
        if call.func.attr=='create_table':sa.Table(values[0],mm,*values[1:],**kw);created.append(values[0])
        elif call.func.attr=='create_index':
            if args.negative_index and values[0]=='ix_people_0':continue
            sa.Index(values[0],*[mm.tables[values[1]].c[x] for x in values[2]],**kw)
        else:raise ValueError('Not a DDL declaration')
    ms=signature(mm)
    for table in expected['structure']:check(table+' migration complete compiled contract',ms.get(table)==expected['structure'][table])
    positions={name:i for i,name in enumerate(created)}
    check('parent before child',all(positions[e.column.table.name]<positions[t.name] for t in mm.tables.values() for fk in t.foreign_key_constraints for e in fk.elements))
    for stmt in down.body[1:]:dropped.append(ast.literal_eval(stmt.value.args[0]))
    check('downgrade reverse order',dropped==created[::-1])
    check('no auth Base or app import', 'from app' not in source and 'create_all' not in migration and len(md.tables)==16)
    check('three virtual slots',sum(c.computed is not None and c.computed.persisted is False for t in md.tables.values() for c in t.c)==3)
    failed=sum(x['status']=='FAIL' for x in results)
    report={'cases':results,'failed':failed,'sqlalchemy':sa.__version__,'scope':'frozen full metadata plus AST declaration compilation only; no SQL execution'}
    (out/'contract_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'catalog_schema_preview.sql').write_text('-- DRAFT NOT EXECUTED\n'+';\n'.join(x['ddl']+';\n'+';\n'.join(x['indexes']) for x in actual.values()),encoding='utf-8')
    print('offline contract checks',len(results),'failed',failed)
    return 1 if failed else 0
if __name__=='__main__':raise SystemExit(main())
