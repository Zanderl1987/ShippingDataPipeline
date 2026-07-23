from __future__ import annotations

import logging
from dataclasses import dataclass, field

import duckdb

from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)


def _get_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    """Execute a count query and return the result."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    result: int = row[0]
    return result


@dataclass
class ValidationResult:
    """Result of a validation check."""

    table: str
    check: str
    passed: bool
    failed_count: int
    total_count: int
    message: str


@dataclass
class ValidationReport:
    """Collection of validation results for a table."""

    table: str
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def total_checks(self) -> int:
        return len(self.results)

    @property
    def passed_checks(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_checks(self) -> int:
        return self.total_checks - self.passed_checks


def validate_not_null(
    table: str,
    column: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> ValidationResult:
    """Check that a column has no NULL values."""
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        total = _get_count(conn, f"SELECT count(*) FROM {table}")
        null_count = _get_count(conn, f"SELECT count(*) FROM {table} WHERE {column} IS NULL")

        passed = null_count == 0
        return ValidationResult(
            table=table,
            check=f"not_null({column})",
            passed=passed,
            failed_count=null_count,
            total_count=total,
            message=f"{null_count} null values in {column}"
            if not passed
            else f"All values in {column} are non-null",
        )
    finally:
        if should_close:
            conn.close()


def validate_range(
    table: str,
    column: str,
    min_value: float | None = None,
    max_value: float | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> ValidationResult:
    """Check that a column's values are within a specified range."""
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        total = _get_count(conn, f"SELECT count(*) FROM {table}")

        conditions = []
        if min_value is not None:
            conditions.append(f"{column} < {min_value}")
        if max_value is not None:
            conditions.append(f"{column} > {max_value}")

        if not conditions:
            return ValidationResult(
                table=table,
                check=f"range({column})",
                passed=True,
                failed_count=0,
                total_count=total,
                message="No range constraints specified",
            )

        where_clause = " OR ".join(conditions)
        failed_count = _get_count(conn, f"SELECT count(*) FROM {table} WHERE {where_clause}")

        passed = failed_count == 0
        range_desc = f"[{min_value}, {max_value}]"
        return ValidationResult(
            table=table,
            check=f"range({column}, {range_desc})",
            passed=passed,
            failed_count=failed_count,
            total_count=total,
            message=f"{failed_count} values out of range {range_desc} in {column}"
            if not passed
            else f"All values in {column} are within {range_desc}",
        )
    finally:
        if should_close:
            conn.close()


def validate_positive(
    table: str,
    column: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> ValidationResult:
    """Check that a column's values are all positive."""
    return validate_range(table=table, column=column, min_value=0, conn=conn)


def validate_ais_positions(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> ValidationReport:
    """Run all validation checks on ais_positions table."""
    report = ValidationReport(table="ais_positions")

    report.results.append(validate_not_null("ais_positions", "mmsi", conn))
    report.results.append(validate_not_null("ais_positions", "timestamp", conn))
    report.results.append(validate_range("ais_positions", "latitude", -90, 90, conn))
    report.results.append(validate_range("ais_positions", "longitude", -180, 180, conn))
    report.results.append(validate_range("ais_positions", "sog", 0, 100, conn))
    report.results.append(validate_range("ais_positions", "cog", 0, 360, conn))

    return report


def validate_vessels(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> ValidationReport:
    """Run all validation checks on vessels table."""
    report = ValidationReport(table="vessels")

    report.results.append(validate_not_null("vessels", "imo", conn))
    report.results.append(validate_positive("vessels", "imo", conn))

    return report


def validate_ports(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> ValidationReport:
    """Run all validation checks on ports table."""
    report = ValidationReport(table="ports")

    report.results.append(validate_not_null("ports", "unlocode", conn))
    report.results.append(validate_range("ports", "latitude", -90, 90, conn))
    report.results.append(validate_range("ports", "longitude", -180, 180, conn))

    return report


def run_all_validations(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> list[ValidationReport]:
    """Run validations on all tables and return reports."""
    reports = []
    should_close = False

    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        tables_with_data = [
            t[0]
            for t in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        ]

        if "ais_positions" in tables_with_data:
            reports.append(validate_ais_positions(conn))
        if "vessels" in tables_with_data:
            reports.append(validate_vessels(conn))
        if "ports" in tables_with_data:
            reports.append(validate_ports(conn))
    finally:
        if should_close:
            conn.close()

    return reports
