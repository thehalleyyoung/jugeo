"""Persistent SQLite knowledge base mapping Wikipedia articles → DefaultRules.

Stores articles and their extracted DefaultRule facts so that domain knowledge
accumulates across sessions. Retrieved rules are merged with static DEFAULT_RULES
before reasoning, giving the DefaultReasoner access to learned world knowledge.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from gofai_chat.core.grade import Grade


_DB_PATH = Path.home() / ".gofai_chat" / "knowledge.db"


def _get_conn(db_path: Path = _DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            topic_key TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            tone TEXT NOT NULL DEFAULT 'neutral',
            imagery_words TEXT NOT NULL DEFAULT '[]',
            frame_names TEXT NOT NULL DEFAULT '[]',
            fetched_at TEXT NOT NULL DEFAULT '',
            UNIQUE(topic_key)
        );
        CREATE TABLE IF NOT EXISTS default_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER REFERENCES articles(id),
            name TEXT NOT NULL UNIQUE,
            condition TEXT NOT NULL,
            conclusion TEXT NOT NULL,
            strength REAL NOT NULL DEFAULT 0.7,
            priority INTEGER NOT NULL DEFAULT 50,
            exceptions TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_rules_condition ON default_rules(condition);
        CREATE TABLE IF NOT EXISTS typicality (
            entity_type TEXT NOT NULL,
            property TEXT NOT NULL,
            grade REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'wiki',
            PRIMARY KEY (entity_type, property)
        );
    """)
    conn.commit()


class KnowledgeDB:
    """SQLite-backed store for Wikipedia-sourced DefaultRules and typicality grades."""

    def __init__(self, db_path: Path = _DB_PATH):
        self._path = db_path
        with _get_conn(db_path) as conn:
            _init_db(conn)

    def store_article(self, article) -> int:
        """Store WikiArticle metadata, return article_id."""
        with _get_conn(self._path) as conn:
            conn.execute("""
                INSERT INTO articles
                    (title, topic_key, url, summary, tone, imagery_words, frame_names, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_key) DO UPDATE SET
                    summary=excluded.summary,
                    tone=excluded.tone,
                    imagery_words=excluded.imagery_words,
                    frame_names=excluded.frame_names,
                    fetched_at=excluded.fetched_at
            """, (
                article.title,
                article.title.lower().strip(),
                getattr(article, 'url', ''),
                getattr(article, 'summary', '')[:2000],
                getattr(article, 'tone', 'neutral'),
                json.dumps(getattr(article, 'imagery_words', [])),
                json.dumps(getattr(article, 'frame_names', [])),
                getattr(article, 'fetched_at', ''),
            ))
            conn.commit()
            row = conn.execute(
                "SELECT id FROM articles WHERE topic_key=?",
                (article.title.lower().strip(),)
            ).fetchone()
            return row[0] if row else -1

    def store_rules(self, article_id: int, rules: list) -> None:
        """Persist a list of DefaultRule objects linked to an article."""
        with _get_conn(self._path) as conn:
            for rule in rules:
                strength = rule.strength.to_prob() if hasattr(rule.strength, 'to_prob') else float(rule.strength)
                conn.execute("""
                    INSERT INTO default_rules
                        (article_id, name, condition, conclusion, strength,
                         priority, exceptions, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        strength=excluded.strength,
                        priority=excluded.priority
                """, (
                    article_id,
                    rule.name,
                    rule.condition.lower(),
                    rule.conclusion,
                    strength,
                    rule.priority,
                    json.dumps(rule.exceptions),
                    rule.description,
                ))
            conn.commit()

    def store_typicality(self, entity_type: str, property: str, grade: float, source: str = 'wiki') -> None:
        with _get_conn(self._path) as conn:
            conn.execute("""
                INSERT INTO typicality (entity_type, property, grade, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(entity_type, property) DO UPDATE SET grade=MAX(grade, excluded.grade)
            """, (entity_type.lower(), property.lower(), grade, source))
            conn.commit()

    def retrieve_rules(self, entity_type: str, fuzzy: bool = True) -> list:
        """Return DefaultRule objects for an entity type (exact + fuzzy match)."""
        from gofai_chat.inference.defaults import DefaultRule

        et = entity_type.lower().strip()
        with _get_conn(self._path) as conn:
            if fuzzy:
                rows = conn.execute("""
                    SELECT name, condition, conclusion, strength, priority, exceptions, description
                    FROM default_rules
                    WHERE lower(condition) = ?
                       OR lower(condition) LIKE ?
                       OR ? LIKE '%' || lower(condition) || '%'
                    ORDER BY strength DESC LIMIT 50
                """, (et, f"%{et}%", et)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT name, condition, conclusion, strength, priority, exceptions, description
                    FROM default_rules WHERE lower(condition) = ?
                    ORDER BY strength DESC LIMIT 50
                """, (et,)).fetchall()

        rules = []
        for name, condition, conclusion, strength, priority, exceptions, description in rows:
            try:
                exc = json.loads(exceptions) if exceptions else []
            except Exception:
                exc = []
            rules.append(DefaultRule(
                name=name,
                condition=condition,
                conclusion=conclusion,
                strength=Grade.from_prob(float(strength)),
                priority=int(priority),
                exceptions=exc,
                description=description or '',
            ))
        return rules

    def retrieve_imagery(self, entity_type: str) -> list:
        """Return imagery words for a topic from stored articles."""
        et = entity_type.lower().strip()
        with _get_conn(self._path) as conn:
            rows = conn.execute("""
                SELECT imagery_words FROM articles
                WHERE lower(topic_key) LIKE ? OR lower(title) LIKE ?
                ORDER BY rowid DESC LIMIT 5
            """, (f"%{et}%", f"%{et}%")).fetchall()
        words: list = []
        for (iw_json,) in rows:
            try:
                words.extend(json.loads(iw_json))
            except Exception:
                pass
        return list(dict.fromkeys(words))[:30]

    def has_article(self, topic: str) -> bool:
        et = topic.lower().strip()
        with _get_conn(self._path) as conn:
            row = conn.execute(
                "SELECT id FROM articles WHERE topic_key=? OR lower(topic_key) LIKE ?",
                (et, f"%{et}%")
            ).fetchone()
        return row is not None

    def list_topics(self) -> list:
        with _get_conn(self._path) as conn:
            rows = conn.execute("SELECT title FROM articles ORDER BY rowid DESC").fetchall()
        return [r[0] for r in rows]
