"""Read-only network and PostgreSQL checks. Never displays credentials."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys

from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    saved = dotenv_values(ROOT / '.env').get('DATABASE_URL')
    active = os.environ.get('DATABASE_URL') or saved
    if not active:
        print('CONFIG: FAIL - DATABASE_URL is missing.')
        return 1
    if os.environ.get('DATABASE_URL') and saved and active != saved:
        print('CONFIG: WARNING - your Terminal DATABASE_URL overrides and differs from project .env.')
    try:
        config = conninfo_to_dict(active)
        host = config['host']
        port = int(config.get('port', '5432'))
    except (psycopg.Error, KeyError, ValueError):
        print('CONFIG: FAIL - DATABASE_URL cannot be parsed.')
        return 1
    print(f'CONFIG: parsed; port={port}; sslmode={config.get("sslmode", "default")}.')
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        print('DNS: FAIL - the database hostname cannot be resolved on this network.')
        return 1
    print('DNS: PASS')
    connected = False
    for family, kind, protocol, _, address in addresses[:4]:
        try:
            with socket.socket(family, kind, protocol) as sock:
                sock.settimeout(4)
                sock.connect(address)
            connected = True
            break
        except OSError:
            continue
    if not connected:
        print(f'TCP: FAIL - cannot reach database port {port}.')
        print('Check Tiger service status and any attached IP allow list; compare using a phone hotspot.')
        return 1
    print('TCP: PASS')
    try:
        with psycopg.connect(active, connect_timeout=8, options='-c default_transaction_read_only=on') as conn:
            version = conn.execute('SHOW server_version').fetchone()[0]
            extensions = conn.execute('SELECT extname,extversion FROM pg_extension ORDER BY extname').fetchall()
        print('POSTGRES: PASS - SSL/authentication/query succeeded.')
        print('PostgreSQL version:', version)
        print('Extensions:', extensions)
        return 0
    except psycopg.Error as exc:
        # Raw libpq exceptions can contain connection details; display categories only.
        if exc.sqlstate == '28P01':
            reason = 'password authentication failed'
        elif exc.sqlstate == '3D000':
            reason = 'database name does not exist'
        elif isinstance(exc, psycopg.errors.ConnectionTimeout):
            reason = 'TCP succeeded but PostgreSQL connection setup timed out'
        else:
            reason = f'{type(exc).__name__}; SQLSTATE={exc.sqlstate or "unavailable"}'
        print('POSTGRES: FAIL -', reason)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
