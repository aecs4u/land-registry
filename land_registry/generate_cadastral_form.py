#!/usr/bin/env python3
"""
Script to analyze Italian cadastral QGIS data structure and generate
a JSON dictionary and HTML multiselection form.
"""

import os
import json
from pathlib import Path
import html

def analyze_qgis_structure(base_path):
    """
    Analyze the QGIS folder structure and create a nested dictionary.
    
    Structure: REGIONE/PROVINCIA/COMUNE_CODE_NAME/*.gpkg
    """
    structure = {}
    base_path = Path(base_path)
    
    if not base_path.exists():
        print(f"Error: Path {base_path} does not exist")
        return {}
    
    print(f"Analyzing structure at: {base_path}")
    
    # Walk through regions
    for region_path in sorted(base_path.iterdir()):
        if not region_path.is_dir():
            continue
            
        region_name = region_path.name
        print(f"Processing region: {region_name}")
        structure[region_name] = {}
        
        # Walk through provinces
        for province_path in sorted(region_path.iterdir()):
            if not province_path.is_dir():
                continue
                
            province_code = province_path.name
            print(f"  Processing province: {province_code}")
            structure[region_name][province_code] = {}
            
            # Walk through municipalities
            for municipality_path in sorted(province_path.iterdir()):
                if not municipality_path.is_dir():
                    continue
                    
                municipality_folder = municipality_path.name
                
                # Parse municipality code and name
                # Format: CODE_NAME (e.g., "A018_ACCIANO")
                parts = municipality_folder.split('_', 1)
                if len(parts) >= 2:
                    mun_code = parts[0]
                    mun_name = parts[1]
                else:
                    mun_code = municipality_folder
                    mun_name = municipality_folder
                
                print(f"    Processing municipality: {mun_code} - {mun_name}")
                
                # Collect GPKG files
                gpkg_files = []
                for file_path in sorted(municipality_path.iterdir()):
                    if file_path.is_file() and file_path.suffix.lower() == '.gpkg':
                        gpkg_files.append(file_path.name)
                
                structure[region_name][province_code][municipality_folder] = {
                    'code': mun_code,
                    'name': mun_name,
                    'files': gpkg_files
                }
    
    return structure

def generate_html_form(structure, output_path):
    """
    Generate an HTML form with multiselection inputs based on the structure.
    """
    
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Italian Cadastral Data Selection</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        
        h1 {{
            color: #333;
            text-align: center;
            border-bottom: 3px solid #007cba;
            padding-bottom: 10px;
        }}
        
        .form-section {{
            margin: 30px 0;
            padding: 20px;
            border: 1px solid #ddd;
            border-radius: 8px;
            background-color: #fafafa;
        }}
        
        .form-section h2 {{
            margin-top: 0;
            color: #007cba;
            border-bottom: 2px solid #007cba;
            padding-bottom: 5px;
        }}
        
        .selection-group {{
            margin: 20px 0;
        }}
        
        label {{
            display: block;
            font-weight: bold;
            margin-bottom: 10px;
            color: #333;
        }}
        
        select {{
            width: 100%;
            padding: 10px;
            border: 2px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
            background-color: white;
        }}
        
        select:focus {{
            border-color: #007cba;
            outline: none;
        }}
        
        .button-group {{
            text-align: center;
            margin: 30px 0;
        }}
        
        button {{
            background-color: #007cba;
            color: white;
            border: none;
            padding: 12px 25px;
            margin: 0 10px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
        }}
        
        button:hover {{
            background-color: #005a8b;
        }}
        
        .results {{
            margin-top: 30px;
            padding: 20px;
            background-color: #e7f3ff;
            border-radius: 8px;
            border: 1px solid #007cba;
        }}
        
        .results h3 {{
            margin-top: 0;
            color: #007cba;
        }}
        
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}
        
        .stat-card {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }}
        
        .stat-number {{
            font-size: 24px;
            font-weight: bold;
            color: #007cba;
        }}
        
        .stat-label {{
            font-size: 12px;
            color: #666;
            margin-top: 5px;
        }}
        
        .municipality-list {{
            max-height: 300px;
            overflow-y: auto;
            border: 1px solid #ddd;
            padding: 10px;
            background: white;
            border-radius: 5px;
        }}
        
        .municipality-item {{
            padding: 5px 10px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
        }}
        
        .municipality-code {{
            font-weight: bold;
            color: #007cba;
        }}
        
        .file-count {{
            font-size: 12px;
            color: #666;
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 3px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🏛️ Italian Cadastral Data Selection Tool</h1>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number" id="totalRegions">{len(structure)}</div>
                <div class="stat-label">Regions</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="totalProvinces">{sum(len(provinces) for provinces in structure.values())}</div>
                <div class="stat-label">Provinces</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="totalMunicipalities">{sum(len(municipalities) for region in structure.values() for municipalities in region.values())}</div>
                <div class="stat-label">Municipalities</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="totalFiles">{sum(len(mun_data['files']) for region in structure.values() for province in region.values() for mun_data in province.values())}</div>
                <div class="stat-label">GPKG Files</div>
            </div>
        </div>
        
        <form id="cadastralForm">
            <div class="form-section">
                <h2>🌍 Geographic Selection</h2>
                
                <div class="selection-group">
                    <label for="regions">Select Regions:</label>
                    <select id="regions" multiple size="8">
'''

    # Add region options
    for region in sorted(structure.keys()):
        html_content += f'                        <option value="{html.escape(region)}">{html.escape(region)}</option>\n'
    
    html_content += '''                    </select>
                </div>
                
                <div class="selection-group">
                    <label for="provinces">Select Provinces:</label>
                    <select id="provinces" multiple size="8" disabled>
                    </select>
                </div>
                
                <div class="selection-group">
                    <label for="municipalities">Select Municipalities:</label>
                    <select id="municipalities" multiple size="10" disabled>
                    </select>
                </div>
            </div>
            
            <div class="form-section">
                <h2>📁 File Type Selection</h2>
                
                <div class="selection-group">
                    <label for="fileTypes">Select File Types:</label>
                    <select id="fileTypes" multiple size="4">
                        <option value="_map.gpkg">Map Files (_map.gpkg)</option>
                        <option value="_ple.gpkg">PLE Files (_ple.gpkg)</option>
                    </select>
                </div>
            </div>
            
            <div class="button-group">
                <button type="button" onclick="selectAll()">Select All</button>
                <button type="button" onclick="clearAll()">Clear All</button>
                <button type="button" onclick="generateSelection()">Generate Selection</button>
                <button type="button" onclick="downloadJSON()">Download JSON</button>
            </div>
        </form>
        
        <div class="results" id="results" style="display: none;">
            <h3>📋 Selection Results</h3>
            <div id="selectionResults"></div>
            <div class="municipality-list" id="municipalityList"></div>
        </div>
    </div>
    
    <script>
        // Store the complete data structure
        const cadastralData = ''' + json.dumps(structure, indent=8) + ''';
        
        let selectedRegions = [];
        let selectedProvinces = [];
        let selectedMunicipalities = [];
        let selectedFileTypes = [];
        
        // Event listeners
        document.getElementById('regions').addEventListener('change', updateProvinces);
        document.getElementById('provinces').addEventListener('change', updateMunicipalities);
        document.getElementById('municipalities').addEventListener('change', updateSelection);
        document.getElementById('fileTypes').addEventListener('change', updateSelection);
        
        function updateProvinces() {
            const regionSelect = document.getElementById('regions');
            const provinceSelect = document.getElementById('provinces');
            
            selectedRegions = Array.from(regionSelect.selectedOptions).map(option => option.value);
            
            // Clear and populate provinces
            provinceSelect.innerHTML = '';
            provinceSelect.disabled = selectedRegions.length === 0;
            
            const provinces = new Set();
            selectedRegions.forEach(region => {
                if (cadastralData[region]) {
                    Object.keys(cadastralData[region]).forEach(province => {
                        provinces.add(province);
                    });
                }
            });
            
            Array.from(provinces).sort().forEach(province => {
                const option = document.createElement('option');
                option.value = province;
                option.textContent = province;
                provinceSelect.appendChild(option);
            });
            
            updateMunicipalities();
        }
        
        function updateMunicipalities() {
            const provinceSelect = document.getElementById('provinces');
            const municipalitySelect = document.getElementById('municipalities');
            
            selectedProvinces = Array.from(provinceSelect.selectedOptions).map(option => option.value);
            
            // Clear and populate municipalities
            municipalitySelect.innerHTML = '';
            municipalitySelect.disabled = selectedProvinces.length === 0;
            
            const municipalities = [];
            selectedRegions.forEach(region => {
                if (cadastralData[region]) {
                    selectedProvinces.forEach(province => {
                        if (cadastralData[region][province]) {
                            Object.entries(cadastralData[region][province]).forEach(([folder, data]) => {
                                municipalities.push({
                                    folder: folder,
                                    code: data.code,
                                    name: data.name,
                                    region: region,
                                    province: province,
                                    fileCount: data.files.length
                                });
                            });
                        }
                    });
                }
            });
            
            municipalities.sort((a, b) => a.name.localeCompare(b.name)).forEach(municipality => {
                const option = document.createElement('option');
                option.value = `${municipality.region}|${municipality.province}|${municipality.folder}`;
                option.textContent = `${municipality.code} - ${municipality.name} (${municipality.fileCount} files)`;
                municipalitySelect.appendChild(option);
            });
            
            updateSelection();
        }
        
        function updateSelection() {
            const municipalitySelect = document.getElementById('municipalities');
            const fileTypeSelect = document.getElementById('fileTypes');
            
            selectedMunicipalities = Array.from(municipalitySelect.selectedOptions).map(option => option.value);
            selectedFileTypes = Array.from(fileTypeSelect.selectedOptions).map(option => option.value);
        }
        
        function selectAll() {
            const selects = document.querySelectorAll('select');
            selects.forEach(select => {
                for (let option of select.options) {
                    option.selected = true;
                }
            });
            updateProvinces();
        }
        
        function clearAll() {
            const selects = document.querySelectorAll('select');
            selects.forEach(select => {
                select.selectedIndex = -1;
            });
            selectedRegions = [];
            selectedProvinces = [];
            selectedMunicipalities = [];
            selectedFileTypes = [];
            updateProvinces();
            document.getElementById('results').style.display = 'none';
        }
        
        function generateSelection() {
            if (selectedMunicipalities.length === 0 || selectedFileTypes.length === 0) {
                alert('Please select at least one municipality and one file type.');
                return;
            }
            
            const selection = {
                timestamp: new Date().toISOString(),
                selection: {
                    regions: selectedRegions,
                    provinces: selectedProvinces,
                    municipalities: selectedMunicipalities.length,
                    fileTypes: selectedFileTypes
                },
                files: []
            };
            
            selectedMunicipalities.forEach(municipalityPath => {
                const [region, province, folder] = municipalityPath.split('|');
                const municipalityData = cadastralData[region][province][folder];
                
                selectedFileTypes.forEach(fileType => {
                    const matchingFiles = municipalityData.files.filter(file => file.endsWith(fileType));
                    matchingFiles.forEach(file => {
                        selection.files.push({
                            region: region,
                            province: province,
                            municipalityCode: municipalityData.code,
                            municipalityName: municipalityData.name,
                            fileName: file,
                            filePath: `${region}/${province}/${folder}/${file}`
                        });
                    });
                });
            });
            
            // Display results
            const resultsDiv = document.getElementById('results');
            const selectionResultsDiv = document.getElementById('selectionResults');
            const municipalityListDiv = document.getElementById('municipalityList');
            
            selectionResultsDiv.innerHTML = `
                <p><strong>Selected:</strong> ${selection.files.length} files from ${selectedMunicipalities.length} municipalities</p>
                <p><strong>File Types:</strong> ${selectedFileTypes.join(', ')}</p>
                <p><strong>Generated:</strong> ${new Date().toLocaleString()}</p>
            `;
            
            municipalityListDiv.innerHTML = '';
            const municipalityFiles = {};
            
            selection.files.forEach(file => {
                const key = `${file.municipalityCode} - ${file.municipalityName}`;
                if (!municipalityFiles[key]) {
                    municipalityFiles[key] = [];
                }
                municipalityFiles[key].push(file.fileName);
            });
            
            Object.entries(municipalityFiles).forEach(([municipality, files]) => {
                const div = document.createElement('div');
                div.className = 'municipality-item';
                div.innerHTML = `
                    <span class="municipality-code">${municipality}</span>
                    <span class="file-count">${files.length} files</span>
                `;
                municipalityListDiv.appendChild(div);
            });
            
            resultsDiv.style.display = 'block';
            
            // Store selection for download
            window.currentSelection = selection;
        }
        
        function downloadJSON() {
            if (!window.currentSelection) {
                alert('Please generate a selection first.');
                return;
            }
            
            const blob = new Blob([JSON.stringify(window.currentSelection, null, 2)], {
                type: 'application/json'
            });
            
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `cadastral_selection_${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
    </script>
</body>
</html>'''

    # Write HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"HTML form generated: {output_path}")

def main():
    """Main function to run the analysis and generation."""

    root_folder = os.path.dirname(__file__)
    
    base_path = "/media/emanuele/ddbb5477-3ef2-4097-b731-3784cb7767c1/aecs4u.it/catasto/qgis"
    json_output = os.path.join(root_folder, "../data/cadastral_structure.json")
    html_output = "/media/emanuele/research/git/aecs4u.it/map/cadastral_selection_form.html"
    
    print("Starting cadastral data analysis...")
    
    # Analyze the structure
    structure = analyze_qgis_structure(base_path)
    
    if not structure:
        print("No data found or error occurred.")
        return
    
    # Save JSON structure
    with open(json_output, 'w', encoding='utf-8') as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)
    
    print(f"JSON structure saved: {json_output}")
    
    # Generate HTML form
    generate_html_form(structure, html_output)
    
    # Print summary
    total_regions = len(structure)
    total_provinces = sum(len(provinces) for provinces in structure.values())
    total_municipalities = sum(len(municipalities) for region in structure.values() for municipalities in region.values())
    total_files = sum(len(mun_data['files']) for region in structure.values() for province in region.values() for mun_data in province.values())
    
    print("\n=== ANALYSIS SUMMARY ===")
    print(f"Total Regions: {total_regions}")
    print(f"Total Provinces: {total_provinces}")
    print(f"Total Municipalities: {total_municipalities}")
    print(f"Total GPKG Files: {total_files}")
    print(f"\nFiles generated:")
    print(f"  - JSON Structure: {json_output}")
    print(f"  - HTML Form: {html_output}")

if __name__ == "__main__":
    main()