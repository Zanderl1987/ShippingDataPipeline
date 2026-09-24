"""Curation module for deduplication, validation, and enrichment."""

from src.curation.dedup import (
    deduplicate_ais_positions,
    deduplicate_ports,
    deduplicate_table,
    deduplicate_vessels,
)
from src.curation.enrichment import (
    create_curated_ais_positions,
    create_curated_vessels,
    create_port_congestion_proxy,
    enrich_ais_with_port_info,
    enrich_ais_with_vessel_info,
)
from src.curation.pipeline import CurationResult, print_curation_report, run_curation
from src.curation.validation import (
    ValidationReport,
    ValidationResult,
    validate_ais_positions,
    validate_not_null,
    validate_ports,
    validate_positive,
    validate_range,
    validate_vessels,
)

__all__ = [
    "CurationResult",
    "ValidationReport",
    "ValidationResult",
    "create_curated_ais_positions",
    "create_curated_vessels",
    "create_port_congestion_proxy",
    "deduplicate_ais_positions",
    "deduplicate_ports",
    "deduplicate_table",
    "deduplicate_vessels",
    "enrich_ais_with_port_info",
    "enrich_ais_with_vessel_info",
    "print_curation_report",
    "run_curation",
    "validate_ais_positions",
    "validate_not_null",
    "validate_ports",
    "validate_positive",
    "validate_range",
    "validate_vessels",
]
