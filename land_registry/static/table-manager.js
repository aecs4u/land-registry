// Table Manager for enhanced tables with server-side pagination, filtering and sorting
// Handles Table View, Adjacency View, and Mapping View tables

// Global table state variables
let currentTablePage = 1;
let totalTablePages = 1;
let currentAdjacencyPage = 1;
let totalAdjacencyPages = 1;
let currentMappingPage = 1;
let totalMappingPages = 1;

// Table configuration
const tableConfigs = {
    table: {
        endpoint: '/api/v1/table-data',
        pageSize: 100,
        searchField: 'tableSearch',
        filterField: 'tableFilterField',
        filterValue: 'tableFilterValue',
        sortField: 'tableSortField',
        sortDirection: 'tableSortDirection',
        pageSizeSelect: 'tablePageSize',
        tableHead: 'tableHead',
        tableBody: 'tableBody',
        recordInfo: 'tableRecordInfo',
        pageNumbers: 'tablePageNumbers',
        firstBtn: 'tableFirstBtn',
        prevBtn: 'tablePrevBtn',
        nextBtn: 'tableNextBtn',
        lastBtn: 'tableLastBtn',
        paginationContainer: '.pagination-container',
        tableWrapper: '.table-wrapper',
        loadingMessage: '.loading-message',
        noDataMessage: '.no-data-message'
    },
    adjacency: {
        endpoint: '/api/v1/adjacency-data',
        pageSize: 50,
        searchField: 'adjacencySearch',
        filterField: 'adjacencyFilterField',
        filterValue: 'adjacencyFilterValue',
        sortField: 'adjacencySortField',
        sortDirection: 'adjacencySortDirection',
        pageSizeSelect: 'adjacencyPageSize',
        tableHead: 'adjacencyTableHead',
        tableBody: 'adjacencyTableBody',
        recordInfo: 'adjacencyRecordInfo',
        pageNumbers: 'adjacencyPageNumbers',
        firstBtn: 'adjacencyFirstBtn',
        prevBtn: 'adjacencyPrevBtn',
        nextBtn: 'adjacencyNextBtn',
        lastBtn: 'adjacencyLastBtn',
        paginationContainer: '.pagination-container',
        tableWrapper: '.table-wrapper',
        loadingMessage: '.loading-message',
        noDataMessage: '.no-data-message'
    },
    mapping: {
        endpoint: '/api/v1/mapping-data',
        pageSize: 25,
        searchField: 'mappingSearch',
        filterField: 'mappingFilterField',
        filterValue: 'mappingFilterValue',
        sortField: 'mappingSortField',
        sortDirection: 'mappingSortDirection',
        pageSizeSelect: 'mappingPageSize',
        tableHead: 'mappingTableHead',
        tableBody: 'mappingTableBody',
        recordInfo: 'mappingRecordInfo',
        pageNumbers: 'mappingPageNumbers',
        firstBtn: 'mappingFirstBtn',
        prevBtn: 'mappingPrevBtn',
        nextBtn: 'mappingNextBtn',
        lastBtn: 'mappingLastBtn',
        paginationContainer: '.pagination-container',
        tableWrapper: '.table-wrapper',
        loadingMessage: '.loading-message',
        noDataMessage: '.no-data-message'
    }
};

const MAPPING_VIEW_QUERY_KEY = 'view';
const MAPPING_WORKFLOW_QUERY_KEY = 'workflow';
const MAPPING_ZONE_QUERY_KEY = 'zone_id';

window.mappingViewContext = null;

// Generic function to load table data
async function loadTableData(tableType, page = 1) {
    const config = tableConfigs[tableType];
    if (!config) return;

    const searchValue = document.getElementById(config.searchField)?.value || '';
    const filterField = document.getElementById(config.filterField)?.value || '';
    const filterValue = document.getElementById(config.filterValue)?.value || '';
    const sortField = document.getElementById(config.sortField)?.value || '';
    const sortDirection = document.getElementById(config.sortDirection)?.value || 'asc';
    const pageSize = document.getElementById(config.pageSizeSelect)?.value || config.pageSize;

    // Show loading state
    showLoadingState(tableType);

    try {
        const params = new URLSearchParams({
            page: page.toString(),
            size: pageSize.toString()
        });

        if (searchValue) params.append('search', searchValue);
        if (filterField && filterValue) {
            params.append('filter_field', filterField);
            params.append('filter_value', filterValue);
        }
        if (sortField) {
            params.append('sort_field', sortField);
            params.append('sort_dir', sortDirection);
        }

        const response = await fetch(`${config.endpoint}?${params}`);
        const data = await response.json();

        if (response.ok) {
            renderTable(tableType, data);
            updatePagination(tableType, data);
            updateCurrentPage(tableType, page);
        } else {
            showError(tableType, data.detail || 'Error loading data');
        }
    } catch (error) {
        console.error(`Error loading ${tableType} data:`, error);
        showError(tableType, 'Network error occurred');
    }
}

// Show loading state
function showLoadingState(tableType) {
    const config = tableConfigs[tableType];
    const container = getTableContainer(tableType);

    if (container) {
        container.querySelector(config.loadingMessage)?.style.setProperty('display', 'block');
        container.querySelector(config.tableWrapper)?.style.setProperty('display', 'none');
        container.querySelector(config.noDataMessage)?.style.setProperty('display', 'none');
        container.querySelector(config.paginationContainer)?.style.setProperty('display', 'none');
    }
}

// Show error state
function showError(tableType, message) {
    const config = tableConfigs[tableType];
    const container = getTableContainer(tableType);

    if (container) {
        container.querySelector(config.loadingMessage)?.style.setProperty('display', 'none');
        container.querySelector(config.tableWrapper)?.style.setProperty('display', 'none');
        container.querySelector(config.paginationContainer)?.style.setProperty('display', 'none');

        const noDataEl = container.querySelector(config.noDataMessage);
        if (noDataEl) {
            noDataEl.style.display = 'block';
            noDataEl.innerHTML = `<p>Error: ${message}</p>`;
        }
    }
}

// Get table container based on type
function getTableContainer(tableType) {
    switch (tableType) {
        case 'table':
            return document.getElementById('tableView');
        case 'adjacency':
            return document.getElementById('adjacencyView');
        case 'mapping':
            return document.getElementById('mappingView');
        default:
            return null;
    }
}

// Render table with data
function renderTable(tableType, data) {
    const config = tableConfigs[tableType];
    const container = getTableContainer(tableType);

    if (!container || !data.data || data.data.length === 0) {
        showNoData(tableType);
        return;
    }

    // Show table elements
    container.querySelector(config.loadingMessage)?.style.setProperty('display', 'none');
    container.querySelector(config.noDataMessage)?.style.setProperty('display', 'none');
    container.querySelector(config.tableWrapper)?.style.setProperty('display', 'block');
    container.querySelector(config.paginationContainer)?.style.setProperty('display', 'flex');

    // Populate column selects
    populateColumnSelects(tableType, data.columns);

    // Render table header
    const tableHead = document.getElementById(config.tableHead);
    if (tableHead) {
        tableHead.innerHTML = '';
        const headerRow = document.createElement('tr');
        data.columns.forEach(column => {
            const th = document.createElement('th');
            th.textContent = column;
            th.style.cursor = 'pointer';
            th.onclick = () => sortByColumn(tableType, column);
            headerRow.appendChild(th);
        });
        tableHead.appendChild(headerRow);
    }

    // Render table body
    const tableBody = document.getElementById(config.tableBody);
    if (tableBody) {
        tableBody.innerHTML = '';
        data.data.forEach(row => {
            const tr = document.createElement('tr');
            data.columns.forEach(column => {
                const td = document.createElement('td');
                const value = row[column];
                td.textContent = value !== null && value !== undefined ? value : '';
                tr.appendChild(td);
            });
            tableBody.appendChild(tr);
        });
    }
}

// Show no data state
function showNoData(tableType) {
    const config = tableConfigs[tableType];
    const container = getTableContainer(tableType);

    if (container) {
        container.querySelector(config.loadingMessage)?.style.setProperty('display', 'none');
        container.querySelector(config.tableWrapper)?.style.setProperty('display', 'none');
        container.querySelector(config.paginationContainer)?.style.setProperty('display', 'none');
        container.querySelector(config.noDataMessage)?.style.setProperty('display', 'block');
    }
}

// Populate column select dropdowns
function populateColumnSelects(tableType, columns) {
    const config = tableConfigs[tableType];

    const filterSelect = document.getElementById(config.filterField);
    const sortSelect = document.getElementById(config.sortField);

    // Clear and populate filter select
    if (filterSelect) {
        const currentFilter = filterSelect.value;
        filterSelect.innerHTML = '<option value="">Filter by column...</option>';
        columns.forEach(column => {
            const option = document.createElement('option');
            option.value = column;
            option.textContent = column;
            if (column === currentFilter) option.selected = true;
            filterSelect.appendChild(option);
        });
    }

    // Clear and populate sort select
    if (sortSelect) {
        const currentSort = sortSelect.value;
        sortSelect.innerHTML = '<option value="">Sort by column...</option>';
        columns.forEach(column => {
            const option = document.createElement('option');
            option.value = column;
            option.textContent = column;
            if (column === currentSort) option.selected = true;
            sortSelect.appendChild(option);
        });
    }
}

// Update pagination controls
function updatePagination(tableType, data) {
    const config = tableConfigs[tableType];

    // Update record info
    const recordInfo = document.getElementById(config.recordInfo);
    if (recordInfo) {
        const start = (data.page - 1) * data.size + 1;
        const end = Math.min(data.page * data.size, data.total);
        recordInfo.textContent = `Showing ${start} to ${end} of ${data.total} entries`;
    }

    // Update page numbers
    const pageNumbers = document.getElementById(config.pageNumbers);
    if (pageNumbers) {
        pageNumbers.innerHTML = '';

        const maxVisiblePages = 5;
        const startPage = Math.max(1, data.page - Math.floor(maxVisiblePages / 2));
        const endPage = Math.min(data.total_pages, startPage + maxVisiblePages - 1);

        for (let i = startPage; i <= endPage; i++) {
            const button = document.createElement('button');
            button.textContent = i;
            button.className = 'page-btn';
            if (i === data.page) button.classList.add('active');
            button.onclick = () => goToPage(tableType, i);
            pageNumbers.appendChild(button);
        }
    }

    // Update navigation buttons
    const firstBtn = document.getElementById(config.firstBtn);
    const prevBtn = document.getElementById(config.prevBtn);
    const nextBtn = document.getElementById(config.nextBtn);
    const lastBtn = document.getElementById(config.lastBtn);

    if (firstBtn) firstBtn.disabled = data.page <= 1;
    if (prevBtn) prevBtn.disabled = data.page <= 1;
    if (nextBtn) nextBtn.disabled = data.page >= data.total_pages;
    if (lastBtn) lastBtn.disabled = data.page >= data.total_pages;

    // Store total pages
    updateTotalPages(tableType, data.total_pages);
}

// Update current page variable
function updateCurrentPage(tableType, page) {
    switch (tableType) {
        case 'table':
            currentTablePage = page;
            break;
        case 'adjacency':
            currentAdjacencyPage = page;
            break;
        case 'mapping':
            currentMappingPage = page;
            break;
    }
}

// Update total pages variable
function updateTotalPages(tableType, totalPages) {
    switch (tableType) {
        case 'table':
            totalTablePages = totalPages;
            break;
        case 'adjacency':
            totalAdjacencyPages = totalPages;
            break;
        case 'mapping':
            totalMappingPages = totalPages;
            break;
    }
}

// Generic page navigation
function goToPage(tableType, page) {
    if (page < 1) return;

    const totalPages = getTotalPages(tableType);
    if (page > totalPages) return;

    loadTableData(tableType, page);
}

// Get total pages for table type
function getTotalPages(tableType) {
    switch (tableType) {
        case 'table': return totalTablePages;
        case 'adjacency': return totalAdjacencyPages;
        case 'mapping': return totalMappingPages;
        default: return 1;
    }
}

// Sort by column
function sortByColumn(tableType, column) {
    const config = tableConfigs[tableType];
    const sortField = document.getElementById(config.sortField);
    const sortDirection = document.getElementById(config.sortDirection);

    if (sortField) sortField.value = column;

    // Toggle direction if same column
    if (sortDirection && sortField.value === column) {
        sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc';
    }

    loadTableData(tableType, 1);
}

// Table-specific functions
function searchTable() { loadTableData('table', 1); }
function filterTable() { loadTableData('table', 1); }
function clearTableFilters() {
    document.getElementById('tableSearch').value = '';
    document.getElementById('tableFilterField').value = '';
    document.getElementById('tableFilterValue').value = '';
    document.getElementById('tableSortField').value = '';
    loadTableData('table', 1);
}
function sortTable() { loadTableData('table', 1); }
function changeTablePageSize() { loadTableData('table', 1); }
function goToTablePage(page) { goToPage('table', page); }

// Adjacency-specific functions
function searchAdjacencyTable() { loadTableData('adjacency', 1); }
function filterAdjacencyTable() { loadTableData('adjacency', 1); }
function clearAdjacencyFilters() {
    document.getElementById('adjacencySearch').value = '';
    document.getElementById('adjacencyFilterField').value = '';
    document.getElementById('adjacencyFilterValue').value = '';
    document.getElementById('adjacencySortField').value = '';
    loadTableData('adjacency', 1);
}
function sortAdjacencyTable() { loadTableData('adjacency', 1); }
function changeAdjacencyPageSize() { loadTableData('adjacency', 1); }
function goToAdjacencyPage(page) { goToPage('adjacency', page); }

// Mapping-specific functions
function searchMappingTable() { loadTableData('mapping', 1); }
function filterMappingTable() { loadTableData('mapping', 1); }
function clearMappingFilters() {
    document.getElementById('mappingSearch').value = '';
    document.getElementById('mappingFilterField').value = '';
    document.getElementById('mappingFilterValue').value = '';
    document.getElementById('mappingSortField').value = '';
    loadTableData('mapping', 1);
}
function sortMappingTable() { loadTableData('mapping', 1); }
function changeMappingPageSize() { loadTableData('mapping', 1); }
function goToMappingPage(page) { goToPage('mapping', page); }

function normalizeMappingWorkflow(workflow) {
    return workflow === 'rents' ? 'rents' : 'sales';
}

function normalizeMappingContext(context) {
    if (!context || typeof context !== 'object') return null;
    const zoneId = parseInt(context.zoneId, 10);
    if (!Number.isFinite(zoneId)) return null;
    return {
        zoneId,
        workflow: normalizeMappingWorkflow(context.workflow)
    };
}

function getMappingContextFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get(MAPPING_VIEW_QUERY_KEY) !== 'mapping') return null;

    const zoneId = parseInt(params.get(MAPPING_ZONE_QUERY_KEY) || '', 10);
    if (!Number.isFinite(zoneId)) return null;

    return {
        zoneId,
        workflow: normalizeMappingWorkflow(params.get(MAPPING_WORKFLOW_QUERY_KEY))
    };
}

function updateMappingUrlContext(context) {
    const params = new URLSearchParams(window.location.search);
    params.set(MAPPING_VIEW_QUERY_KEY, 'mapping');

    if (context && Number.isFinite(Number(context.zoneId))) {
        params.set(MAPPING_ZONE_QUERY_KEY, String(context.zoneId));
        params.set(MAPPING_WORKFLOW_QUERY_KEY, normalizeMappingWorkflow(context.workflow));
    } else {
        params.delete(MAPPING_ZONE_QUERY_KEY);
        params.delete(MAPPING_WORKFLOW_QUERY_KEY);
    }

    const query = params.toString();
    const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    window.history.replaceState({}, '', nextUrl);
}

function applyMappingZoneContextToUi(context) {
    if (typeof window.applyMappingZoneContext === 'function') {
        window.applyMappingZoneContext(context || null);
    }
}

// Initialize table when data is loaded
function initializeTables() {
    // Load table data when entering table view
    if (window.hasData) {
        loadTableData('table', 1);
    }

    const mappingContext = getMappingContextFromUrl();
    if (mappingContext) {
        showMappingView(mappingContext);
    }
}

// Enhanced table view click handler that loads data
function handleTableViewClick() {
    document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-toggle button').forEach(el => el.classList.remove('active'));
    document.getElementById('tableView').classList.add('active');
    document.getElementById('tableViewBtn').classList.add('active');

    // Load table data
    if (window.hasData) {
        loadTableData('table', 1);
    }
}

// Enhanced adjacency view handler
function showAdjacencyView() {
    document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-toggle button').forEach(el => el.classList.remove('active'));
    document.getElementById('adjacencyView').classList.add('active');
    document.getElementById('adjacencyViewBtn').classList.add('active');

    // Load adjacency data if available
    loadTableData('adjacency', 1);
}

// Enhanced mapping view handler
function showMappingView(options) {
    document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-toggle button').forEach(el => el.classList.remove('active'));
    document.getElementById('mappingView').classList.add('active');
    document.getElementById('mappingViewBtn').classList.add('active');

    const contextFromArgs = normalizeMappingContext(options);
    const contextFromUrl = getMappingContextFromUrl();
    const resolvedContext = contextFromArgs || contextFromUrl;
    window.mappingViewContext = resolvedContext;

    updateMappingUrlContext(resolvedContext);
    applyMappingZoneContextToUi(resolvedContext);

    // Load mapping data if available
    loadTableData('mapping', 1);

    return resolvedContext;
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', initializeTables);
