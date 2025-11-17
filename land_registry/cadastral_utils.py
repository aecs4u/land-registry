"""
Utility module for cadastral data loading and statistics.
Provides centralized loading, caching, and statistics computation.
"""

import json
import logging
import os
from typing import Optional, Dict, Any, Tuple
from functools import lru_cache
from pathlib import Path
import time

from land_registry.s3_storage import get_s3_storage
from land_registry.settings import get_cadastral_structure_path, get_cadastral_data_root, cadastral_settings

logger = logging.getLogger(__name__)


class CadastralData:
    """Container for cadastral data and statistics"""
    def __init__(self, data: Dict[str, Any], stats: Dict[str, int], source: str = "unknown"):
        self.data = data
        self.stats = stats
        self.loaded_at = time.time()
        self.source = source  # "local", "s3", or "json"

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

    def cache_age(self) -> float:
        """Return cache age in seconds"""
        return time.time() - self.loaded_at

    def cache_metadata(self) -> Dict[str, Any]:
        """Return cache metadata (age, source, timestamp)"""
        return {
            'loaded_at': self.loaded_at,
            'age_seconds': self.cache_age(),
            'source': self.source,
            'ttl_seconds': _cache_ttl_seconds,
            'is_expired': self.cache_age() >= _cache_ttl_seconds
        }

    def get_file_availability_stats(self) -> Dict[str, Any]:
        """
        Get statistics about file availability across the cadastral structure.
        Returns counts of municipalities with/without files.
        """
        municipalities_with_files = 0
        municipalities_without_files = 0

        for region in self.data.values():
            for province in region.values():
                for municipality in province.values():
                    if isinstance(municipality, dict):
                        files = municipality.get('files', [])
                        if files:
                            municipalities_with_files += 1
                        else:
                            municipalities_without_files += 1

        total_municipalities = municipalities_with_files + municipalities_without_files
        coverage_percentage = (municipalities_with_files / total_municipalities * 100) if total_municipalities > 0 else 0

        return {
            'municipalities_with_files': municipalities_with_files,
            'municipalities_without_files': municipalities_without_files,
            'total_municipalities': total_municipalities,
            'coverage_percentage': round(coverage_percentage, 2)
        }


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


def _scan_local_cadastral_directory(root_path: str) -> Dict[str, Any]:
    """
    Scan local cadastral directory and build the structure.
    Expected structure: root_path/REGION/PROVINCE/MUNICIPALITY_CODE/files.gpkg
    """
    cadastral_data = {}
    root = Path(root_path)

    if not root.exists():
        logger.warning(f"Local cadastral data path does not exist: {root_path}")
        return {}

    # Iterate through regions
    for region_dir in sorted(root.iterdir()):
        if not region_dir.is_dir():
            continue

        region_name = region_dir.name
        cadastral_data[region_name] = {}

        # Iterate through provinces
        for province_dir in sorted(region_dir.iterdir()):
            if not province_dir.is_dir():
                continue

            province_code = province_dir.name
            cadastral_data[region_name][province_code] = {}

            # Iterate through municipalities
            for municipality_dir in sorted(province_dir.iterdir()):
                if not municipality_dir.is_dir():
                    continue

                municipality_key = municipality_dir.name

                # Extract municipality code and name from folder name
                # Format: CODE_MUNICIPALITY_NAME (e.g., A018_ACCIANO)
                parts = municipality_key.split('_')
                if len(parts) >= 2:
                    # First part is usually the code (like "A018")
                    code = parts[0]
                    # Rest is the name
                    name = '_'.join(parts[1:])
                else:
                    code = municipality_key
                    name = municipality_key

                # List all .gpkg files in the municipality directory
                files = [f.name for f in sorted(municipality_dir.glob('*.gpkg'))]

                if files:  # Only add if there are files
                    cadastral_data[region_name][province_code][municipality_key] = {
                        'code': code,
                        'name': name.replace('_', ' '),
                        'files': files
                    }

    logger.info(f"Scanned local cadastral directory: {root_path}")
    return cadastral_data


def _load_cadastral_data_internal() -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Load cadastral data from local files or S3.
    Returns tuple of (data, source) where source is "local", "s3", or "json"
    """
    cadastral_data = None

    # Check if we should use local files (development mode)
    local_data_root = get_cadastral_data_root()
    if local_data_root and cadastral_settings.use_local_files:
        try:
            cadastral_data = _scan_local_cadastral_directory(local_data_root)
            if cadastral_data:
                logger.info(f"Loaded cadastral structure from local directory: {local_data_root}")
                return cadastral_data, "local"
        except Exception as e:
            logger.error(f"Failed to scan local cadastral directory: {e}", exc_info=True)

    # Try S3 (production mode or fallback)
    try:
        s3_storage = get_s3_storage()
        cadastral_data = s3_storage.get_cadastral_structure()
        if cadastral_data:
            logger.info("Loaded cadastral structure from S3")
            return cadastral_data, "s3"
    except Exception as s3_error:
        # Suppress credentials errors - expected in local development
        if "credentials" not in str(s3_error).lower():
            logger.warning(f"S3 access failed: {s3_error}")

    # Fallback to cadastral_structure.json file
    cadastral_path = get_cadastral_structure_path()
    if cadastral_path:
        try:
            with open(cadastral_path, 'r', encoding='utf-8') as f:
                cadastral_data = json.load(f)
            logger.info(f"Loaded cadastral structure from JSON file: {cadastral_path}")
            return cadastral_data, "json"
        except Exception as e:
            logger.error(f"Failed to load cadastral structure from {cadastral_path}: {e}")

    return None, "unknown"


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
        cadastral_data, source = _load_cadastral_data_internal()
        if not cadastral_data:
            logger.warning("Could not load cadastral structure data")
            return None

        # Calculate statistics
        stats = _calculate_statistics(cadastral_data)

        # Update cache
        _cadastral_cache = CadastralData(cadastral_data, stats, source)
        logger.info(f"Loaded cadastral data from {source}: {stats['total_regions']} regions, "
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
