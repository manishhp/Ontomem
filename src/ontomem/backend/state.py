"""Application state: one engine, one event store, accounts, proposals and evaluation jobs.

Everything persists under ``ONTOMEM_HOME`` (default ``./var``):
  events.sqlite      - the event log (source of truth for every memory)
  app.sqlite         - accounts, ontology proposals, evaluation runs
  ontology/          - evolved ontology versions (concepts-<version>.ttl) + changelog
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

import bcrypt

from ..core.engine import OntoMemEngine
from ..core.events import EventStore
from ..core.llm import from_env
from ..core.ontology import Ontology, default_ontology
from ..evolve import Evolver, Proposal

HOME = Path(os.environ.get("ONTOMEM_HOME", "var"))

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL, is_demo INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, surface TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT, occurrences INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY, params TEXT NOT NULL, status TEXT NOT NULL, progress TEXT, result_dir TEXT,
    created_at TEXT NOT NULL, finished_at TEXT, error TEXT, created_by TEXT
);
"""


def _data(name: str) -> Path:
    return Path(str(resources.files("ontomem") / "data" / name))


class AppState:
    def __init__(self, home: str | Path | None = None, seed_demo: bool = True) -> None:
        self.home = Path(home) if home else HOME
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "ontology").mkdir(exist_ok=True)
        self.db = sqlite3.connect(str(self.home / "app.sqlite"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(APP_SCHEMA)
        self.lock = threading.RLock()
        self.ontology = self._load_ontology()
        self.store = EventStore(self.home / "events.sqlite")
        self.llm = from_env()
        self.engine = OntoMemEngine(ontology=self.ontology, store=self.store, llm=self.llm)
        self.evolver = Evolver(self.ontology, self.llm)
        self._saved_changes = 0
        self.scenarios = json.loads(_data("scenarios.json").read_text())
        self.started = time.time()
        self.requests = 0
        self.request_ms = 0.0
        self.jobs: dict[str, dict[str, Any]] = {}
        if seed_demo:
            self.seed_demo_accounts()

    # ------------------------------------------------------------ ontology
    def _changelog_path(self) -> Path:
        return self.home / "ontology" / "changelog.json"

    def changelog(self) -> list[dict[str, Any]]:
        p = self._changelog_path()
        return json.loads(p.read_text()) if p.exists() else []

    def _load_ontology(self) -> Ontology:
        versions = sorted((self.home / "ontology").glob("concepts-*.ttl"), key=lambda p: [int(x) for x in p.stem.split("-")[1].split(".")])
        if versions:
            o = Ontology.load(versions[-1])
            return o
        return default_ontology().copy()

    def save_ontology(self) -> Path:
        p = self.home / "ontology" / f"concepts-{self.ontology.version}.ttl"
        p.write_text(self.ontology.to_graph().serialize(format="turtle"))
        new = self.evolver.changelog[self._saved_changes:]
        self._saved_changes = len(self.evolver.changelog)
        self._changelog_path().write_text(json.dumps(self.changelog() + new, indent=1))
        return p

    # ------------------------------------------------------------ accounts
    def create_account(self, username: str, password: str, display_name: str | None = None, is_demo: bool = False) -> dict:
        uid = f"u-{uuid.uuid4().hex[:10]}"
        ph = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
        with self.lock:
            self.db.execute(
                "INSERT INTO accounts (id, username, display_name, password_hash, created_at, is_demo) VALUES (?,?,?,?,?,?)",
                (uid, username.lower(), display_name or username, ph, datetime.now().isoformat(timespec="seconds"), int(is_demo)),
            )
            self.db.commit()
        return self.account(uid)  # type: ignore[return-value]

    def _one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self.lock:  # the connection is shared by request threads
            return self.db.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def account(self, uid: str) -> dict | None:
        r = self._one("SELECT id, username, display_name, created_at, is_demo FROM accounts WHERE id=?", (uid,))
        return dict(r) if r else None

    def account_by_name(self, username: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM accounts WHERE username=?", (username.lower(),))

    def verify(self, username: str, password: str) -> dict | None:
        r = self.account_by_name(username)
        if r and bcrypt.checkpw(password.encode(), r["password_hash"].encode()):
            return self.account(r["id"])
        return None

    def accounts(self) -> list[dict]:
        return [dict(r) for r in self._all("SELECT id, username, display_name, is_demo FROM accounts ORDER BY username")]

    def seed_demo_accounts(self) -> None:
        for d in self.scenarios["demo_users"]:
            if self.account_by_name(d["username"]):
                continue
            acc = self.create_account(d["username"], d["password"], d["display_name"], is_demo=True)
            for t in d["turns"]:
                self.engine.observe(acc["id"], t["text"], datetime.fromisoformat(t["ts"]))
            self.collect_proposals()

    def reset_demo(self, uid: str) -> None:
        acc = self.account(uid)
        if not acc or not acc["is_demo"]:
            raise ValueError("only demo accounts can be reset")
        self.engine.erase(uid)
        d = next(x for x in self.scenarios["demo_users"] if x["username"] == acc["username"])
        for t in d["turns"]:
            self.engine.observe(uid, t["text"], datetime.fromisoformat(t["ts"]))

    # ----------------------------------------------------------- proposals
    def collect_proposals(self) -> int:
        """Turn unmapped / partially mapped phrases seen in conversations into review items."""
        with self.lock:
            return self._collect_proposals()

    def _collect_proposals(self) -> int:
        added = 0
        seen: dict[str, int] = {}
        for rec in self.engine.unmapped_log:
            s = str(rec.get("surface") or "").strip().lower()
            if s:
                seen[s] = seen.get(s, 0) + 1
        self.engine.unmapped_log.clear()
        for s, n in seen.items():
            row = self.db.execute("SELECT id, occurrences FROM proposals WHERE surface=? AND status='pending'", (s,)).fetchone()
            if row:
                self.db.execute("UPDATE proposals SET occurrences=? WHERE id=?", (row["occurrences"] + n, row["id"]))
                continue
            need, why = self.evolver.needs_review(s)
            if not need:
                continue
            props = [p.to_dict() for p in self.evolver.propose(s)] + [p.to_dict() for p in self.evolver.alias_candidates(s)]
            self.db.execute(
                "INSERT INTO proposals (id, surface, kind, payload, status, created_at, occurrences) VALUES (?,?,?,?,?,?,?)",
                (f"pr-{uuid.uuid4().hex[:8]}", s, why, json.dumps(props), "pending", datetime.now().isoformat(timespec="seconds"), n),
            )
            added += 1
        self.db.commit()
        return added

    def proposals(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM proposals" + (" WHERE status=?" if status else "") + " ORDER BY occurrences DESC, created_at DESC"
        rows = self._all(q, (status,) if status else ())
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def decide(self, pid: str, choice: int | None, approve: bool, reviewer: str,
               parent: str | None = None, label: str | None = None) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("proposal not found or already decided")
            result: dict[str, Any] = {"status": "rejected"}
            if approve:
                options = json.loads(row["payload"])
                if parent:  # manual placement by the reviewer
                    from ..evolve import concept_id_for

                    p = Proposal(row["surface"], concept_id_for(label or row["surface"]), label or row["surface"], parent, 1.0, "manual")
                    version = self.evolver.apply(p, reviewer)
                else:
                    o = options[choice or 0]
                    p = Proposal(**{k: o[k] for k in ("surface", "concept_id", "label", "parent", "score", "source")})
                    version = self.evolver.apply_alias(p, reviewer) if o["source"] == "alias" else self.evolver.apply(p, reviewer)
                self.save_ontology()
                rekeyed = {}
                self.engine.mapper.__init__(self.ontology)
                self.engine.retriever.mapper = self.engine.mapper
                for uid in self.engine.users():
                    r = self.engine.rekey(uid)
                    if r:
                        rekeyed[uid] = r
                result = {"status": "approved", "version": version, "rekeyed_users": len(rekeyed),
                          "rekeyed": sum(len(v) for v in rekeyed.values())}
            self.db.execute("UPDATE proposals SET status=?, decided_at=?, decided_by=? WHERE id=?",
                            ("approved" if approve else "rejected", datetime.now().isoformat(timespec="seconds"), reviewer, pid))
            self.db.commit()
            return result
