"""Durable aggregate model-attempt admission; independent of visitor/project state."""
import os
from pathlib import Path
import sqlite3


class Admission:
    def __init__(self, path, maximum):
        if type(maximum) is not int or maximum < 1:
            raise ValueError('Explicit positive aggregate model-call allowance required')
        self.path = Path(path)
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise ValueError('Invalid admission storage')
        self.maximum = maximum
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS allowance (id INTEGER PRIMARY KEY CHECK(id=1), maximum INTEGER NOT NULL, used INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO allowance VALUES (1, ?, 0)', (maximum,))
            if db.execute('SELECT maximum FROM allowance WHERE id=1').fetchone()[0] != maximum:
                raise ValueError('Stored allowance differs; operator reconciliation required')
        os.chmod(self.path, 0o600)

    def connect(self):
        if self.path.is_symlink():
            raise ValueError('Invalid admission storage')
        return sqlite3.connect(self.path, timeout=5)

    def reserve(self):
        # Commit before invocation. Failed/uncertain attempts are never refunded.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE allowance SET used=used+1 WHERE id=1 AND used<maximum').rowcount
            return changed == 1
