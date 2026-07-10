"""
Tests for generate_cadastral_form module to boost coverage.
"""

import tempfile
import os
from pathlib import Path
from unittest.mock import patch, mock_open

from land_registry.generate_cadastral_form import (
    analyze_qgis_structure,
    generate_html_form,
    main
)


class TestAnalyzeQgisStructure:
    """Test analyze_qgis_structure function."""

    def test_analyze_qgis_structure_nonexistent_path(self):
        """Test with nonexistent path."""
        result = analyze_qgis_structure("/nonexistent/path")
        assert result == {}

    def test_analyze_qgis_structure_empty_directory(self):
        """Test with empty directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = analyze_qgis_structure(temp_dir)
            assert result == {}

    def test_analyze_qgis_structure_valid_structure(self):
        """Test with valid cadastral structure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create sample structure: REGION/PROVINCE/MUNICIPALITY_CODE_NAME/
            region_dir = Path(temp_dir) / "ABRUZZO"
            province_dir = region_dir / "AQ"
            municipality_dir = province_dir / "A018_ACCIANO"

            # Create directories
            municipality_dir.mkdir(parents=True)

            # Create sample GPKG files
            (municipality_dir / "A018_map.gpkg").touch()
            (municipality_dir / "A018_ple.gpkg").touch()
            (municipality_dir / "other_file.txt").touch()  # Should be ignored

            result = analyze_qgis_structure(temp_dir)

            assert "ABRUZZO" in result
            assert "AQ" in result["ABRUZZO"]
            assert "A018_ACCIANO" in result["ABRUZZO"]["AQ"]

            municipality_data = result["ABRUZZO"]["AQ"]["A018_ACCIANO"]
            assert municipality_data["code"] == "A018"
            assert municipality_data["name"] == "ACCIANO"
            assert len(municipality_data["files"]) == 2
            assert "A018_map.gpkg" in municipality_data["files"]
            assert "A018_ple.gpkg" in municipality_data["files"]
            assert "other_file.txt" not in municipality_data["files"]

    def test_analyze_qgis_structure_municipality_without_underscore(self):
        """Test municipality folder without underscore separator."""
        with tempfile.TemporaryDirectory() as temp_dir:
            region_dir = Path(temp_dir) / "LAZIO"
            province_dir = region_dir / "RM"
            municipality_dir = province_dir / "ROMA"

            municipality_dir.mkdir(parents=True)
            (municipality_dir / "roma_map.gpkg").touch()

            result = analyze_qgis_structure(temp_dir)

            municipality_data = result["LAZIO"]["RM"]["ROMA"]
            assert municipality_data["code"] == "ROMA"
            assert municipality_data["name"] == "ROMA"

    def test_analyze_qgis_structure_mixed_case_gpkg(self):
        """Test GPKG files with different case extensions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            region_dir = Path(temp_dir) / "VENETO"
            province_dir = region_dir / "VE"
            municipality_dir = province_dir / "A001_ABANO"

            municipality_dir.mkdir(parents=True)
            (municipality_dir / "test.GPKG").touch()
            (municipality_dir / "test2.gpkg").touch()
            (municipality_dir / "test3.Gpkg").touch()

            result = analyze_qgis_structure(temp_dir)

            files = result["VENETO"]["VE"]["A001_ABANO"]["files"]
            assert len(files) == 3
            assert "test.GPKG" in files
            assert "test2.gpkg" in files
            assert "test3.Gpkg" in files

    def test_analyze_qgis_structure_files_not_directories(self):
        """Test that files in base directory are ignored."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a file instead of directory at region level
            (Path(temp_dir) / "not_a_region.txt").touch()

            # Create valid structure
            region_dir = Path(temp_dir) / "CALABRIA"
            province_dir = region_dir / "CS"
            municipality_dir = province_dir / "A001_ACQUAFORMOSA"

            municipality_dir.mkdir(parents=True)
            (municipality_dir / "test.gpkg").touch()

            result = analyze_qgis_structure(temp_dir)

            # Should only contain the valid region, not the file
            assert len(result) == 1
            assert "CALABRIA" in result
            assert "not_a_region.txt" not in result

    def test_analyze_qgis_structure_no_gpkg_files(self):
        """Test municipality with no GPKG files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            region_dir = Path(temp_dir) / "SICILIA"
            province_dir = region_dir / "PA"
            municipality_dir = province_dir / "A001_PALERMO"

            municipality_dir.mkdir(parents=True)
            (municipality_dir / "readme.txt").touch()
            (municipality_dir / "data.shp").touch()

            result = analyze_qgis_structure(temp_dir)

            municipality_data = result["SICILIA"]["PA"]["A001_PALERMO"]
            assert municipality_data["files"] == []


class TestGenerateHtmlForm:
    """Test generate_html_form function."""

    def test_generate_html_form_basic(self):
        """Test basic HTML form generation."""
        structure = {
            "ABRUZZO": {
                "AQ": {
                    "A018_ACCIANO": {
                        "code": "A018",
                        "name": "ACCIANO",
                        "files": ["A018_map.gpkg", "A018_ple.gpkg"]
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            # Read generated file
            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            # Basic checks
            assert "<!DOCTYPE html>" in html_content
            assert "Italian Cadastral Data Selection" in html_content
            assert "ABRUZZO" in html_content
            assert '"code": "A018"' in html_content
            assert '"name": "ACCIANO"' in html_content
            # Files may be formatted differently by json.dumps with indent
            assert "A018_map.gpkg" in html_content
            assert "A018_ple.gpkg" in html_content

            # Check statistics are calculated
            assert '<div class="stat-number" id="totalRegions">1</div>' in html_content
            assert '<div class="stat-number" id="totalProvinces">1</div>' in html_content
            assert '<div class="stat-number" id="totalMunicipalities">1</div>' in html_content
            assert '<div class="stat-number" id="totalFiles">2</div>' in html_content

            os.unlink(temp_file.name)

    def test_generate_html_form_empty_structure(self):
        """Test HTML form generation with empty structure."""
        structure = {}

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            # Should still generate valid HTML with zero statistics
            assert "<!DOCTYPE html>" in html_content
            assert '<div class="stat-number" id="totalRegions">0</div>' in html_content
            assert '<div class="stat-number" id="totalProvinces">0</div>' in html_content
            assert '<div class="stat-number" id="totalMunicipalities">0</div>' in html_content
            assert '<div class="stat-number" id="totalFiles">0</div>' in html_content

            os.unlink(temp_file.name)

    def test_generate_html_form_special_characters(self):
        """Test HTML form generation with special characters in names."""
        structure = {
            "TRENTINO-ALTO ADIGE": {
                "BZ": {
                    "A001_MÜHLBACH": {
                        "code": "A001",
                        "name": "MÜHLBACH",
                        "files": ["test.gpkg"]
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            # Check that special characters are present (may be Unicode-escaped in JSON)
            assert "TRENTINO-ALTO ADIGE" in html_content
            # In the JSON part, Unicode characters get escaped by json.dumps
            # Check for either the raw character or the escaped version
            assert "MÜHLBACH" in html_content or "M\\u00dcHLBACH" in html_content

            os.unlink(temp_file.name)

    def test_generate_html_form_large_structure(self):
        """Test HTML form generation with larger structure."""
        structure = {}
        total_files = 0

        # Create structure with multiple regions, provinces, municipalities
        for region_idx in range(3):
            region_name = f"REGION_{region_idx}"
            structure[region_name] = {}

            for province_idx in range(2):
                province_name = f"PR_{province_idx}"
                structure[region_name][province_name] = {}

                for mun_idx in range(2):
                    mun_code = f"A{mun_idx:03d}"
                    mun_name = f"MUNICIPALITY_{mun_idx}"
                    folder_name = f"{mun_code}_{mun_name}"

                    files = [f"{mun_code}_map.gpkg", f"{mun_code}_ple.gpkg"]
                    total_files += len(files)

                    structure[region_name][province_name][folder_name] = {
                        "code": mun_code,
                        "name": mun_name,
                        "files": files
                    }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            # Check statistics
            assert '<div class="stat-number" id="totalRegions">3</div>' in html_content
            assert '<div class="stat-number" id="totalProvinces">6</div>' in html_content
            assert '<div class="stat-number" id="totalMunicipalities">12</div>' in html_content
            assert f'<div class="stat-number" id="totalFiles">{total_files}</div>' in html_content

            os.unlink(temp_file.name)


class TestMain:
    """Test main function."""

    @patch('land_registry.generate_cadastral_form.analyze_qgis_structure')
    @patch('land_registry.generate_cadastral_form.generate_html_form')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    def test_main_success(self, mock_json_dump, mock_file_open, mock_generate_html, mock_analyze):
        """Test successful main execution."""
        # Mock the analysis result
        mock_structure = {
            "ABRUZZO": {
                "AQ": {
                    "A018_ACCIANO": {
                        "code": "A018",
                        "name": "ACCIANO",
                        "files": ["A018_map.gpkg"]
                    }
                }
            }
        }
        mock_analyze.return_value = mock_structure

        # Run main
        main()

        # Verify calls
        mock_analyze.assert_called_once()
        mock_generate_html.assert_called_once()
        mock_json_dump.assert_called_once_with(
            mock_structure,
            mock_file_open.return_value.__enter__.return_value,
            indent=2,
            ensure_ascii=False
        )

    @patch('land_registry.generate_cadastral_form.analyze_qgis_structure')
    def test_main_no_data_found(self, mock_analyze):
        """Test main execution when no data is found."""
        mock_analyze.return_value = {}

        # Should not raise exception, just return early
        main()

        mock_analyze.assert_called_once()

    @patch('land_registry.generate_cadastral_form.analyze_qgis_structure')
    @patch('land_registry.generate_cadastral_form.generate_html_form')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    def test_main_with_complex_structure(self, mock_json_dump, mock_file_open, mock_generate_html, mock_analyze):
        """Test main with complex structure for coverage of summary calculations."""
        # Create complex structure to test all summary calculations
        mock_structure = {
            "ABRUZZO": {
                "AQ": {
                    "A018_ACCIANO": {
                        "code": "A018",
                        "name": "ACCIANO",
                        "files": ["A018_map.gpkg", "A018_ple.gpkg"]
                    },
                    "A019_ADELFIA": {
                        "code": "A019",
                        "name": "ADELFIA",
                        "files": ["A019_map.gpkg"]
                    }
                },
                "PE": {
                    "A020_PESCARA": {
                        "code": "A020",
                        "name": "PESCARA",
                        "files": ["A020_map.gpkg", "A020_ple.gpkg", "A020_extra.gpkg"]
                    }
                }
            },
            "LAZIO": {
                "RM": {
                    "H501_ROMA": {
                        "code": "H501",
                        "name": "ROMA",
                        "files": ["H501_map.gpkg"]
                    }
                }
            }
        }
        mock_analyze.return_value = mock_structure

        main()

        # Verify all functions were called
        mock_analyze.assert_called_once()
        mock_generate_html.assert_called_once()
        mock_json_dump.assert_called_once()


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_full_workflow_integration(self):
        """Test complete workflow from directory analysis to HTML generation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test directory structure
            region_dir = Path(temp_dir) / "TEST_REGION"
            province_dir = region_dir / "TP"
            municipality_dir = province_dir / "T001_TEST_CITY"

            municipality_dir.mkdir(parents=True)
            (municipality_dir / "T001_map.gpkg").touch()
            (municipality_dir / "T001_ple.gpkg").touch()

            # Analyze structure
            structure = analyze_qgis_structure(temp_dir)

            # Generate HTML
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as html_file:
                generate_html_form(structure, html_file.name)

                # Verify HTML was generated and contains expected content
                with open(html_file.name, 'r', encoding='utf-8') as f:
                    html_content = f.read()

                assert "TEST_REGION" in html_content
                assert "T001" in html_content
                assert "TEST_CITY" in html_content

                os.unlink(html_file.name)

    def test_edge_case_handling(self):
        """Test edge cases in the workflow."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create structure with edge cases
            region_dir = Path(temp_dir) / "EDGE_CASE_REGION"
            province_dir = region_dir / "EC"

            # Municipality with no files
            empty_mun_dir = province_dir / "E001_EMPTY"
            empty_mun_dir.mkdir(parents=True)

            # Municipality with non-GPKG files only
            non_gpkg_mun_dir = province_dir / "E002_NON_GPKG"
            non_gpkg_mun_dir.mkdir(parents=True)
            (non_gpkg_mun_dir / "data.txt").touch()
            (non_gpkg_mun_dir / "shape.shp").touch()

            # Analyze and generate
            structure = analyze_qgis_structure(temp_dir)

            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as html_file:
                generate_html_form(structure, html_file.name)

                with open(html_file.name, 'r', encoding='utf-8') as f:
                    html_content = f.read()

                # Should handle empty files list gracefully
                assert "EDGE_CASE_REGION" in html_content
                assert '"files": []' in html_content

                os.unlink(html_file.name)