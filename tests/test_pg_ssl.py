"""Loopback psycopg2 connections skip TLS; remote ones keep their settings."""

import pytest

from land_registry.pg_ssl import is_loopback_host, plain_loopback_dsn


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "/var/run/postgresql"])
def test_loopback_hosts_are_detected(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", [None, "", "db.example.com", "10.0.0.5"])
def test_other_hosts_are_not_loopback(host):
    assert not is_loopback_host(host)


def test_loopback_url_dsn_gets_sslmode_disable():
    dsn = plain_loopback_dsn("postgresql://postgres@127.0.0.1:5432/hazards?passfile=/x/.pgpass")
    assert "sslmode=disable" in dsn and "host=127.0.0.1" in dsn and "dbname=hazards" in dsn


def test_remote_dsn_is_left_untouched():
    dsn = "postgresql://u:p@db.example.com:5432/x"
    assert plain_loopback_dsn(dsn) == dsn


def test_explicit_sslmode_is_respected():
    dsn = "postgresql://u@127.0.0.1/x?sslmode=require"
    assert plain_loopback_dsn(dsn) == dsn


def test_unparseable_dsn_is_returned_unchanged():
    assert plain_loopback_dsn("not a dsn ::") == "not a dsn ::"
