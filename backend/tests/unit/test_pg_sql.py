"""Unit test penerjemah placeholder SQLite ke asyncpg."""

from studio.store.pg import _command_rowcount, is_postgres_url, to_pg_sql


def test_placeholders_numbered_in_order() -> None:
    assert to_pg_sql("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = $1 AND b = $2"
    )


def test_question_mark_inside_literal_untouched() -> None:
    sql = "SELECT 'apa?', 'it''s?' FROM t WHERE x = ?"
    assert to_pg_sql(sql) == "SELECT 'apa?', 'it''s?' FROM t WHERE x = $1"


def test_rowcount_and_url() -> None:
    assert _command_rowcount("UPDATE 3") == 3
    assert _command_rowcount("INSERT 0 1") == 1
    assert is_postgres_url("postgresql+asyncpg://u@h/db")
    assert not is_postgres_url(None) and not is_postgres_url("sqlite:///x.db")
