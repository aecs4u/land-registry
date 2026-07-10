// Cadastral Data Filtering JavaScript

// Store original data for filtering
let originalStats = {
    regions: 0,
    provinces: 0,
    municipalities: 0,
    files: 0
};

// Initialize when page loads
document.addEventListener('DOMContentLoaded', function() {
    // Store original statistics
    const statsElement = document.getElementById('statsDisplay');
    if (statsElement) {
        const statsText = statsElement.textContent;
        const match = statsText.match(/(\d+)\s+Regions.*?(\d+)\s+Provinces.*?(\d+)\s+Municipalities.*?(\d+)\s+Files/);
        if (match) {
            originalStats = {
                regions: parseInt(match[1]),
                provinces: parseInt(match[2]),
                municipalities: parseInt(match[3]),
                files: parseInt(match[4])
            };
        }
    }

    // Add enter key support for filter inputs
    ['regionFilter', 'provinceFilter', 'municipalityFilter'].forEach(id => {
        const input = document.getElementById(id);
        if (input) {
            input.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    applyFilters();
                }
            });
        }
    });
});

function applyFilters() {
    const regionFilter = document.getElementById('regionFilter').value.toLowerCase().trim();
    const provinceFilter = document.getElementById('provinceFilter').value.toLowerCase().trim();
    const municipalityFilter = document.getElementById('municipalityFilter').value.toLowerCase().trim();

    const regions = document.querySelectorAll('.region');
    const noResultsMsg = document.getElementById('noResultsMessage');
    const cadastralContainer = document.getElementById('cadastralDataContainer');
    const resultsInfo = document.getElementById('filterResultsInfo');

    let visibleRegions = 0;
    let visibleProvinces = 0;
    let visibleMunicipalities = 0;
    let visibleFiles = 0;

    let hasAnyResults = false;

    regions.forEach(region => {
        const regionName = region.dataset.region;
        let regionHasVisibleContent = false;

        // Check if region matches filter
        let regionMatches = !regionFilter || regionName.includes(regionFilter);

        if (!regionMatches) {
            region.style.display = 'none';
            return;
        }

        const provinces = region.querySelectorAll('.province');
        provinces.forEach(province => {
            const provinceName = province.dataset.province;
            let provinceHasVisibleContent = false;

            // Check if province matches filter
            let provinceMatches = !provinceFilter || provinceName.includes(provinceFilter);

            if (!provinceMatches) {
                province.style.display = 'none';
                return;
            }

            const municipalities = province.querySelectorAll('.municipality');
            let visibleMunicipalitiesInProvince = 0;

            municipalities.forEach(municipality => {
                const municipalityName = municipality.dataset.municipality;

                // Check if municipality matches filter
                let municipalityMatches = !municipalityFilter || municipalityName.includes(municipalityFilter);

                if (municipalityMatches) {
                    municipality.style.display = 'block';
                    visibleMunicipalitiesInProvince++;
                    visibleMunicipalities++;

                    // Count files in this municipality
                    const filesText = municipality.querySelector('.files strong').textContent;
                    const filesMatch = filesText.match(/Files \\((\\d+)\\)/);
                    if (filesMatch) {
                        visibleFiles += parseInt(filesMatch[1]);
                    }
                } else {
                    municipality.style.display = 'none';
                }
            });

            if (visibleMunicipalitiesInProvince > 0) {
                province.style.display = 'block';
                provinceHasVisibleContent = true;
                visibleProvinces++;
            } else {
                province.style.display = 'none';
            }
        });

        // Show region only if it has visible provinces
        const visibleProvincesInRegion = Array.from(region.querySelectorAll('.province')).filter(p => p.style.display !== 'none').length;
        if (visibleProvincesInRegion > 0) {
            region.style.display = 'block';
            regionHasVisibleContent = true;
            visibleRegions++;
            hasAnyResults = true;
        } else {
            region.style.display = 'none';
        }
    });

    // Update statistics
    updateStats(visibleRegions, visibleProvinces, visibleMunicipalities, visibleFiles);

    // Show/hide no results message
    if (hasAnyResults) {
        cadastralContainer.style.display = 'block';
        noResultsMsg.style.display = 'none';

        // Show filter results info if any filters are applied
        if (regionFilter || provinceFilter || municipalityFilter) {
            showFilterResults(visibleRegions, visibleProvinces, visibleMunicipalities, visibleFiles);
        } else {
            resultsInfo.style.display = 'none';
        }
    } else {
        cadastralContainer.style.display = 'none';
        noResultsMsg.style.display = 'block';
        showFilterResults(0, 0, 0, 0);
    }
}

function clearFilters() {
    // Clear all filter inputs
    document.getElementById('regionFilter').value = '';
    document.getElementById('provinceFilter').value = '';
    document.getElementById('municipalityFilter').value = '';

    // Show all regions, provinces, and municipalities
    const regions = document.querySelectorAll('.region');
    const provinces = document.querySelectorAll('.province');
    const municipalities = document.querySelectorAll('.municipality');

    regions.forEach(region => region.style.display = 'block');
    provinces.forEach(province => province.style.display = 'block');
    municipalities.forEach(municipality => municipality.style.display = 'block');

    // Reset statistics to original values
    updateStats(originalStats.regions, originalStats.provinces, originalStats.municipalities, originalStats.files);

    // Hide results info and no results message
    document.getElementById('filterResultsInfo').style.display = 'none';
    document.getElementById('noResultsMessage').style.display = 'none';
    document.getElementById('cadastralDataContainer').style.display = 'block';
}

function updateStats(regions, provinces, municipalities, files) {
    const statsElement = document.getElementById('statsDisplay');
    if (statsElement) {
        statsElement.textContent = `📊 Statistics: ${regions} Regions | ${provinces} Provinces | ${municipalities} Municipalities | ${files} Files`;
    }
}

function showFilterResults(regions, provinces, municipalities, files) {
    const resultsInfo = document.getElementById('filterResultsInfo');
    if (resultsInfo) {
        if (regions === 0 && provinces === 0 && municipalities === 0 && files === 0) {
            resultsInfo.innerHTML = '🔍 No results found matching your filter criteria.';
        } else {
            resultsInfo.innerHTML = `🔍 Filtered results: ${regions} regions, ${provinces} provinces, ${municipalities} municipalities, ${files} files found.`;
        }
        resultsInfo.style.display = 'block';
    }
}

// File Availability Functions

async function checkFileAvailability(forceRefresh = false) {
    const checkBtn = document.getElementById('checkAvailabilityBtn');
    const clearBtn = document.getElementById('clearCacheBtn');
    const statusDiv = document.getElementById('availabilityStatus');

    // Disable buttons during check
    checkBtn.disabled = true;
    clearBtn.disabled = true;
    checkBtn.textContent = '🔄 Checking...';

    try {
        // Show status
        showAvailabilityStatus('info', '🔄 Checking file availability...');

        const response = await fetch('/api/v1/check-file-availability/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ force_refresh: forceRefresh })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
            // Update file availability stats
            updateFileAvailabilityDisplay(data);

            // Show success message
            const message = `✅ File availability check completed!
            📊 Total files: ${data.total_files}
            ✅ Available: ${data.available_files}
            ❌ Missing: ${data.missing_files}
            ⚠️ Errors: ${data.error_files}
            🔍 Files checked: ${data.files_checked}
            💾 Files from cache: ${data.files_cached}`;

            showAvailabilityStatus('success', message);

            // Refresh the page to show updated counts
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        } else {
            throw new Error('File availability check failed');
        }

    } catch (error) {
        console.error('Error checking file availability:', error);
        showAvailabilityStatus('error', `❌ Error checking file availability: ${error.message}`);
    } finally {
        // Re-enable buttons
        checkBtn.disabled = false;
        clearBtn.disabled = false;
        checkBtn.textContent = '🔄 Check File Availability';
    }
}

async function clearAvailabilityCache() {
    const checkBtn = document.getElementById('checkAvailabilityBtn');
    const clearBtn = document.getElementById('clearCacheBtn');

    if (!confirm('Are you sure you want to clear the file availability cache? This will force a fresh check on the next request.')) {
        return;
    }

    // Disable buttons during operation
    checkBtn.disabled = true;
    clearBtn.disabled = true;
    clearBtn.textContent = '🗑️ Clearing...';

    try {
        showAvailabilityStatus('info', '🗑️ Clearing file availability cache...');

        const response = await fetch('/api/v1/file-availability-cache/', {
            method: 'DELETE'
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
            showAvailabilityStatus('success', '✅ File availability cache cleared successfully!');

            // Refresh the page to show updated counts
            setTimeout(() => {
                window.location.reload();
            }, 1500);
        } else {
            throw new Error('Failed to clear cache');
        }

    } catch (error) {
        console.error('Error clearing cache:', error);
        showAvailabilityStatus('error', `❌ Error clearing cache: ${error.message}`);
    } finally {
        // Re-enable buttons
        checkBtn.disabled = false;
        clearBtn.disabled = false;
        clearBtn.textContent = '🗑️ Clear Cache';
    }
}

function showAvailabilityStatus(type, message) {
    const statusDiv = document.getElementById('availabilityStatus');
    if (!statusDiv) return;

    // Remove existing classes
    statusDiv.className = 'availability-status';
    statusDiv.classList.add(type);

    // Format message for display
    const formattedMessage = message.replace(/\n/g, '<br>');
    statusDiv.innerHTML = formattedMessage;
    statusDiv.style.display = 'block';

    // Auto-hide info messages after 5 seconds
    if (type === 'info') {
        setTimeout(() => {
            statusDiv.style.display = 'none';
        }, 5000);
    }
}

function updateFileAvailabilityDisplay(data) {
    // Update the availability spans if they exist
    const availableSpan = document.querySelector('.available-files');
    const missingSpan = document.querySelector('.missing-files');
    const percentageSpan = document.querySelector('.availability-percentage');

    if (availableSpan) {
        availableSpan.textContent = `✅ Available: ${data.available_files}`;
    }

    if (missingSpan) {
        missingSpan.textContent = `❌ Missing: ${data.missing_files}`;
    }

    if (percentageSpan && data.total_files > 0) {
        const percentage = (data.available_files / data.total_files * 100).toFixed(1);
        percentageSpan.textContent = `📈 ${percentage}% Available`;
    }
}