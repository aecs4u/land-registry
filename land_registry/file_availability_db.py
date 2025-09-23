"""
SQLite database for storing file availability status codes.
Tracks HTTP status codes for S3 file checks to avoid repeated requests.
"""

import sqlite3
import os
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class FileAvailabilityDB:
    """Manages SQLite database for file availability status codes."""

    def __init__(self, db_path: str = "file_availability.db"):
        """Initialize database connection and create tables if needed."""
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Create database tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE NOT NULL,
                    status_code INTEGER NOT NULL,
                    last_checked TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_file_path ON file_status(file_path)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_last_checked ON file_status(last_checked)
            """)

            # Create auction properties table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auction_properties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id TEXT UNIQUE NOT NULL,
                    cadastral_code TEXT NOT NULL,
                    region TEXT NOT NULL,
                    province TEXT NOT NULL,
                    municipality TEXT NOT NULL,
                    property_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    auction_date TEXT NOT NULL,
                    starting_price REAL NOT NULL,
                    final_price REAL,
                    description TEXT,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_cadastral_code ON auction_properties(cadastral_code)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON auction_properties(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_property_type ON auction_properties(property_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_auction_date ON auction_properties(auction_date)
            """)

            conn.commit()
            logger.info(f"Database initialized with file_status and auction_properties tables at {self.db_path}")

    def get_file_status(self, file_path: str, max_age_hours: int = 24) -> Optional[int]:
        """
        Get cached status code for a file if it's recent enough.

        Args:
            file_path: S3 key or file path
            max_age_hours: Maximum age of cached status in hours

        Returns:
            Status code if cached and recent, None otherwise
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status_code FROM file_status
                WHERE file_path = ? AND last_checked > ?
            """, (file_path, cutoff_time))

            result = cursor.fetchone()
            if result:
                logger.debug(f"Cache hit for {file_path}: status {result[0]}")
                return result[0]
            else:
                logger.debug(f"Cache miss for {file_path}")
                return None

    def set_file_status(self, file_path: str, status_code: int):
        """
        Store or update status code for a file.

        Args:
            file_path: S3 key or file path
            status_code: HTTP status code from availability check
        """
        now = datetime.now()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO file_status (file_path, status_code, last_checked)
                VALUES (?, ?, ?)
            """, (file_path, status_code, now))
            conn.commit()
            logger.debug(f"Cached status {status_code} for {file_path}")

    def get_file_status_batch(self, file_paths: List[str], max_age_hours: int = 24) -> Dict[str, int]:
        """
        Get cached status codes for multiple files.

        Args:
            file_paths: List of S3 keys or file paths
            max_age_hours: Maximum age of cached status in hours

        Returns:
            Dictionary mapping file paths to status codes
        """
        if not file_paths:
            return {}

        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        placeholders = ",".join("?" * len(file_paths))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT file_path, status_code FROM file_status
                WHERE file_path IN ({placeholders}) AND last_checked > ?
            """, file_paths + [cutoff_time])

            results = dict(cursor.fetchall())
            logger.debug(f"Batch cache lookup: {len(results)}/{len(file_paths)} hits")
            return results

    def set_file_status_batch(self, file_statuses: Dict[str, int]):
        """
        Store or update status codes for multiple files.

        Args:
            file_statuses: Dictionary mapping file paths to status codes
        """
        if not file_statuses:
            return

        now = datetime.now()
        data = [(path, status, now) for path, status in file_statuses.items()]

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany("""
                INSERT OR REPLACE INTO file_status (file_path, status_code, last_checked)
                VALUES (?, ?, ?)
            """, data)
            conn.commit()
            logger.debug(f"Batch cached {len(file_statuses)} file statuses")

    def clear_cache(self):
        """Clear all cached file statuses."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM file_status")
            conn.commit()
            logger.info("File availability cache cleared")

    def cleanup_old_entries(self, max_age_days: int = 7):
        """Remove old cache entries to keep database size manageable."""
        cutoff_time = datetime.now() - timedelta(days=max_age_days)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM file_status WHERE last_checked < ?", (cutoff_time,))
            deleted_count = cursor.rowcount
            conn.commit()
            logger.info(f"Cleaned up {deleted_count} old cache entries")

    def get_stats(self) -> Dict[str, int]:
        """Get database statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Total entries
            cursor.execute("SELECT COUNT(*) FROM file_status")
            total_entries = cursor.fetchone()[0]

            # Available files (status 200)
            cursor.execute("SELECT COUNT(*) FROM file_status WHERE status_code = 200")
            available_files = cursor.fetchone()[0]

            # Missing files (status 404)
            cursor.execute("SELECT COUNT(*) FROM file_status WHERE status_code = 404")
            missing_files = cursor.fetchone()[0]

            # Error status files (other codes)
            cursor.execute("SELECT COUNT(*) FROM file_status WHERE status_code NOT IN (200, 404)")
            error_files = cursor.fetchone()[0]

            return {
                "total_entries": total_entries,
                "available_files": available_files,
                "missing_files": missing_files,
                "error_files": error_files
            }

    # ============================================================================
    # Auction Properties Methods
    # ============================================================================

    def insert_auction_property(self, property_data: Dict) -> bool:
        """Insert a new auction property into the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO auction_properties (
                        property_id, cadastral_code, region, province, municipality,
                        property_type, status, auction_date, starting_price, final_price,
                        description, latitude, longitude, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    property_data['property_id'],
                    property_data['cadastral_code'],
                    property_data['region'],
                    property_data['province'],
                    property_data['municipality'],
                    property_data['property_type'],
                    property_data['status'],
                    property_data['auction_date'],
                    property_data['starting_price'],
                    property_data.get('final_price'),
                    property_data.get('description'),
                    property_data['latitude'],
                    property_data['longitude']
                ))
                conn.commit()
                logger.info(f"Inserted auction property: {property_data['property_id']}")
                return True
        except sqlite3.Error as e:
            logger.error(f"Error inserting auction property: {e}")
            return False

    def get_auction_properties(self, filters: Dict = None) -> List[Dict]:
        """Get auction properties with optional filters."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                query = "SELECT * FROM auction_properties"
                params = []

                if filters:
                    conditions = []
                    if 'status' in filters:
                        conditions.append("status = ?")
                        params.append(filters['status'])
                    if 'property_type' in filters:
                        conditions.append("property_type = ?")
                        params.append(filters['property_type'])
                    if 'cadastral_code' in filters:
                        conditions.append("cadastral_code = ?")
                        params.append(filters['cadastral_code'])
                    if 'max_price' in filters:
                        conditions.append("starting_price <= ?")
                        params.append(filters['max_price'])

                    if conditions:
                        query += " WHERE " + " AND ".join(conditions)

                query += " ORDER BY auction_date DESC"

                cursor.execute(query, params)
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()

                return [dict(zip(columns, row)) for row in rows]

        except sqlite3.Error as e:
            logger.error(f"Error getting auction properties: {e}")
            return []

    def populate_dummy_auction_data(self):
        """Populate database with dummy auction properties for testing."""
        dummy_data = [
            {
                'property_id': 'A018_AUC_001',
                'cadastral_code': 'A018',
                'region': 'ABRUZZO',
                'province': 'AQ',
                'municipality': 'ACCIANO',
                'property_type': 'residential',
                'status': 'active',
                'auction_date': '2024-03-15',
                'starting_price': 125000.0,
                'final_price': None,
                'description': 'Casa indipendente con giardino - Centro storico Acciano',
                'latitude': 42.2025,
                'longitude': 13.6625
            },
            {
                'property_id': 'A018_AUC_002',
                'cadastral_code': 'A018',
                'region': 'ABRUZZO',
                'province': 'AQ',
                'municipality': 'ACCIANO',
                'property_type': 'agricultural',
                'status': 'active',
                'auction_date': '2024-04-20',
                'starting_price': 85000.0,
                'final_price': None,
                'description': 'Terreno agricolo irriguo - Periferia Acciano',
                'latitude': 42.2045,
                'longitude': 13.6645
            },
            {
                'property_id': 'A018_AUC_003',
                'cadastral_code': 'A018',
                'region': 'ABRUZZO',
                'province': 'AQ',
                'municipality': 'ACCIANO',
                'property_type': 'commercial',
                'status': 'sold',
                'auction_date': '2024-02-10',
                'starting_price': 200000.0,
                'final_price': 245000.0,
                'description': 'Immobile commerciale con magazzino - Via principale',
                'latitude': 42.2015,
                'longitude': 13.6605
            },
            {
                'property_id': 'A018_AUC_004',
                'cadastral_code': 'A018',
                'region': 'ABRUZZO',
                'province': 'AQ',
                'municipality': 'ACCIANO',
                'property_type': 'residential',
                'status': 'sold',
                'auction_date': '2024-01-25',
                'starting_price': 95000.0,
                'final_price': 110000.0,
                'description': 'Appartamento ristrutturato - Secondo piano',
                'latitude': 42.2035,
                'longitude': 13.6615
            },
            {
                'property_id': 'A018_AUC_005',
                'cadastral_code': 'A018',
                'region': 'ABRUZZO',
                'province': 'AQ',
                'municipality': 'ACCIANO',
                'property_type': 'industrial',
                'status': 'cancelled',
                'auction_date': '2024-02-28',
                'starting_price': 350000.0,
                'final_price': None,
                'description': 'Capannone industriale con area di carico',
                'latitude': 42.2055,
                'longitude': 13.6635
            },
            {
                'property_id': 'A018_AUC_006',
                'cadastral_code': 'A018',
                'region': 'ABRUZZO',
                'province': 'AQ',
                'municipality': 'ACCIANO',
                'property_type': 'residential',
                'status': 'active',
                'auction_date': '2024-05-10',
                'starting_price': 165000.0,
                'final_price': None,
                'description': 'Villa bifamiliare con piscina - Zona residenziale',
                'latitude': 42.2065,
                'longitude': 13.6655
            }
        ]

        inserted_count = 0
        for property_data in dummy_data:
            if self.insert_auction_property(property_data):
                inserted_count += 1

        logger.info(f"Populated database with {inserted_count} dummy auction properties")
        return inserted_count

    def get_auction_statistics(self) -> Dict:
        """Get statistics about auction properties in the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Total properties
                cursor.execute("SELECT COUNT(*) FROM auction_properties")
                total_properties = cursor.fetchone()[0]

                # By status
                cursor.execute("SELECT status, COUNT(*) FROM auction_properties GROUP BY status")
                status_counts = dict(cursor.fetchall())

                # By property type
                cursor.execute("SELECT property_type, COUNT(*) FROM auction_properties GROUP BY property_type")
                type_counts = dict(cursor.fetchall())

                # Average prices
                cursor.execute("SELECT AVG(starting_price) FROM auction_properties WHERE status = 'active'")
                avg_starting_price = cursor.fetchone()[0]

                cursor.execute("SELECT AVG(final_price) FROM auction_properties WHERE final_price IS NOT NULL")
                avg_final_price = cursor.fetchone()[0]

                return {
                    "total_properties": total_properties,
                    "status_counts": status_counts,
                    "type_counts": type_counts,
                    "avg_starting_price": avg_starting_price,
                    "avg_final_price": avg_final_price
                }

        except sqlite3.Error as e:
            logger.error(f"Error getting auction statistics: {e}")
            return {}


# Global instance
file_availability_db = FileAvailabilityDB()