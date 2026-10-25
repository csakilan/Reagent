"""Bucket ACLs and redacted memory storage; all identity is runtime supplied."""
from __future__ import annotations
from contextlib import contextmanager
import math
import os
import threading
from connectors.common import TRUSTED_HEADERS, env_redactor

CONNECTIONS = threading.BoundedSemaphore(3)


def permitted(acl: list[str], team: str, admin: bool) -> bool:
    return '*' in acl or team in acl or (admin and 'admins' in acl)


def write_status(kind: str, runtime: bool) -> str:
    if kind == 'episodic' and not runtime:
        raise ValueError('E_MEMORY_RUNTIME_ONLY')
    return 'pending' if kind == 'procedural' else 'active'


def vector_literal(embedding: list[float]) -> str:
    if len(embedding) != 1536 or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in embedding):
        raise ValueError('Expected 1536 finite embedding values')
    return '[' + ','.join(str(float(v)) for v in embedding) + ']'


@contextmanager
def connection():
    import psycopg
    if not os.environ.get('DATABASE_URL'):
        raise RuntimeError('Memory database is not configured')
    with CONNECTIONS:
        try:
            with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10) as conn:
                yield conn
        except psycopg.Error:
            raise RuntimeError('Memory database operation failed') from None


class Memory:
    def bucket(self, conn, name: str, mode: str) -> tuple:
        headers = TRUSTED_HEADERS.get()
        team_id, user_id = headers.get('x-reagent-team-id'), headers.get('x-reagent-user-id')
        if not team_id or not user_id:
            raise ValueError('E_MEMORY_NO_PRINCIPAL')
        principal = conn.execute('SELECT t.name,u.is_enterprise_admin,EXISTS '
            '(SELECT 1 FROM team_members m WHERE m.team_id=t.id AND m.user_id=u.id) '
            'FROM teams t CROSS JOIN users u WHERE t.id=%s AND u.id=%s', (team_id, user_id)).fetchone()
        if not principal or not (principal[1] or principal[2]):
            raise ValueError('E_MEMORY_ACCESS_DENIED')
        row = conn.execute('SELECT id,type,"read","write" FROM memory_buckets WHERE name=%s FOR SHARE', (name,)).fetchone()
        if not row or not permitted(row[2 if mode == 'read' else 3], principal[0], principal[1]):
            raise ValueError('E_MEMORY_ACCESS_DENIED')
        return row

    def read(self, bucket: str, limit: int = 20) -> list[dict]:
        """Read active entries in a bucket allowed to the root caller's team."""
        with connection() as conn:
            row = self.bucket(conn, bucket, 'read')
            result = conn.execute("SELECT id,content,source_run_id FROM memory_items WHERE bucket_id=%s AND status='active' ORDER BY created_at DESC,id DESC LIMIT %s",
                                  (row[0], min(max(limit, 1), 100))).fetchall()
        return env_redactor()([{'id': r[0], 'content': r[1], 'source_run_id': r[2]} for r in result])

    def search(self, bucket: str, query: str, embedding: list[float] | None = None, limit: int = 20) -> list[dict]:
        """Search active entries by supplied vector or Postgres full text."""
        with connection() as conn:
            row = self.bucket(conn, bucket, 'read')
            count = min(max(limit, 1), 100)
            if embedding is not None:
                result = conn.execute("SELECT id,content,source_run_id FROM memory_items WHERE bucket_id=%s AND status='active' AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s",
                                      (row[0], vector_literal(embedding), count)).fetchall()
            else:
                result = conn.execute("SELECT id,content,source_run_id FROM memory_items WHERE bucket_id=%s AND status='active' AND to_tsvector('english',content) @@ plainto_tsquery('english',%s) ORDER BY ts_rank(to_tsvector('english',content),plainto_tsquery('english',%s)) DESC,id LIMIT %s",
                                      (row[0], query, query, count)).fetchall()
        return env_redactor()([{'id': r[0], 'content': r[1], 'source_run_id': r[2]} for r in result])

    def write(self, bucket: str, content: str, embedding: list[float] | None = None) -> dict:
        """Store redacted content; procedural proposals await reevaluation."""
        headers = TRUSTED_HEADERS.get()
        run_id = headers.get('x-reagent-run-id')
        if not run_id:
            raise ValueError('E_MEMORY_SOURCE_RUN_REQUIRED')
        with connection() as conn:
            row = self.bucket(conn, bucket, 'write')
            # Runtime-only episodic writes additionally require an actual system
            # principal established by the authenticated internal caller.
            status = write_status(row[1], runtime=headers.get('x-reagent-invocation') == 'system')
            if not conn.execute('SELECT 1 FROM runs WHERE id=%s AND team_id=%s LIMIT 1', (run_id, headers.get('x-reagent-team-id'))).fetchone():
                raise ValueError('E_MEMORY_SOURCE_RUN_INVALID')
            item = conn.execute('INSERT INTO memory_items (bucket_id,content,embedding,source_run_id,status) VALUES (%s,%s,%s::vector,%s,%s) RETURNING id',
                (row[0], env_redactor()(content), vector_literal(embedding) if embedding else None, run_id, status)).fetchone()[0]
        return {'id': item, 'status': status}
