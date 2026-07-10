#!/usr/bin/env python3
"""
OMI Data Import Script

Imports real estate market data from Agenzia delle Entrate (OMI - Osservatorio del Mercato Immobiliare)
ZIP archives into a normalized SQLite database.

Data sources:
- QI files: Quotazioni Immobiliari (market quotes) with VALORI and ZONE tables
- VCN files: Volumi Compravendite Nazionali (national sales volumes) with multiple tables

Usage:
    python scripts/omi_import.py [--data-dir PATH] [--output PATH]
"""

import argparse
import re
import sqlite3
import zipfile
from glob import glob
from io import StringIO
from pathlib import Path

import pandas as pd


def extract_semester_from_filename(name: str) -> tuple[int, int] | None:
    """Extract year and semester from QI filename like QI_1308407_1_20041_VALORI.csv"""
    match = re.search(r'_(\d{4})(\d)_', name)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def extract_year_from_filename(name: str) -> int | None:
    """Extract year from VCN filename like VCN_1308410_1_2024_LISTA-COM.csv"""
    match = re.search(r'_(\d{4})_[A-Z]', name)
    return int(match.group(1)) if match else None


def normalize_vcn_columns(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Normalize year-specific column names and add a year column"""
    new_cols = {}
    for col in df.columns:
        new_col = re.sub(r'(\d{4})_', 'YEAR_', col)
        new_col = re.sub(r'_(\d{4})_', '_YEAR_', new_col)
        new_col = re.sub(r'_(\d{4})$', '_YEAR', new_col)
        new_col = re.sub(r' (\d{4}) ', ' YEAR ', new_col)
        new_col = new_col.upper()
        new_cols[col] = new_col
    df = df.rename(columns=new_cols)
    df['ANNO'] = year
    return df


def process_qi_files(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Process QI (Quotazioni Immobiliari) ZIP files"""
    qi_files = glob(str(data_dir / 'QI*_*.zip'))
    print(f"Found {len(qi_files)} QI archive files")

    valori_dfs = []
    zone_dfs = []

    for zip_path in qi_files:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                semester_info = extract_semester_from_filename(name)
                if semester_info is None:
                    continue
                year, semester = semester_info

                with zf.open(name) as f:
                    content = f.read().decode('utf-8')
                    lines = content.split('\n')
                    csv_content = '\n'.join(lines[1:])  # Skip header comment

                    df = pd.read_csv(StringIO(csv_content), sep=';', low_memory=False)
                    df['Anno'] = year
                    df['Semestre'] = semester

                    if '_VALORI.csv' in name:
                        valori_dfs.append(df)
                    elif '_ZONE.csv' in name:
                        zone_dfs.append(df)

    valori_df = pd.concat(valori_dfs, ignore_index=True) if valori_dfs else pd.DataFrame()
    zone_df = pd.concat(zone_dfs, ignore_index=True) if zone_dfs else pd.DataFrame()

    print(f"Processed {len(valori_dfs)} VALORI files ({len(valori_df)} rows)")
    print(f"Processed {len(zone_dfs)} ZONE files ({len(zone_df)} rows)")

    return valori_df, zone_df


def process_vcn_files(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Process VCN (Volumi Compravendite Nazionali) ZIP files"""
    vcn_files = glob(str(data_dir / 'VCN*_*.zip'))
    print(f"Found {len(vcn_files)} VCN archive files")

    tables = {
        'lista_com': [],
        'valori_com': [],
        'valori_per': [],
        'valori_res': []
    }

    for zip_path in vcn_files:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                year = extract_year_from_filename(name)
                if year is None:
                    continue

                with zf.open(name) as f:
                    content = f.read().decode('utf-8')
                    df = pd.read_csv(StringIO(content), sep=';', low_memory=False)
                    df = normalize_vcn_columns(df, year)

                    if '_LISTA-COM.csv' in name:
                        tables['lista_com'].append(df)
                    elif '_VALORI-COM.csv' in name:
                        tables['valori_com'].append(df)
                    elif '_VALORI-PER.csv' in name:
                        tables['valori_per'].append(df)
                    elif '_VALORI-RES.csv' in name:
                        tables['valori_res'].append(df)

    result = {}
    for key, dfs in tables.items():
        if dfs:
            combined = pd.concat(dfs, ignore_index=True)
            # Remove unnamed columns
            combined = combined.loc[:, ~combined.columns.str.contains('^UNNAMED', case=False)]
            combined = combined.drop_duplicates()
            result[key] = combined
            print(f"Processed VCN {key}: {len(combined)} rows, {len(combined.columns)} columns")

    return result


def normalize_qi_valori(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize QI VALORI table columns"""
    # Remove unnamed columns
    df = df.loc[:, ~df.columns.str.contains('^Unnamed', case=False)]

    column_mapping = {
        'Area_territoriale': 'area_territoriale',
        'Regione': 'regione',
        'Prov': 'provincia',
        'Comune_ISTAT': 'cod_comune_istat',
        'Comune_cat': 'cod_comune_catastale',
        'Sez': 'sezione',
        'Comune_amm': 'cod_comune_amm',
        'Comune_descrizione': 'comune',
        'Fascia': 'fascia',
        'Zona': 'zona',
        'LinkZona': 'link_zona',
        'Cod_Tip': 'cod_tipologia',
        'Descr_Tipologia': 'tipologia',
        'Stato': 'stato_conservazione',
        'Stato_prev': 'stato_prevalente',
        'Compr_min': 'prezzo_min',
        'Compr_max': 'prezzo_max',
        'Sup_NL_compr': 'superficie_nl_compravendita',
        'Loc_min': 'locazione_min',
        'Loc_max': 'locazione_max',
        'Sup_NL_loc': 'superficie_nl_locazione',
        'Anno': 'anno',
        'Semestre': 'semestre'
    }
    df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
    return df


def normalize_qi_zone(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize QI ZONE table columns"""
    # Remove unnamed columns
    df = df.loc[:, ~df.columns.str.contains('^Unnamed', case=False)]

    column_mapping = {
        'Area_territoriale': 'area_territoriale',
        'Regione': 'regione',
        'Prov': 'provincia',
        'Comune_ISTAT': 'cod_comune_istat',
        'Comune_cat': 'cod_comune_catastale',
        'Sez': 'sezione',
        'Comune_amm': 'cod_comune_amm',
        'Comune_descrizione': 'comune',
        'Fascia': 'fascia',
        'Zona_Descr': 'zona_descrizione',
        'Zona': 'zona',
        'LinkZona': 'link_zona',
        'Cod_tip_prev': 'cod_tipologia_prevalente',
        'Descr_tip_prev': 'tipologia_prevalente',
        'Stato_prev': 'stato_prevalente',
        'Microzona': 'microzona',
        'Anno': 'anno',
        'Semestre': 'semestre'
    }
    df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
    return df


def normalize_vcn_comuni(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize VCN comuni table - consolidate municipality reference data"""
    # Handle PROV/PROVINCIA conflict - keep only one
    if 'PROV' in df.columns and 'PROVINCIA' in df.columns:
        df = df.drop(columns=['PROV'])
    elif 'PROV' in df.columns:
        df = df.rename(columns={'PROV': 'PROVINCIA'})

    column_mapping = {
        'AREA': 'area_territoriale',
        'REGIONE': 'regione',
        'PROVINCIA': 'provincia',
        'YEAR_CODCOM': 'cod_comune',
        'COMUNE': 'comune',
        'COD_ISTAT': 'cod_istat',
        'CAP': 'cap',
        'TAGLIA MERCATO': 'taglia_mercato',
        'ANNO': 'anno'
    }
    df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

    # Keep only relevant columns
    keep_cols = ['area_territoriale', 'regione', 'provincia', 'cod_comune', 'comune',
                 'cod_istat', 'cap', 'taglia_mercato', 'anno']
    existing_cols = [c for c in keep_cols if c in df.columns]
    return df[existing_cols]


def normalize_vcn_valori_com(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize VCN commercial values table"""
    # Handle PROV/PROVINCIA and COD_COM/YEAR_CODCOM conflicts
    if 'PROV' in df.columns and 'PROVINCIA' in df.columns:
        df = df.drop(columns=['PROV'])
    elif 'PROV' in df.columns:
        df = df.rename(columns={'PROV': 'PROVINCIA'})

    if 'COD_COM' in df.columns and 'YEAR_CODCOM' in df.columns:
        df = df.drop(columns=['COD_COM'])
    elif 'COD_COM' in df.columns:
        df = df.rename(columns={'COD_COM': 'YEAR_CODCOM'})

    column_mapping = {
        'AREA': 'area_territoriale',
        'REGIONE': 'regione',
        'PROVINCIA': 'provincia',
        'YEAR_CODCOM': 'cod_comune',
        'NTN_YEAR_UFFICI': 'ntn_uffici',
        'NTN_YEAR_NEGOZI_LAB': 'ntn_negozi_lab',
        'NTN_YEAR_DEPOSITI_COMM_AUTORIMESSE': 'ntn_depositi_comm_autorimesse',
        'NTN_YEAR_DEPOSITI_COMM': 'ntn_depositi_comm',
        'NTN_YEAR_TCO_B04': 'ntn_tco_b04',
        'NTN_YEAR_TCO_D02': 'ntn_tco_d02',
        'NTN_YEAR_TCO_D05': 'ntn_tco_d05',
        'NTN_YEAR_TCO_D08': 'ntn_tco_d08',
        'NTN_YEAR_PRO': 'ntn_produttivo',
        'NTN_YEAR_AGR': 'ntn_agricolo',
        'ANNO': 'anno'
    }
    df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

    # Keep only relevant columns
    keep_cols = ['area_territoriale', 'regione', 'provincia', 'cod_comune', 'anno',
                 'ntn_uffici', 'ntn_negozi_lab', 'ntn_depositi_comm_autorimesse',
                 'ntn_depositi_comm', 'ntn_tco_b04', 'ntn_tco_d02', 'ntn_tco_d05',
                 'ntn_tco_d08', 'ntn_produttivo', 'ntn_agricolo']
    existing_cols = [c for c in keep_cols if c in df.columns]
    return df[existing_cols]


def normalize_vcn_valori_per(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize VCN pertinence values table"""
    # Handle PROV/PROVINCIA and COD_COM/YEAR_CODCOM conflicts
    if 'PROV' in df.columns and 'PROVINCIA' in df.columns:
        df = df.drop(columns=['PROV'])
    elif 'PROV' in df.columns:
        df = df.rename(columns={'PROV': 'PROVINCIA'})

    if 'COD_COM' in df.columns and 'YEAR_CODCOM' in df.columns:
        df = df.drop(columns=['COD_COM'])
    elif 'COD_COM' in df.columns:
        df = df.rename(columns={'COD_COM': 'YEAR_CODCOM'})

    column_mapping = {
        'AREA': 'area_territoriale',
        'REGIONE': 'regione',
        'PROVINCIA': 'provincia',
        'YEAR_CODCOM': 'cod_comune',
        'NTN_YEAR_DEPOSITI_PERT': 'ntn_depositi_pertinenziali',
        'NTN_YEAR_BOX': 'ntn_box',
        'ANNO': 'anno'
    }
    df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

    # Keep only relevant columns
    keep_cols = ['area_territoriale', 'regione', 'provincia', 'cod_comune', 'anno',
                 'ntn_depositi_pertinenziali', 'ntn_box']
    existing_cols = [c for c in keep_cols if c in df.columns]
    return df[existing_cols]


def normalize_vcn_valori_res(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize VCN residential values table"""
    # Handle PROV/PROVINCIA and COD_COM/YEAR_CODCOM conflicts
    if 'PROV' in df.columns and 'PROVINCIA' in df.columns:
        df = df.drop(columns=['PROV'])
    elif 'PROV' in df.columns:
        df = df.rename(columns={'PROV': 'PROVINCIA'})

    if 'COD_COM' in df.columns and 'YEAR_CODCOM' in df.columns:
        df = df.drop(columns=['COD_COM'])
    elif 'COD_COM' in df.columns:
        df = df.rename(columns={'COD_COM': 'YEAR_CODCOM'})

    # Normalize the varied column naming patterns for residential data
    # Different years have different naming (spaces, underscores, etc.)
    new_cols = {}
    for col in df.columns:
        new_col = col
        # Normalize "fino a 50" variants
        if re.search(r'FINO\s*A?\s*50', col, re.IGNORECASE):
            new_col = 'ntn_fino_50mq'
        # Normalize "50 - 85" variants
        elif re.search(r'50\s*-\|?\s*85', col, re.IGNORECASE):
            new_col = 'ntn_50_85mq'
        # Normalize "85 - 115" variants
        elif re.search(r'85\s*-\|?\s*115', col, re.IGNORECASE):
            new_col = 'ntn_85_115mq'
        # Normalize "115 - 145" variants
        elif re.search(r'115\s*-\|?\s*145', col, re.IGNORECASE):
            new_col = 'ntn_115_145mq'
        # Normalize "oltre 145" variants
        elif re.search(r'OLTRE\s*145', col, re.IGNORECASE):
            new_col = 'ntn_oltre_145mq'
        # Normalize NTN total (NTN_YEAR or NTN 20XX)
        elif re.match(r'^NTN[_\s]*(YEAR|\d{4})$', col):
            new_col = 'ntn_totale'
        new_cols[col] = new_col

    df = df.rename(columns=new_cols)

    # Remove duplicate columns after renaming (keep first)
    df = df.loc[:, ~df.columns.duplicated()]

    # Standard column mapping for remaining columns
    column_mapping = {
        'AREA': 'area_territoriale',
        'REGIONE': 'regione',
        'PROVINCIA': 'provincia',
        'YEAR_CODCOM': 'cod_comune',
        'ANNO': 'anno'
    }
    df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

    # Keep only relevant columns
    keep_cols = ['area_territoriale', 'regione', 'provincia', 'cod_comune', 'anno',
                 'ntn_fino_50mq', 'ntn_50_85mq', 'ntn_85_115mq', 'ntn_115_145mq',
                 'ntn_oltre_145mq', 'ntn_totale']
    existing_cols = [c for c in keep_cols if c in df.columns]
    return df[existing_cols]


def create_database(output_path: Path, qi_valori: pd.DataFrame, qi_zone: pd.DataFrame,
                   vcn_tables: dict[str, pd.DataFrame]) -> None:
    """Create SQLite database with normalized tables"""
    # Remove existing database to ensure clean state
    if output_path.exists():
        output_path.unlink()

    conn = sqlite3.connect(output_path)

    # Normalize and save QI tables
    if not qi_valori.empty:
        qi_valori_norm = normalize_qi_valori(qi_valori)
        qi_valori_norm.to_sql('quotazioni_valori', conn, if_exists='replace', index=False)
        print(f"Created quotazioni_valori: {len(qi_valori_norm)} rows")

    if not qi_zone.empty:
        qi_zone_norm = normalize_qi_zone(qi_zone)
        qi_zone_norm.to_sql('quotazioni_zone', conn, if_exists='replace', index=False)
        print(f"Created quotazioni_zone: {len(qi_zone_norm)} rows")

    # Normalize and save VCN tables
    if 'lista_com' in vcn_tables:
        comuni = normalize_vcn_comuni(vcn_tables['lista_com'])
        comuni.to_sql('comuni', conn, if_exists='replace', index=False)
        print(f"Created comuni: {len(comuni)} rows")

    if 'valori_com' in vcn_tables:
        valori_com = normalize_vcn_valori_com(vcn_tables['valori_com'])
        valori_com.to_sql('compravendite_commerciali', conn, if_exists='replace', index=False)
        print(f"Created compravendite_commerciali: {len(valori_com)} rows")

    if 'valori_per' in vcn_tables:
        valori_per = normalize_vcn_valori_per(vcn_tables['valori_per'])
        valori_per.to_sql('compravendite_pertinenze', conn, if_exists='replace', index=False)
        print(f"Created compravendite_pertinenze: {len(valori_per)} rows")

    if 'valori_res' in vcn_tables:
        valori_res = normalize_vcn_valori_res(vcn_tables['valori_res'])
        valori_res.to_sql('compravendite_residenziali', conn, if_exists='replace', index=False)
        print(f"Created compravendite_residenziali: {len(valori_res)} rows")

    # Create indexes for common queries
    print("Creating indexes...")
    cursor = conn.cursor()

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_quotazioni_valori_comune ON quotazioni_valori(cod_comune_istat, anno, semestre)",
        "CREATE INDEX IF NOT EXISTS idx_quotazioni_valori_zona ON quotazioni_valori(link_zona)",
        "CREATE INDEX IF NOT EXISTS idx_quotazioni_zone_comune ON quotazioni_zone(cod_comune_istat, anno, semestre)",
        "CREATE INDEX IF NOT EXISTS idx_comuni_cod ON comuni(cod_comune, anno)",
        "CREATE INDEX IF NOT EXISTS idx_compravendite_com ON compravendite_commerciali(cod_comune, anno)",
        "CREATE INDEX IF NOT EXISTS idx_compravendite_per ON compravendite_pertinenze(cod_comune, anno)",
        "CREATE INDEX IF NOT EXISTS idx_compravendite_res ON compravendite_residenziali(cod_comune, anno)",
    ]

    for idx_sql in indexes:
        try:
            cursor.execute(idx_sql)
        except sqlite3.OperationalError as e:
            print(f"Warning: {e}")

    conn.commit()
    conn.close()
    print(f"\nDatabase created: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Import OMI data into SQLite database')
    parser.add_argument('--data-dir', type=Path, default=Path('/mnt/mobile/data/AdE/OMI'),
                       help='Directory containing OMI ZIP files')
    parser.add_argument('--output', type=Path, default=Path('/mnt/mobile/data/AdE/OMI/omi.sqlite'),
                       help='Output SQLite database path')
    args = parser.parse_args()

    print(f"Data directory: {args.data_dir}")
    print(f"Output database: {args.output}")
    print()

    # Process QI files
    print("=== Processing QI files ===")
    qi_valori, qi_zone = process_qi_files(args.data_dir)
    print()

    # Process VCN files
    print("=== Processing VCN files ===")
    vcn_tables = process_vcn_files(args.data_dir)
    print()

    # Create database
    print("=== Creating database ===")
    create_database(args.output, qi_valori, qi_zone, vcn_tables)


if __name__ == '__main__':
    main()
