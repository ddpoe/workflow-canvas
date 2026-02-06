/**
 * DMGTLAS Workflow Builder - History View Logic
 * 
 * Handles the run history display with path view and descendant tree.
 */

// =============================================================================
// Global State for History
// =============================================================================

let allRuns = [];
let filteredRuns = [];
let selectedRun = null;
let currentView = 'paths'; // 'paths' or 'descendants'

// =============================================================================
// Mock Run Data
// =============================================================================

function generateMockRuns() {
    /**
     * Generate mock run history data for testing.
     */
    
    const dataSources = ['Pa16C.h5ad', 'MCF7.h5ad', 'HeLa.h5ad', 'SKBR3.h5ad'];
    const now = Date.now();
    const hour = 60 * 60 * 1000;
    const day = 24 * hour;
    
    const runs = [];
    let runId = 1;
    
    // Generate LoadAnnData runs (entry points)
    dataSources.forEach((ds, dsIdx) => {
        const loadRun = {
            id: `run_${String(runId++).padStart(3, '0')}`,
            module: 'DataLoading',
            method: 'LoadAnnData',
            version: '1.0.0',
            timestamp: now - (dsIdx * 2 * day) - Math.random() * day,
            duration: 2.5 + Math.random() * 2,
            status: 'success',
            inputs: { file_path: `/data/${ds}` },
            outputs: { adata: `adata_${ds.replace('.h5ad', '')}` },
            dataSource: ds,
            parentRunId: null,
            template: [
                'Read h5ad file from disk',
                'Validate AnnData structure',
                'Index cells and genes'
            ]
        };
        runs.push(loadRun);
        
        // Generate G0MO3 runs from this load
        const nComponents = [3, 5, 7];
        nComponents.forEach((n, nIdx) => {
            // v1.3.0 runs
            const g0moRun130 = {
                id: `run_${String(runId++).padStart(3, '0')}`,
                module: 'CellCyclePhaseAnnotations',
                method: 'G0MO3PhasePrediction',
                version: '1.3.0',
                timestamp: loadRun.timestamp + hour + (nIdx * 0.5 * hour),
                duration: 45 + Math.random() * 30,
                status: 'success',
                inputs: { 
                    adata: loadRun.outputs.adata,
                    n_components: n 
                },
                outputs: { 
                    adata: `adata_${ds.replace('.h5ad', '')}_g0mo_n${n}`,
                    phase_labels: `labels_${ds.replace('.h5ad', '')}_g0mo_n${n}`
                },
                dataSource: ds,
                parentRunId: loadRun.id,
                template: [
                    'Log-normalize expression matrix',
                    'Fit 2D Gaussian Mixture Model',
                    'Assign G0/M/O3 phase labels',
                    'Store labels in adata.obs'
                ]
            };
            runs.push(g0moRun130);
            
            // Generate downstream runs for some
            if (nIdx === 0 || (dsIdx === 0 && nIdx < 2)) {
                // RandomForest runs
                [50, 100].forEach((nFeatures, fIdx) => {
                    if (dsIdx > 1 && fIdx > 0) return; // Less runs for later data sources
                    
                    const rfRun = {
                        id: `run_${String(runId++).padStart(3, '0')}`,
                        module: 'FeatureSelection',
                        method: 'RandomForestFS',
                        version: '2.0.0',
                        timestamp: g0moRun130.timestamp + hour + (fIdx * 0.3 * hour),
                        duration: 120 + Math.random() * 60,
                        status: 'success',
                        inputs: {
                            adata: g0moRun130.outputs.adata,
                            labels: g0moRun130.outputs.phase_labels,
                            n_features: nFeatures
                        },
                        outputs: {
                            feature_set: `features_rf_${nFeatures}`,
                            importance_scores: `importance_rf_${nFeatures}`
                        },
                        dataSource: ds,
                        parentRunId: g0moRun130.id,
                        template: [
                            'Split data into train/test',
                            'Train Random Forest classifier',
                            'Calculate feature importances',
                            'Select top N features'
                        ]
                    };
                    runs.push(rfRun);
                    
                    // Visualization runs
                    if (fIdx === 0) {
                        const umapRun = {
                            id: `run_${String(runId++).padStart(3, '0')}`,
                            module: 'Visualization',
                            method: 'UMAPPlot',
                            version: '1.0.0',
                            timestamp: rfRun.timestamp + 0.5 * hour,
                            duration: 30 + Math.random() * 20,
                            status: 'success',
                            inputs: {
                                adata: rfRun.inputs.adata,
                                color_by: rfRun.inputs.labels,
                                n_neighbors: 15
                            },
                            outputs: {
                                figure: `umap_${ds.replace('.h5ad', '')}`,
                                adata: `adata_${ds.replace('.h5ad', '')}_umap`
                            },
                            dataSource: ds,
                            parentRunId: rfRun.id,
                            template: [
                                'Compute PCA if not present',
                                'Build neighbor graph',
                                'Compute UMAP embedding',
                                'Generate scatter plot'
                            ]
                        };
                        runs.push(umapRun);
                    }
                });
                
                // DelveFS for some runs
                if (dsIdx === 0 && nIdx === 0) {
                    const delveRun = {
                        id: `run_${String(runId++).padStart(3, '0')}`,
                        module: 'FeatureSelection',
                        method: 'DelveFS',
                        version: '1.0.0',
                        timestamp: g0moRun130.timestamp + 1.5 * hour,
                        duration: 180 + Math.random() * 60,
                        status: 'success',
                        inputs: {
                            adata: g0moRun130.outputs.adata,
                            n_features: 100
                        },
                        outputs: {
                            feature_set: 'features_delve_100'
                        },
                        dataSource: ds,
                        parentRunId: g0moRun130.id,
                        template: [
                            'Construct cell-cell graph',
                            'Compute feature dynamics',
                            'Rank features by trajectory relevance',
                            'Select top features'
                        ]
                    };
                    runs.push(delveRun);
                    
                    // Heatmap from Delve
                    const heatmapRun = {
                        id: `run_${String(runId++).padStart(3, '0')}`,
                        module: 'Visualization',
                        method: 'FeatureHeatmap',
                        version: '1.0.0',
                        timestamp: delveRun.timestamp + 0.3 * hour,
                        duration: 15 + Math.random() * 10,
                        status: 'success',
                        inputs: {
                            adata: delveRun.inputs.adata,
                            features: delveRun.outputs.feature_set,
                            group_by: g0moRun130.outputs.phase_labels
                        },
                        outputs: {
                            figure: 'heatmap_delve_phases'
                        },
                        dataSource: ds,
                        parentRunId: delveRun.id,
                        template: [
                            'Subset to selected features',
                            'Group cells by labels',
                            'Compute mean expression per group',
                            'Generate clustered heatmap'
                        ]
                    };
                    runs.push(heatmapRun);
                }
            }
        });
        
        // Add some v1.2.0 runs (older version)
        if (dsIdx === 0) {
            const g0moRun120 = {
                id: `run_${String(runId++).padStart(3, '0')}`,
                module: 'CellCyclePhaseAnnotations',
                method: 'G0MO3PhasePrediction',
                version: '1.2.0',
                timestamp: loadRun.timestamp + 0.5 * hour,
                duration: 50 + Math.random() * 20,
                status: 'success',
                inputs: {
                    adata: loadRun.outputs.adata,
                    n_components: 3
                },
                outputs: {
                    adata: `adata_${ds.replace('.h5ad', '')}_g0mo_v120`,
                    phase_labels: `labels_${ds.replace('.h5ad', '')}_g0mo_v120`
                },
                dataSource: ds,
                parentRunId: loadRun.id,
                template: [
                    'Normalize expression matrix',
                    'Fit 2D Gaussian Mixture Model',
                    'Assign phase labels'
                ]
            };
            runs.push(g0moRun120);
        }
        
        // Add scVelo runs
        if (dsIdx < 2) {
            const scveloRun = {
                id: `run_${String(runId++).padStart(3, '0')}`,
                module: 'CellCyclePhaseAnnotations',
                method: 'scVeloPhasePrediction',
                version: '2.1.0',
                timestamp: loadRun.timestamp + 3 * hour,
                duration: 90 + Math.random() * 45,
                status: 'success',
                inputs: {
                    adata: loadRun.outputs.adata,
                    min_shared_counts: 20
                },
                outputs: {
                    adata: `adata_${ds.replace('.h5ad', '')}_scvelo`,
                    phase_labels: `labels_${ds.replace('.h5ad', '')}_scvelo`
                },
                dataSource: ds,
                parentRunId: loadRun.id,
                template: [
                    'Filter genes by shared counts',
                    'Compute moments',
                    'Recover dynamics',
                    'Assign velocity-based phases'
                ]
            };
            runs.push(scveloRun);
        }
    });
    
    // Add one failed run
    runs.push({
        id: `run_${String(runId++).padStart(3, '0')}`,
        module: 'FeatureSelection',
        method: 'RandomForestFS',
        version: '2.0.0',
        timestamp: now - 5 * hour,
        duration: 12,
        status: 'failed',
        error: 'ValueError: n_features must be less than total features (got 500, max 487)',
        inputs: {
            adata: 'adata_Pa16C_g0mo_n3',
            labels: 'labels_Pa16C_g0mo_n3',
            n_features: 500
        },
        outputs: {},
        dataSource: 'Pa16C.h5ad',
        parentRunId: 'run_002',
        template: []
    });
    
    return runs;
}

// =============================================================================
// History View Initialization
// =============================================================================

async function initHistory() {
    /**
     * Initialize the history view.
     */
    
    // Generate mock data
    allRuns = generateMockRuns();
    
    // Populate filter dropdowns
    populateFilters();
    
    // Setup event listeners
    setupHistoryEventListeners();
    
    // Initial render
    applyFilters();
    renderHistoryTree();
    renderPathsView();
}

function populateFilters() {
    /**
     * Populate filter dropdowns with available options.
     */
    
    // Data sources
    const dataSources = [...new Set(allRuns.map(r => r.dataSource))];
    const dsSelect = document.getElementById('filter-datasource');
    dataSources.forEach(ds => {
        const option = document.createElement('option');
        option.value = ds;
        option.textContent = ds;
        dsSelect.appendChild(option);
    });
    
    // Modules
    const modules = [...new Set(allRuns.map(r => r.module))];
    const moduleSelect = document.getElementById('filter-module');
    modules.forEach(mod => {
        const option = document.createElement('option');
        option.value = mod;
        option.textContent = mod;
        moduleSelect.appendChild(option);
    });
}

function setupHistoryEventListeners() {
    /**
     * Setup event listeners for history view.
     */
    
    // Filter changes
    document.getElementById('filter-datasource').addEventListener('change', () => {
        applyFilters();
        renderPathsView();
    });
    
    document.getElementById('filter-time').addEventListener('change', () => {
        applyFilters();
        renderPathsView();
    });
    
    document.getElementById('filter-module').addEventListener('change', () => {
        applyFilters();
        renderPathsView();
    });
    
    document.getElementById('filter-search').addEventListener('input', (e) => {
        applyFilters();
        renderPathsView();
    });
    
    // View toggle
    document.querySelectorAll('#view-toggle .view-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const view = e.target.dataset.view;
            switchHistoryPanel(view);
        });
    });
    
    // Detail panel close
    document.getElementById('close-detail').addEventListener('click', () => {
        hideDetailPanel();
    });
    
    // Detail panel actions
    document.getElementById('btn-show-descendants').addEventListener('click', () => {
        if (selectedRun) {
            showDescendants(selectedRun.id);
        }
    });
    
    document.getElementById('btn-load-canvas').addEventListener('click', () => {
        if (selectedRun) {
            loadRunToCanvas(selectedRun);
        }
    });
}

// =============================================================================
// Filtering
// =============================================================================

function applyFilters() {
    /**
     * Apply all filters to the run list.
     */
    
    const dsFilter = document.getElementById('filter-datasource').value;
    const timeFilter = document.getElementById('filter-time').value;
    const moduleFilter = document.getElementById('filter-module').value;
    const searchFilter = document.getElementById('filter-search').value.toLowerCase();
    
    const now = Date.now();
    const timeRanges = {
        '1h': 60 * 60 * 1000,
        '24h': 24 * 60 * 60 * 1000,
        '7d': 7 * 24 * 60 * 60 * 1000,
        '30d': 30 * 24 * 60 * 60 * 1000,
        'all': Infinity
    };
    
    filteredRuns = allRuns.filter(run => {
        // Data source filter
        if (dsFilter && run.dataSource !== dsFilter) return false;
        
        // Time filter
        const timeLimit = timeRanges[timeFilter] || Infinity;
        if (now - run.timestamp > timeLimit) return false;
        
        // Module filter
        if (moduleFilter && run.module !== moduleFilter) return false;
        
        // Search filter
        if (searchFilter) {
            const searchText = `${run.id} ${run.method} ${run.module} ${JSON.stringify(run.inputs)}`.toLowerCase();
            if (!searchText.includes(searchFilter)) return false;
        }
        
        return true;
    });
    
    // Update tree
    renderHistoryTree();
}

// =============================================================================
// History Tree (Sidebar)
// =============================================================================

function renderHistoryTree() {
    /**
     * Render the sidebar tree grouped by Module > Method > Version.
     */
    
    const treeContainer = document.getElementById('history-tree');
    treeContainer.innerHTML = '';
    
    // Group runs
    const grouped = {};
    filteredRuns.forEach(run => {
        if (!grouped[run.module]) grouped[run.module] = {};
        if (!grouped[run.module][run.method]) grouped[run.module][run.method] = {};
        if (!grouped[run.module][run.method][run.version]) {
            grouped[run.module][run.method][run.version] = [];
        }
        grouped[run.module][run.method][run.version].push(run);
    });
    
    // Render tree
    for (const [moduleName, methods] of Object.entries(grouped)) {
        const moduleEl = document.createElement('div');
        moduleEl.className = 'tree-module';
        
        const moduleHeader = document.createElement('div');
        moduleHeader.className = 'tree-header module-header';
        moduleHeader.innerHTML = `<span class="toggle">▼</span> 📁 ${moduleName}`;
        moduleHeader.addEventListener('click', () => {
            moduleHeader.classList.toggle('collapsed');
            moduleContent.style.display = moduleHeader.classList.contains('collapsed') ? 'none' : 'block';
            moduleHeader.querySelector('.toggle').textContent = 
                moduleHeader.classList.contains('collapsed') ? '▶' : '▼';
        });
        
        const moduleContent = document.createElement('div');
        moduleContent.className = 'tree-content';
        
        for (const [methodName, versions] of Object.entries(methods)) {
            const methodEl = document.createElement('div');
            methodEl.className = 'tree-method';
            
            const methodHeader = document.createElement('div');
            methodHeader.className = 'tree-header method-header';
            methodHeader.innerHTML = `<span class="toggle">▼</span> 📄 ${methodName}`;
            methodHeader.addEventListener('click', (e) => {
                e.stopPropagation();
                methodHeader.classList.toggle('collapsed');
                methodContent.style.display = methodHeader.classList.contains('collapsed') ? 'none' : 'block';
                methodHeader.querySelector('.toggle').textContent = 
                    methodHeader.classList.contains('collapsed') ? '▶' : '▼';
            });
            
            const methodContent = document.createElement('div');
            methodContent.className = 'tree-content';
            
            for (const [version, runs] of Object.entries(versions)) {
                const versionEl = document.createElement('div');
                versionEl.className = 'tree-version';
                versionEl.innerHTML = `
                    <span class="version-badge">v${version}</span>
                    <span class="run-count">${runs.length} runs</span>
                `;
                versionEl.addEventListener('click', (e) => {
                    e.stopPropagation();
                    filterByMethodVersion(moduleName, methodName, version);
                });
                
                methodContent.appendChild(versionEl);
            }
            
            methodEl.appendChild(methodHeader);
            methodEl.appendChild(methodContent);
            moduleContent.appendChild(methodEl);
        }
        
        moduleEl.appendChild(moduleHeader);
        moduleEl.appendChild(moduleContent);
        treeContainer.appendChild(moduleEl);
    }
}

function filterByMethodVersion(module, method, version) {
    /**
     * Filter to show only runs of a specific method version.
     */
    
    // Update breadcrumb
    updateBreadcrumb([
        { label: 'All Runs', level: 'root' },
        { label: `${method} v${version}`, level: 'version' }
    ]);
    
    // Filter runs
    const methodRuns = filteredRuns.filter(r => 
        r.module === module && r.method === method && r.version === version
    );
    
    // Render paths for these runs
    renderPathsForRuns(methodRuns);
}

// =============================================================================
// Path View
// =============================================================================

function renderPathsView() {
    /**
     * Render the path view showing complete run lineages.
     */
    
    // Find all terminal runs (no children)
    const runIds = new Set(filteredRuns.map(r => r.id));
    const parentIds = new Set(filteredRuns.map(r => r.parentRunId).filter(Boolean));
    
    // Terminal runs are those not referenced as parents
    const terminalRuns = filteredRuns.filter(r => {
        // Check if this run has any children in filtered set
        return !filteredRuns.some(other => other.parentRunId === r.id);
    });
    
    renderPathsForRuns(terminalRuns);
}

function renderPathsForRuns(runs) {
    /**
     * Render path view for a set of runs.
     */
    
    const pathsList = document.getElementById('paths-list');
    pathsList.innerHTML = '';
    
    if (runs.length === 0) {
        pathsList.innerHTML = '<div class="empty-state">No runs match the current filters</div>';
        return;
    }
    
    // Build paths for each run
    runs.forEach((run, idx) => {
        const path = buildPathToRoot(run);
        
        const pathEl = document.createElement('div');
        pathEl.className = 'path-row';
        
        // Path label
        const pathLabel = document.createElement('div');
        pathLabel.className = 'path-label';
        pathLabel.textContent = `Path ${idx + 1}`;
        
        // Path nodes
        const pathNodes = document.createElement('div');
        pathNodes.className = 'path-nodes';
        
        path.forEach((runInPath, nodeIdx) => {
            // Node
            const nodeEl = document.createElement('div');
            nodeEl.className = `path-node ${runInPath.status}`;
            nodeEl.innerHTML = `
                <div class="node-method">${runInPath.method}</div>
                <div class="node-params">${formatParams(runInPath.inputs)}</div>
                <div class="node-id">${runInPath.id}</div>
            `;
            nodeEl.style.borderLeftColor = getModuleColor(runInPath.module);
            
            nodeEl.addEventListener('click', () => {
                showRunDetail(runInPath);
            });
            
            pathNodes.appendChild(nodeEl);
            
            // Arrow (except after last node)
            if (nodeIdx < path.length - 1) {
                const arrow = document.createElement('div');
                arrow.className = 'path-arrow';
                arrow.textContent = '→';
                pathNodes.appendChild(arrow);
            }
        });
        
        pathEl.appendChild(pathLabel);
        pathEl.appendChild(pathNodes);
        pathsList.appendChild(pathEl);
    });
}

function buildPathToRoot(run) {
    /**
     * Build the complete path from root to this run.
     */
    
    const path = [run];
    let current = run;
    
    while (current.parentRunId) {
        const parent = allRuns.find(r => r.id === current.parentRunId);
        if (parent) {
            path.unshift(parent);
            current = parent;
        } else {
            break;
        }
    }
    
    return path;
}

function formatParams(inputs) {
    /**
     * Format input parameters for display.
     */
    
    const params = Object.entries(inputs)
        .filter(([k, v]) => typeof v !== 'object' && !String(v).startsWith('adata_') && !String(v).startsWith('labels_'))
        .map(([k, v]) => `${k}=${v}`)
        .slice(0, 2); // Max 2 params shown
    
    return params.length > 0 ? params.join(', ') : '';
}

// =============================================================================
// Descendant Tree View
// =============================================================================

function showDescendants(runId) {
    /**
     * Show the descendant tree for a specific run.
     */
    
    const run = allRuns.find(r => r.id === runId);
    if (!run) return;
    
    // Switch to descendants view
    switchHistoryPanel('descendants');
    
    // Update breadcrumb
    updateBreadcrumb([
        { label: 'All Runs', level: 'root' },
        { label: `${run.method} (${run.id})`, level: 'run' },
        { label: 'Descendants', level: 'descendants' }
    ]);
    
    // Build and render tree
    const tree = buildDescendantTree(runId);
    renderDescendantTree(tree, run);
}

function buildDescendantTree(runId) {
    /**
     * Build a tree of all descendants from a run.
     */
    
    const children = allRuns.filter(r => r.parentRunId === runId);
    
    return children.map(child => ({
        run: child,
        children: buildDescendantTree(child.id)
    }));
}

function renderDescendantTree(tree, rootRun) {
    /**
     * Render the descendant tree.
     */
    
    const container = document.getElementById('descendant-tree');
    container.innerHTML = '';
    
    // Root node
    const rootEl = document.createElement('div');
    rootEl.className = 'desc-root';
    rootEl.innerHTML = `
        <div class="desc-node root-node" style="border-left-color: ${getModuleColor(rootRun.module)}">
            <div class="node-method">${rootRun.method} v${rootRun.version}</div>
            <div class="node-params">${formatParams(rootRun.inputs)}</div>
            <div class="node-id">${rootRun.id}</div>
        </div>
    `;
    container.appendChild(rootEl);
    
    // Descendants
    if (tree.length === 0) {
        container.innerHTML += '<div class="empty-state">No downstream runs found</div>';
        return;
    }
    
    const treeEl = document.createElement('div');
    treeEl.className = 'desc-tree';
    renderDescendantLevel(treeEl, tree, 0);
    container.appendChild(treeEl);
}

function renderDescendantLevel(container, nodes, depth) {
    /**
     * Recursively render a level of the descendant tree.
     */
    
    nodes.forEach((node, idx) => {
        const run = node.run;
        const isLast = idx === nodes.length - 1;
        
        const rowEl = document.createElement('div');
        rowEl.className = 'desc-row';
        rowEl.style.paddingLeft = `${depth * 30}px`;
        
        // Connector line
        const connector = document.createElement('div');
        connector.className = `desc-connector ${isLast ? 'last' : ''}`;
        connector.innerHTML = isLast ? '└─▶' : '├─▶';
        
        // Node
        const nodeEl = document.createElement('div');
        nodeEl.className = `desc-node ${run.status}`;
        nodeEl.style.borderLeftColor = getModuleColor(run.module);
        nodeEl.innerHTML = `
            <div class="node-method">${run.method} v${run.version}</div>
            <div class="node-params">${formatParams(run.inputs)}</div>
            <div class="node-id">${run.id}</div>
        `;
        
        nodeEl.addEventListener('click', () => {
            showRunDetail(run);
        });
        
        rowEl.appendChild(connector);
        rowEl.appendChild(nodeEl);
        container.appendChild(rowEl);
        
        // Recurse for children
        if (node.children.length > 0) {
            renderDescendantLevel(container, node.children, depth + 1);
        }
    });
}

// =============================================================================
// Detail Panel
// =============================================================================

function showRunDetail(run) {
    /**
     * Show the detail panel for a run.
     */
    
    selectedRun = run;
    
    const panel = document.getElementById('detail-panel');
    const title = document.getElementById('detail-title');
    const content = document.getElementById('detail-content');
    
    title.textContent = `${run.id}: ${run.method}`;
    
    const statusIcon = run.status === 'success' ? '✅' : '❌';
    const statusClass = run.status === 'success' ? 'success' : 'failed';
    
    content.innerHTML = `
        <div class="detail-section">
            <div class="detail-row">
                <span class="detail-label">Status:</span>
                <span class="detail-value ${statusClass}">${statusIcon} ${run.status}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Version:</span>
                <span class="detail-value">${run.version}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Executed:</span>
                <span class="detail-value">${new Date(run.timestamp).toLocaleString()}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Duration:</span>
                <span class="detail-value">${run.duration.toFixed(1)}s</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Data Source:</span>
                <span class="detail-value">${run.dataSource}</span>
            </div>
        </div>
        
        <div class="detail-section">
            <h4>Inputs</h4>
            <div class="detail-params">
                ${Object.entries(run.inputs).map(([k, v]) => `
                    <div class="param-row">
                        <span class="param-key">${k}:</span>
                        <span class="param-value">${v}</span>
                    </div>
                `).join('')}
            </div>
        </div>
        
        <div class="detail-section">
            <h4>Outputs</h4>
            <div class="detail-params">
                ${Object.entries(run.outputs).map(([k, v]) => `
                    <div class="param-row">
                        <span class="param-key">${k}:</span>
                        <span class="param-value">${v}</span>
                    </div>
                `).join('')}
            </div>
        </div>
        
        ${run.template && run.template.length > 0 ? `
        <div class="detail-section">
            <h4>Method Template (v${run.version})</h4>
            <ol class="template-steps">
                ${run.template.map(step => `<li>${step}</li>`).join('')}
            </ol>
        </div>
        ` : ''}
        
        ${run.error ? `
        <div class="detail-section error-section">
            <h4>Error</h4>
            <div class="error-message">${run.error}</div>
        </div>
        ` : ''}
    `;
    
    panel.classList.remove('hidden');
}

function hideDetailPanel() {
    /**
     * Hide the detail panel.
     */
    
    document.getElementById('detail-panel').classList.add('hidden');
    selectedRun = null;
}

// =============================================================================
// UI Helpers
// =============================================================================

function switchHistoryPanel(view) {
    /**
     * Switch between path view and descendant tree view.
     */
    
    currentView = view;
    
    // Update buttons
    document.querySelectorAll('#view-toggle .view-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });
    
    // Update panels
    document.querySelectorAll('.history-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `${view}-container`);
    });
}

function updateBreadcrumb(items) {
    /**
     * Update the breadcrumb navigation.
     */
    
    const breadcrumb = document.getElementById('history-breadcrumb');
    breadcrumb.innerHTML = items.map((item, idx) => {
        const isLast = idx === items.length - 1;
        return `
            <span class="breadcrumb-item ${isLast ? 'active' : ''}" data-level="${item.level}">
                ${item.label}
            </span>
            ${!isLast ? '<span class="breadcrumb-sep">›</span>' : ''}
        `;
    }).join('');
    
    // Add click handlers
    breadcrumb.querySelectorAll('.breadcrumb-item').forEach(item => {
        item.addEventListener('click', () => {
            if (item.dataset.level === 'root') {
                updateBreadcrumb([{ label: 'All Runs', level: 'root' }]);
                applyFilters();
                renderPathsView();
                switchHistoryPanel('paths');
            }
        });
    });
}

function loadRunToCanvas(run) {
    /**
     * Load a run's workflow to the builder canvas.
     */
    
    // Switch to builder view
    document.querySelector('.main-tab-btn[data-view="builder"]').click();
    
    // Build the full path
    const path = buildPathToRoot(run);
    
    // Clear canvas
    if (graph) {
        graph.clear();
    }
    
    // Add nodes for each step in path
    let lastNode = null;
    path.forEach((step, idx) => {
        const node = addNodeToGraph(step.module, step.method, 100 + idx * 250, 200);
        if (node) {
            // Set properties from run inputs
            for (const [key, value] of Object.entries(step.inputs)) {
                if (node.properties.hasOwnProperty(key)) {
                    node.properties[key] = value;
                }
            }
            
            // Connect to previous node if possible
            if (lastNode && node.inputs && node.inputs.length > 0) {
                // Find compatible output/input pair
                for (let o = 0; o < lastNode.outputs.length; o++) {
                    for (let i = 0; i < node.inputs.length; i++) {
                        if (lastNode.outputs[o].type === node.inputs[i].type) {
                            lastNode.connect(o, node, i);
                            break;
                        }
                    }
                }
            }
            
            lastNode = node;
        }
    });
    
    log(`Loaded workflow from ${run.id} with ${path.length} steps`);
}
