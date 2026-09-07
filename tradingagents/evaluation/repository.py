from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import EvaluationEpisode, EvaluationOutcome
from .point_in_time import validate_episode_point_in_time


class EvaluationRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evaluation_episodes (
                    decision_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    decision_at TEXT NOT NULL,
                    data_as_of TEXT NOT NULL,
                    reference_price REAL NOT NULL,
                    benchmark_symbol TEXT NOT NULL,
                    benchmark_price REAL NOT NULL,
                    confidence REAL,
                    experiment_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS evaluation_outcomes (
                    decision_id TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    outcome_at TEXT NOT NULL,
                    asset_price REAL NOT NULL,
                    benchmark_price REAL NOT NULL,
                    asset_return_pct REAL NOT NULL,
                    benchmark_return_pct REAL NOT NULL,
                    excess_return_pct REAL NOT NULL,
                    directionally_correct INTEGER NOT NULL,
                    estimated_cost_pct REAL NOT NULL,
                    PRIMARY KEY (decision_id, horizon),
                    FOREIGN KEY (decision_id) REFERENCES evaluation_episodes(decision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_evaluation_experiment
                    ON evaluation_episodes(experiment_id, decision_at);
                """
            )

    def record_episode(self, episode: EvaluationEpisode) -> None:
        validate_episode_point_in_time(episode)
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_episodes VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    episode.decision_id,
                    episode.symbol,
                    episode.action,
                    episode.decision_at.isoformat(),
                    episode.data_as_of.isoformat(),
                    episode.reference_price,
                    episode.benchmark_symbol,
                    episode.benchmark_price,
                    episode.confidence,
                    episode.experiment_id,
                    json.dumps(episode.metadata, sort_keys=True, default=str),
                ),
            )

    def get_episode(self, decision_id: str) -> Optional[EvaluationEpisode]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_episodes WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            return None
        return EvaluationEpisode(
            decision_id=row["decision_id"], symbol=row["symbol"], action=row["action"],
            decision_at=row["decision_at"], data_as_of=row["data_as_of"],
            reference_price=row["reference_price"], benchmark_symbol=row["benchmark_symbol"],
            benchmark_price=row["benchmark_price"], confidence=row["confidence"],
            experiment_id=row["experiment_id"], metadata=json.loads(row["metadata_json"]),
        )

    def record_outcome(self, outcome: EvaluationOutcome) -> None:
        if self.get_episode(outcome.decision_id) is None:
            raise KeyError(f"Unknown evaluation episode {outcome.decision_id}")
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO evaluation_outcomes VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    outcome.decision_id, outcome.horizon, outcome.outcome_at.isoformat(),
                    outcome.asset_price, outcome.benchmark_price, outcome.asset_return_pct,
                    outcome.benchmark_return_pct, outcome.excess_return_pct,
                    int(outcome.directionally_correct), outcome.estimated_cost_pct,
                ),
            )

    def outcomes(self, *, experiment_id: Optional[str] = None) -> list[EvaluationOutcome]:
        query = """SELECT o.* FROM evaluation_outcomes o
                   JOIN evaluation_episodes e ON e.decision_id = o.decision_id"""
        params: tuple[str, ...] = ()
        if experiment_id:
            query += " WHERE e.experiment_id = ?"
            params = (experiment_id,)
        query += " ORDER BY o.outcome_at"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            EvaluationOutcome(
                decision_id=row["decision_id"], horizon=row["horizon"],
                outcome_at=row["outcome_at"], asset_price=row["asset_price"],
                benchmark_price=row["benchmark_price"], asset_return_pct=row["asset_return_pct"],
                benchmark_return_pct=row["benchmark_return_pct"],
                excess_return_pct=row["excess_return_pct"],
                directionally_correct=bool(row["directionally_correct"]),
                estimated_cost_pct=row["estimated_cost_pct"],
            ) for row in rows
        ]
