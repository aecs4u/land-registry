"""
Utility module for cadastral data loading and statistics.
Provides centralized loading, caching, and statistics computation.
"""

import json
import logging
from typing import Optional, Dict, Any, Tuple
from functools import lru_cache
import time

from land_registry.s3_storage import get_s3_storage
from land_registry.settings import get_cadastral_structure_path

logger = logging.getLogger(__name__)


class CadastralData:
    """Container for cadastral data and statistics"""
    def __init__(self, data: Dict[str, Any], stats: Dict[str, int]):
        self.data = data
        self.stats = stats
        self.loaded_at = time.time()

    @property
    def total_regions(self) -> int:
        return self.stats.get('total_regions', 0)

    @property
    def total_provinces(self) -> int:
        return self.stats.get('total_provinces', 0)

    @property
    def total_municipalities(self) -> int:
        return self.stats.get('total_municipalities', 0)

    @property
    def total_files(self) -> int:
        return self.stats.get('total_files', 0)


# Global cache for cadastral data
_cadastral_cache: Optional[CadastralData] = None
_cache_ttl_seconds = 300  # 5 minutes


def _calculate_statistics(cadastral_data: Dict[str, Any]) -> Dict[str, int]:
    """Calculate statistics from cadastral data structure"""
    if not cadastral_data or not isinstance(cadastral_data, dict):
        return {
            'total_regions': 0,
            'total_provinces': 0,
            'total_municipalities': 0,
            'total_files': 0
        }

    total_regions = len(cadastral_data)
    total_provinces = sum(len(region) for region in cadastral_data.values())
    total_municipalities = sum(
        len(province)
        for region in cadastral_data.values()
        for province in region.values()
    )

    # Count files correctly
    total_files = sum(
        len(municipality.get('files', []))
        for region in cadastral_data.values()
        for province in region.values()
        for municipality in province.values()
        if isinstance(municipality, dict)
    )

    return {
        'total_regions': total_regions,
        'total_provinces': total_provinces,
        'total_municipalities': total_municipalities,
        'total_files': total_files
    }


def _load_cadastral_data_internal() -> Optional[Dict[str, Any]]:
    """Load cadastral data from S3 or local file"""
    cadastral_data = None

    # Try S3 first (but handle credentials gracefully)
    try:
        s3_storage = get_s3_storage()
        cadastral_data = s3_storage.get_cadastral_structure()
        if cadastral_data:
            logger.info("Loaded cadastral structure from S3")
            return cadastral_data
    except Exception as s3_error:
        # Suppress credentials errors - expected in local development
        if "credentials" not in str(s3_error).lower():
            logger.warning(f"S3 access failed: {s3_error}")

    # Fallback to local file
    cadastral_path = get_cadastral_structure_path()
    if cadastral_path:
        try:
            with open(cadastral_path, 'r', encoding='utf-8') as f:
                cadastral_data = json.load(f)
            logger.info(f"Loaded cadastral structure from local file: {cadastral_path}")
            return cadastral_data
        except Exception as e:
            logger.error(f"Failed to load cadastral structure from {cadastral_path}: {e}")

    return None


def load_cadastral_structure(use_cache: bool = True) -> Optional[CadastralData]:
    """
    Load cadastral structure data with statistics.

    Args:
        use_cache: If True, use cached data if available and not expired

    Returns:
        CadastralData object or None if loading fails
    """
    global _cadastral_cache

    # Check cache
    if use_cache and _cadastral_cache:
        age = time.time() - _cadastral_cache.loaded_at
        if age < _cache_ttl_seconds:
            logger.debug(f"Using cached cadastral data (age: {age:.1f}s)")
            return _cadastral_cache

    # Load fresh data
    try:
        cadastral_data = _load_cadastral_data_internal()
        if not cadastral_data:
            logger.warning("Could not load cadastral structure data")
            return None

        # Calculate statistics
        stats = _calculate_statistics(cadastral_data)

        # Update cache
        _cadastral_cache = CadastralData(cadastral_data, stats)
        logger.info(f"Loaded cadastral data: {stats['total_regions']} regions, "
                   f"{stats['total_provinces']} provinces, "
                   f"{stats['total_municipalities']} municipalities, "
                   f"{stats['total_files']} files")

        return _cadastral_cache

    except Exception as e:
        logger.error(f"Error loading cadastral structure: {e}", exc_info=True)
        return None


def clear_cache():
    """Clear the cadastral data cache"""
    global _cadastral_cache
    _cadastral_cache = None
    logger.info("Cadastral data cache cleared")


def get_cadastral_stats() -> Dict[str, int]:
    """
    Get cadastral statistics (regions, provinces, municipalities, files).
    Returns zeros if data cannot be loaded.
    """
    cadastral = load_cadastral_structure()
    if cadastral:
        return cadastral.stats
    return {
        'total_regions': 0,
        'total_provinces': 0,
        'total_municipalities': 0,
        'total_files': 0
    }
