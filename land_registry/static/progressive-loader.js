/**
 * Progressive Loader for cadastral file loading
 *
 * Streams NDJSON from the backend and renders layers incrementally
 * as each file completes loading, providing real-time progress feedback
 * without requiring a page reload.
 */

const ProgressiveLoader = {
    // State
    isLoading: false,
    abortController: null,
    loadedLayers: [],

    /**
     * Load cadastral files progressively with streaming
     * @param {string[]} filePaths - Array of file paths to load
     * @param {Object} options - Loading options
     * @param {Function} options.onLayer - Callback when a layer is ready: (layerName, geojson, featureCount, fileIndex) => void
     * @param {Function} options.onProgress - Progress callback: (fileIndex, totalFiles, fileName, status) => void
     * @param {Function} options.onComplete - Completion callback: (summary) => void
     * @param {Function} options.onError - Error callback: (fileIndex, fileName, error) => void
     * @param {boolean} options.clearExisting - Clear existing data (default true)
     * @returns {Promise<Object>} Final summary
     */
    async load(filePaths, options = {}) {
        if (this.isLoading) {
            console.warn('[ProgressiveLoader] Already loading, aborting previous request');
            this.abort();
        }

        this.isLoading = true;
        this.abortController = new AbortController();
        this.loadedLayers = [];

        const {
            onLayer = () => {},
            onProgress = () => {},
            onComplete = () => {},
            onError = () => {},
            clearExisting = true,
        } = options;

        try {
            const response = await fetch('/api/v1/load-cadastral-files-stream/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_paths: filePaths,
                    clear_existing: clearExisting,
                }),
                signal: this.abortController.signal,
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let summary = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // Process complete NDJSON lines
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                for (const line of lines) {
                    if (!line.trim()) continue;

                    try {
                        const event = JSON.parse(line);
                        summary = this._handleEvent(event, { onLayer, onProgress, onComplete, onError });
                    } catch (parseError) {
                        console.warn('[ProgressiveLoader] Failed to parse event:', line, parseError);
                    }
                }
            }

            // Process any remaining buffer
            if (buffer.trim()) {
                try {
                    const event = JSON.parse(buffer);
                    summary = this._handleEvent(event, { onLayer, onProgress, onComplete, onError });
                } catch (e) {
                    // ignore incomplete data
                }
            }

            return summary;

        } catch (error) {
            if (error.name === 'AbortError') {
                console.log('[ProgressiveLoader] Loading aborted by user');
                return null;
            }
            console.error('[ProgressiveLoader] Streaming error:', error);
            throw error;
        } finally {
            this.isLoading = false;
            this.abortController = null;
        }
    },

    /**
     * Handle a single NDJSON event
     * @private
     */
    _handleEvent(event, callbacks) {
        switch (event.event) {
            case 'start':
                console.log(`[ProgressiveLoader] Starting load of ${event.total_files} files`);
                callbacks.onProgress(0, event.total_files, '', 'starting');
                break;

            case 'progress':
                console.log(`[ProgressiveLoader] Loading file ${event.file_index + 1}: ${event.file_path}`);
                callbacks.onProgress(event.file_index, null, event.file_path, 'loading');
                break;

            case 'layer':
                console.log(`[ProgressiveLoader] Loaded ${event.layer_name} (${event.feature_count} features)`);
                this.loadedLayers.push(event.layer_name);
                callbacks.onLayer(event.layer_name, event.geojson, event.feature_count, event.file_index);
                break;

            case 'error':
                console.warn(`[ProgressiveLoader] Error loading ${event.file_path}: ${event.error}`);
                callbacks.onError(event.file_index, event.file_path, event.error);
                break;

            case 'complete':
                console.log(`[ProgressiveLoader] Complete: ${event.total_layers} layers, ${event.total_features} features in ${event.load_time_seconds}s`);
                callbacks.onComplete(event);
                return event;
        }
        return null;
    },

    /**
     * Abort current loading operation
     */
    abort() {
        if (this.abortController) {
            this.abortController.abort();
            this.isLoading = false;
        }
    },

    /**
     * Get list of loaded layer names
     * @returns {string[]}
     */
    getLoadedLayers() {
        return [...this.loadedLayers];
    }
};

// ============================================================================
// Progress UI - renders a progress overlay during loading
// ============================================================================

const ProgressUI = {
    _overlay: null,

    /**
     * Show progress overlay
     * @param {number} totalFiles - Total number of files to load
     */
    show(totalFiles) {
        this.hide(); // Remove any existing overlay

        const overlay = document.createElement('div');
        overlay.id = 'progressive-load-overlay';
        overlay.innerHTML = `
            <div class="progressive-load-panel">
                <div class="progressive-load-header">
                    <span class="progressive-load-title">Loading Cadastral Data</span>
                    <button class="progressive-load-cancel" title="Cancel">&#x2715;</button>
                </div>
                <div class="progressive-load-bar-container">
                    <div class="progressive-load-bar" style="width: 0%"></div>
                </div>
                <div class="progressive-load-status">Preparing...</div>
                <div class="progressive-load-details">
                    <span class="progressive-load-files">0 / ${totalFiles} files</span>
                    <span class="progressive-load-features">0 features</span>
                </div>
                <div class="progressive-load-log"></div>
            </div>
        `;

        // Cancel button handler
        overlay.querySelector('.progressive-load-cancel').addEventListener('click', () => {
            ProgressiveLoader.abort();
            this.hide();
        });

        document.body.appendChild(overlay);
        this._overlay = overlay;
    },

    /**
     * Update progress bar and status
     * @param {number} current - Current file index (0-based)
     * @param {number} total - Total files
     * @param {string} fileName - Current file name
     * @param {string} status - Status text
     */
    updateProgress(current, total, fileName, status) {
        if (!this._overlay) return;

        if (total) {
            const pct = Math.round(((current + 1) / total) * 100);
            const bar = this._overlay.querySelector('.progressive-load-bar');
            if (bar) bar.style.width = `${pct}%`;

            const files = this._overlay.querySelector('.progressive-load-files');
            if (files) files.textContent = `${current + 1} / ${total} files`;
        }

        if (fileName) {
            const statusEl = this._overlay.querySelector('.progressive-load-status');
            if (statusEl) statusEl.textContent = `Loading: ${fileName}`;
        }
    },

    /**
     * Update feature count display
     * @param {number} count - Total features loaded so far
     */
    updateFeatureCount(count) {
        if (!this._overlay) return;
        const el = this._overlay.querySelector('.progressive-load-features');
        if (el) el.textContent = `${count.toLocaleString()} features`;
    },

    /**
     * Add a log entry
     * @param {string} message - Log message
     * @param {string} type - 'success', 'error', or 'info'
     */
    addLog(message, type = 'info') {
        if (!this._overlay) return;
        const log = this._overlay.querySelector('.progressive-load-log');
        if (!log) return;

        const entry = document.createElement('div');
        entry.className = `progressive-load-log-entry progressive-load-log-${type}`;
        entry.textContent = message;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    },

    /**
     * Show completion state
     * @param {Object} summary - Completion summary from server
     */
    showComplete(summary) {
        if (!this._overlay) return;

        const bar = this._overlay.querySelector('.progressive-load-bar');
        if (bar) {
            bar.style.width = '100%';
            bar.classList.add('progressive-load-bar-complete');
        }

        const statusEl = this._overlay.querySelector('.progressive-load-status');
        if (statusEl) {
            statusEl.textContent = `Loaded ${summary.total_layers} layers (${summary.total_features.toLocaleString()} features) in ${summary.load_time_seconds}s`;
        }

        // Auto-hide after 2 seconds
        setTimeout(() => this.hide(), 2000);
    },

    /**
     * Hide and remove the overlay
     */
    hide() {
        if (this._overlay) {
            this._overlay.remove();
            this._overlay = null;
        }
    }
};

// Export to window
if (typeof window !== 'undefined') {
    window.ProgressiveLoader = ProgressiveLoader;
    window.ProgressUI = ProgressUI;
}
