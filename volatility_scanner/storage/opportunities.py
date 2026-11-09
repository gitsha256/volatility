from __future__ import annotations

import sqlite3
from pathlib import Path

from volatility_scanner.strategies.scanner import OpportunityScore


def initialize_database(db_path: str | Path = "scanner.db") -> sqlite3.Connection:
    path = Path(db_path)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            futures_price REAL NOT NULL,
            strike REAL NOT NULL,
            iv REAL NOT NULL,
            forecast_rv REAL NOT NULL,
            volatility_spread REAL NOT NULL,
            variance_ratio REAL NOT NULL,
            implied_move REAL NOT NULL,
            forecast_move REAL NOT NULL,
            gamma_theta REAL NOT NULL,
            expected_pnl REAL NOT NULL,
            confidence REAL NOT NULL,
            direction_filter TEXT NOT NULL,
            qualifies INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_opportunity_score(conn: sqlite3.Connection, score: OpportunityScore) -> None:
    conn.execute(
        """
        INSERT INTO opportunities (
            strategy,
            futures_price,
            strike,
            iv,
            forecast_rv,
            volatility_spread,
            variance_ratio,
            implied_move,
            forecast_move,
            gamma_theta,
            expected_pnl,
            confidence,
            direction_filter,
            qualifies
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            score.strategy,
            score.futures_price,
            score.strike,
            score.iv,
            score.forecast_rv,
            score.volatility_spread,
            score.variance_ratio,
            score.implied_move,
            score.forecast_move,
            score.gamma_theta,
            score.expected_pnl,
            score.confidence,
            score.direction_filter,
            int(score.qualifies),
        ),
    )
    conn.commit()
