"""Explicit PostgreSQL compatibility boundary for the existing SQL application.

SQLite remains the default. PostgreSQL requires an independently migrated schema.
This is a bounded adapter, not a general SQLite SQL interpreter. Unsupported DDL
and PRAGMAs fail closed. SQL literals/comments are never rewritten as SQL tokens.
"""
import os
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal

SCHEMA_VERSION = '0252-compat-v2'
WRITER_LOCK = 733252001
_PROTECTED = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/")


def postgres_enabled():
    url = os.environ.get('DATABASE_URL', '')
    if url and not url.startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('DATABASE_URL must be a PostgreSQL URL; omit it to use SQLite.')
    return bool(url)


def protect(sql):
    values = []
    def stash(match):
        values.append(match.group())
        return f'__sql_literal_{len(values)-1}__'
    masked = _PROTECTED.sub(stash, sql)
    def restore(text, escape_percent=False):
        return re.sub(r'__sql_literal_(\d+)__', lambda m: values[int(m[1])].replace('%','%%') if escape_percent else values[int(m[1])], text)
    return masked, restore


def translate_sql(sql, bound=False):
    masked, restore = protect(sql.strip().rstrip(';'))
    masked = re.sub(r'\bgroup_concat\s*\(', 'string_agg(', masked, flags=re.I)
    for old,new in [('date','invoice_sqlite_date'),('datetime','invoice_sqlite_datetime'),('julianday','invoice_sqlite_julianday'),('instr','strpos'),('ifnull','coalesce')]:
        masked = re.sub(r'\b'+old+r'\s*\(', new+'(', masked, flags=re.I)
    masked = re.sub(r'\bsqlite_master\b', 'invoice_sqlite_master', masked, flags=re.I)
    masked = re.sub(r'\b([a-zA-Z_][\w.]*)\s+collate\s+nocase\b', r'lower(\1)', masked, flags=re.I)
    # C collation gives the ASCII case folding used by default SQLite LIKE.
    masked = re.sub(r'\b(not\s+)?like\b', lambda m: 'COLLATE "C" '+('NOT ' if m[1] else '')+'ILIKE', masked, flags=re.I)
    def glob(m):
        literal = restore(m[1])
        if not re.fullmatch(r"'(?:[A-Z]|\[0-9\])+'", literal):
            raise sqlite3.NotSupportedError('Unreviewed GLOB expression')
        return "~ '^"+literal[1:-1]+"$'"
    masked = re.sub(r'\bglob\s+(__sql_literal_\d+__)', glob, masked, flags=re.I)
    if re.match(r'insert\s+or\s+ignore\b', masked, re.I):
        masked = re.sub(r'insert\s+or\s+ignore', 'insert', masked, count=1, flags=re.I)
        masked += ' ON CONFLICT DO NOTHING'
    if re.match(r'insert\s+or\s+replace\b', masked, re.I):
        m = re.match(r'insert\s+or\s+replace\s+into\s+(\w+)\s*\(([^)]+)\)', masked, re.I)
        keys = {'settings':['key'], 'ai_photo_analysis':['photo_path','photo_hash','analysis_model','analysis_version']}
        if not m or m[1].lower() not in keys:
            raise sqlite3.NotSupportedError('Unreviewed REPLACE target')
        columns = [c.strip() for c in m[2].split(',')]
        target = keys[m[1].lower()]
        if any(not re.fullmatch(r'\w+', c) for c in columns) or not set(target) <= set(columns):
            raise sqlite3.NotSupportedError('Unreviewed REPLACE columns')
        masked = re.sub(r'insert\s+or\s+replace', 'insert', masked, count=1, flags=re.I)
        masked += ' ON CONFLICT ('+','.join(target)+') DO UPDATE SET '+','.join(c+'=excluded.'+c for c in columns if c not in target)
    # psycopg's percent escaping also applies inside literals when params bind.
    if bound:
        masked = masked.replace('%','%%').replace('?', '%s')
    return restore(masked, escape_percent=bound)


class Row:
    def __init__(self, names, values):
        self._names = names
        self._values = tuple(self.normalize(v) for v in values)
    @staticmethod
    def normalize(value):
        if isinstance(value, (date,datetime)): return value.isoformat()
        if isinstance(value, Decimal): return float(value)
        return value
    def keys(self): return self._names
    def __getitem__(self, key):
        if isinstance(key, (int,slice)): return self._values[key]
        # sqlite3.Row returns the first column when duplicate labels exist.
        return self._values[self._names.index(key)]
    def __iter__(self): return iter(self._values)
    def __len__(self): return len(self._values)


class Cursor:
    def __init__(self, cursor, generated=False):
        self._cursor=cursor
        self.lastrowid=None
        self.rowcount=cursor.rowcount
        self.description=cursor.description
        if generated:
            item=cursor.fetchone()
            self.lastrowid=item[0] if item else None
            self.description=None
    def _row(self, item):
        return None if item is None else Row([d.name for d in self.description], item)
    def fetchone(self): return self._row(self._cursor.fetchone())
    def fetchall(self): return [self._row(r) for r in self._cursor.fetchall()]
    def fetchmany(self,size=1): return [self._row(r) for r in self._cursor.fetchmany(size)]
    def __iter__(self):
        while True:
            item=self.fetchone()
            if item is None: break
            yield item


class PostgreSQLConnection:
    dialect='postgresql'
    def __init__(self, url=None):
        import psycopg
        self.driver=psycopg
        self.raw=psycopg.connect(url or os.environ['DATABASE_URL'], autocommit=True, cursor_factory=psycopg.ClientCursor,
            connect_timeout=10, application_name='invoice-tool', options='-c timezone=UTC -c lock_timeout=15000 -c statement_timeout=60000')
        self._locked=False
        self.identities={r[0]:r[1] for r in self.raw.execute("select table_name,column_name from information_schema.columns where table_schema='public' and is_identity='YES'")}
    @property
    def in_transaction(self): return self.raw.info.transaction_status != self.driver.pq.TransactionStatus.IDLE
    def begin_write(self):
        if not self.in_transaction: self.raw.execute('BEGIN')
        if not self._locked:
            # Phase 1 preserves SQLite's serialized writes. Lock before number
            # allocation and before DML. Later tuning can narrow lock scope.
            self.raw.execute('select pg_advisory_xact_lock(%s)',(WRITER_LOCK,))
            self._locked=True
    def execute(self, sql, params=None):
        masked,_=protect(sql.strip())
        kind=masked.split(None,1)[0].lower() if masked.strip() else ''
        if re.fullmatch(r'begin(?:\s+immediate)?\s*;?',sql.strip(),re.I):
            self.begin_write()
            return Cursor(self.raw.execute('select 1 where false'))
        if kind=='pragma':
            match=re.fullmatch(r'pragma\s+table_info\(\s*"?(\w+)"?\s*\)\s*;?',sql.strip(),re.I)
            if not match: raise sqlite3.NotSupportedError('SQLite PRAGMA is unavailable on PostgreSQL')
            return Cursor(self.raw.execute('select cid,name,type,"notnull",dflt_value,pk from invoice_sqlite_columns where table_name=%s order by cid',(match[1],)))
        if kind not in {'select','with','insert','update','delete','explain','savepoint','release','rollback'}:
            raise sqlite3.NotSupportedError('Schema changes require the PostgreSQL migration tool')
        writing=kind in {'insert','update','delete'} or (kind=='with' and re.search(r'\b(insert|update|delete)\b',masked,re.I))
        if writing: self.begin_write()
        translated=translate_sql(sql, params is not None)
        generated=False
        match=re.match(r'insert\s+into\s+(\w+)\b',translated,re.I)
        if match and match[1] in self.identities and not re.search(r'\breturning\b',masked,re.I):
            translated += ' RETURNING '+self.identities[match[1]]
            generated=True
        savepoint=self.in_transaction and kind not in {'savepoint','release','rollback'}
        if savepoint: self.raw.execute('SAVEPOINT invoice_statement')
        try:
            cursor=self.raw.execute(translated, params)
            result=Cursor(cursor, generated)
            if savepoint: self.raw.execute('RELEASE SAVEPOINT invoice_statement')
            return result
        except self.driver.Error as error:
            if savepoint:
                self.raw.execute('ROLLBACK TO SAVEPOINT invoice_statement')
                self.raw.execute('RELEASE SAVEPOINT invoice_statement')
            cls=sqlite3.IntegrityError if isinstance(error,self.driver.IntegrityError) else sqlite3.OperationalError
            raise cls(str(error)) from error
    def executemany(self,sql,items):
        result=None
        for params in items: result=self.execute(sql,params)
        return result
    def executescript(self,sql):
        raise sqlite3.NotSupportedError('Run versioned PostgreSQL migrations before application startup')
    def commit(self):
        self.raw.commit()
        self._locked=False
    def rollback(self):
        self.raw.rollback()
        self._locked=False
    def close(self):
        self.raw.close()
    def __enter__(self): return self
    def __exit__(self,exc_type,exc,tb):
        self.rollback() if exc_type else self.commit()


def lock_number_allocation(connection):
    if getattr(connection,'dialect',None)=='postgresql': connection.begin_write()


def verify_postgres_schema():
    c=PostgreSQLConnection()
    try:
        row=c.raw.execute('select version from invoice_schema_version where singleton=1').fetchone()
        if not row or row[0]!=SCHEMA_VERSION:
            raise RuntimeError('PostgreSQL schema version mismatch; run the migration tool first.')
    finally:
        c.close()
