import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, insert, inspect, text

from vitadrive.storage import (
    Store,
    _alembic_config,
    _create_engine,
    current_db_revision,
    database_url,
    metadata,
    rate_limits_t,
    readings_daily_t,
    upgrade_db,
    vehicles_t,
)

PG_URL = os.environ.get("VITADRIVE_TEST_POSTGRES")


def _assert_matches_models(engine):
    with engine.connect() as connection:
        diffs = compare_metadata(MigrationContext.configure(connection), metadata)
    assert diffs == []


def _legacy_vehicle(engine):
    with engine.begin() as connection:
        connection.execute(insert(vehicles_t).values(
            vin="LEGACY00000000001",
            owner_id="legacy-owner",
            data='{"vin":"LEGACY00000000001"}',
        ))


def test_fresh_sqlite_database_matches_models(tmp_path):
    store = Store(tmp_path / "fresh.db")
    try:
        assert current_db_revision(store.engine) == "0003"
        _assert_matches_models(store.engine)
    finally:
        store.close()


def test_legacy_sqlite_database_is_stamped_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(database_url(path))
    metadata.create_all(engine)
    readings_daily_t.drop(engine)
    rate_limits_t.drop(engine)
    _legacy_vehicle(engine)
    engine.dispose()

    store = Store(path)
    try:
        assert current_db_revision(store.engine) == "0003"
        with store.engine.connect() as connection:
            assert connection.execute(
                text("SELECT owner_id FROM vehicles WHERE vin = 'LEGACY00000000001'")
            ).scalar_one() == "legacy-owner"
        _assert_matches_models(store.engine)
    finally:
        store.close()


def test_opening_store_twice_is_idempotent(tmp_path):
    path = tmp_path / "repeat.db"
    first = Store(path)
    first.close()
    second = Store(path)
    try:
        assert current_db_revision(second.engine) == "0003"
        _assert_matches_models(second.engine)
    finally:
        second.close()


def test_partial_legacy_database_fails_clearly(tmp_path):
    engine = create_engine(database_url(tmp_path / "partial.db"))
    vehicles_t.create(engine)
    engine.dispose()

    with pytest.raises(RuntimeError, match="partial legacy database"):
        Store(tmp_path / "partial.db")


def test_auto_migrate_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VITADRIVE_AUTO_MIGRATE", "0")
    store = Store(tmp_path / "manual.db")
    try:
        assert inspect(store.engine).get_table_names() == []
    finally:
        store.close()


def test_cli_db_current_upgrade_and_stamp(tmp_path, capsys):
    from vitadrive.cli import main

    path = tmp_path / "cli.db"
    main(["--db", str(path), "db", "current"])
    assert capsys.readouterr().out.strip() == "None"
    main(["--db", str(path), "db", "upgrade"])
    main(["--db", str(path), "db", "current"])
    assert capsys.readouterr().out.strip() == "0003"

    stamped_path = tmp_path / "stamped.db"
    stamped_engine = create_engine(database_url(stamped_path))
    metadata.create_all(stamped_engine)
    stamped_engine.dispose()
    main(["--db", str(stamped_path), "db", "stamp", "0001"])
    main(["--db", str(stamped_path), "db", "current"])
    assert capsys.readouterr().out.strip() == "0001"


def test_sqlite_upgrade_downgrade_and_upgrade_again(tmp_path):
    store = Store(tmp_path / "cycle.db")
    try:
        with store.engine.begin() as connection:
            command.downgrade(_alembic_config(connection), "base")
        assert current_db_revision(store.engine) is None
        upgrade_db(store.engine)
        assert current_db_revision(store.engine) == "0003"
        _assert_matches_models(store.engine)
    finally:
        store.close()


def _drop_postgres_app_schema(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    metadata.drop_all(engine)


@pytest.fixture
def postgres_engine():
    if not PG_URL:
        pytest.skip("set VITADRIVE_TEST_POSTGRES to run against Postgres")
    engine = _create_engine(PG_URL)
    _drop_postgres_app_schema(engine)
    try:
        yield engine
    finally:
        try:
            upgrade_db(engine)
        finally:
            engine.dispose()


def test_fresh_postgres_database_matches_models(postgres_engine):
    store = Store(PG_URL)
    try:
        assert current_db_revision(store.engine) == "0003"
        _assert_matches_models(store.engine)
    finally:
        store.close()


def test_legacy_postgres_database_is_stamped_without_data_loss(postgres_engine):
    metadata.create_all(postgres_engine)
    readings_daily_t.drop(postgres_engine)
    rate_limits_t.drop(postgres_engine)
    _legacy_vehicle(postgres_engine)

    store = Store(PG_URL)
    try:
        assert current_db_revision(store.engine) == "0003"
        with store.engine.connect() as connection:
            assert connection.execute(
                text("SELECT owner_id FROM vehicles WHERE vin = 'LEGACY00000000001'")
            ).scalar_one() == "legacy-owner"
        _assert_matches_models(store.engine)
    finally:
        store.close()
