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
                const detail = errorData.detail;
                let message;
                if (!detail) {
                    message = `HTTP ${response.status}`;
                } else if (typeof detail === 'string') {
                    message = detail;
                } else if (Array.isArray(detail)) {
                    // Pydantic 422 validation errors: [{loc, msg, type}, ...]
                    message = detail.map(e => e.msg || JSON.stringify(e)).join('; ');
                } else {
                    message = JSON.stringify(detail);
                }
                throw new Error(message);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let summary = null;

            const handleLine = (line) => {
                if (!line.trim()) return;
                try {
                    const event = JSON.parse(line);
                    summary = this._handleEvent(event, { onLayer, onProgress, onComplete, onError });
                } catch (parseError) {
                    console.warn('[ProgressiveLoader] Failed to parse event:', line, parseError);
                }
            };

            while (true) {
                const { done, value } = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

                // Process complete NDJSON lines. A stream chunk is not a line
                // boundary, so retain the tail until a newline arrives.
                let newlineIndex;
                while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
                    const line = buffer.slice(0, newlineIndex);
                    buffer = buffer.slice(newlineIndex + 1);
                    handleLine(line);
                }
                if (done) break;
            }

            // Process any remaining buffer
            if (buffer.trim()) {
                handleLine(buffer);
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
                // Heartbeats carry only elapsed_seconds -- no file identity.
                if (event.file_path === undefined) break;
                console.log(`[ProgressiveLoader] Loading file ${event.file_index + 1}: ${event.file_path}`);
                callbacks.onProgress(event.file_index, null, event.file_path, 'loading');
                break;

            case 'layer':
                console.log(`[ProgressiveLoader] Loaded ${event.layer_name} (${event.feature_count} features)`);
                this.loadedLayers.push(event.layer_name);
                if (event.total_files && event.completed_files) {
                    callbacks.onProgress(
                        event.completed_files - 1,
                        event.total_files,
                        event.layer_name,
                        'loaded'
                    );
                }
                callbacks.onLayer(event.layer_name, event.geojson, event.feature_count, event.file_index, event.layer_type || 'map');
                break;

            case 'error':
                console.warn(`[ProgressiveLoader] Error loading ${event.file_path}: ${event.error}`);
                if (event.total_files && event.completed_files) {
                    callbacks.onProgress(
                        event.completed_files - 1,
                        event.total_files,
                        event.file_path,
                        'error'
                    );
                }
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
    _startedAt: 0,
    _timer: null,

    /**
     * Show progress overlay
     * @param {number} totalFiles - Total number of files to load
     */
    show(totalFiles) {
        this.hide(); // Remove any existing overlay

        const t = window.t || (key => key);
        const overlay = document.createElement('div');
        overlay.id = 'progressive-load-overlay';
        overlay.innerHTML = `
            <div class="progressive-load-panel">
                <div class="progressive-load-header">
                    <span class="progressive-load-title">${t('Loading Cadastral Data')}</span>
                    <button class="progressive-load-cancel" title="Cancel">&#x2715;</button>
                </div>
                <div class="progressive-load-bar-container">
                    <div class="progressive-load-bar" style="width: 0%"></div>
                </div>
                <div class="progressive-load-status">${t('Preparing...')}</div>
                <div class="progressive-load-details">
                    <span class="progressive-load-files">${t('{n} / {m} files').replace('{n}', 0).replace('{m}', totalFiles)}</span>
                    <span class="progressive-load-elapsed">0s</span>
                    <span class="progressive-load-features">${t('0 features')}</span>
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

        // A single large file produces no server events at all while it is
        // being read and serialized -- and because that work is CPU-bound and
        // holds the GIL, server-side heartbeats cannot be relied upon either.
        // Tick locally so the panel always shows the load is still alive.
        this._startedAt = Date.now();
        clearInterval(this._timer);
        this._timer = setInterval(() => this._tickElapsed(), 1000);
    },

    /** Update the locally-measured elapsed time. */
    _tickElapsed() {
        if (!this._overlay) {
            clearInterval(this._timer);
            this._timer = null;
            return;
        }
        const el = this._overlay.querySelector('.progressive-load-elapsed');
        if (el) el.textContent = `${Math.round((Date.now() - this._startedAt) / 1000)}s`;
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

        const t = window.t || (key => key);
        if (total) {
            const currentCount = status === 'starting' ? 0 : current + 1;
            const pct = Math.round((currentCount / total) * 100);
            const bar = this._overlay.querySelector('.progressive-load-bar');
            if (bar) bar.style.width = `${pct}%`;

            const files = this._overlay.querySelector('.progressive-load-files');
            if (files) files.textContent = t('{n} / {m} files').replace('{n}', currentCount).replace('{m}', total);
        }

        if (fileName) {
            const statusEl = this._overlay.querySelector('.progressive-load-status');
            if (statusEl) statusEl.textContent = t('Loading: {file}').replace('{file}', fileName);
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

        clearInterval(this._timer);
        this._timer = null;

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
        clearInterval(this._timer);
        this._timer = null;
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
