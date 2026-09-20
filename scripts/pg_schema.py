"""Read SQLite schema metadata without importing or initializing the app."""
import sqlite3

def qi(s): return '"'+s.replace('"','""')+'"'

def schema_manifest(c):
    c.row_factory=sqlite3.Row
    manifest={}
    for row in c.execute("select name,sql from sqlite_master where type='table' and name not like 'sqlite_%' order by name").fetchall():
        table=row['name']
        indexes=[]
        for index in c.execute('pragma index_list('+qi(table)+')'):
            indexes.append({**dict(index),'columns':[dict(v) for v in c.execute('pragma index_info('+qi(index['name'])+')')],
                'sql':(c.execute('select sql from sqlite_master where name=?',(index['name'],)).fetchone() or [None])[0]})
        manifest[table]={'sql':row['sql'],'columns':[dict(v) for v in c.execute('pragma table_info('+qi(table)+')')],
                         'foreign_keys':[dict(v) for v in c.execute('pragma foreign_key_list('+qi(table)+')')],
                         'indexes':indexes}
    return manifest
