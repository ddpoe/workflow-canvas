/**
 * Workflow Canvas - Main Application Logic
 * 
 * Initializes the LiteGraph canvas and handles UI interactions.
 */

// =============================================================================
// Global State
// =============================================================================

let graph = null;
let canvas = null;
let modules = {};

// =============================================================================
// Initialization
// =============================================================================

async function init() {
    /**
     * Initialize the application.
     */
    
    try {
        setStatus("Loading modules...", "info");
        
        // Register custom nodes from API
        modules = await registerNodesFromAPI();
        
        // Create graph
        graph = new LGraph();
        
        // Get canvas element
        const canvasElement = document.getElementById("workflow-canvas");
        
        // Resize canvas to fit container
        resizeCanvas(canvasElement);
        
        // Create LiteGraph canvas
        canvas = new LGraphCanvas(canvasElement, graph);
        
        // Configure canvas
        configureCanvas(canvas);
        
        // Populate module palette
        populateModulePalette(modules);
        
        // Setup event listeners
        setupEventListeners();
        
        // Setup tab switching
        setupTabSwitching();
        
        // Initialize history view
        initHistory();
        
        // Handle window resize
        window.addEventListener("resize", () => resizeCanvas(canvasElement));
        
        // Start graph execution (for live updates)
        graph.start();
        
        setStatus("Ready", "success");
        updateNodeCount();
        
    } catch (error) {
        console.error("Initialization failed:", error);
        setStatus(`Error: ${error.message}`, "error");
    }
}

function resizeCanvas(canvasElement) {
    /**
     * Resize canvas to fit its container.
     */
    const container = document.getElementById("canvas-container");
    canvasElement.width = container.clientWidth;
    canvasElement.height = container.clientHeight;
    
    if (canvas) {
        canvas.resize();
    }
}

function configureCanvas(canvas) {
    /**
     * Configure LiteGraph canvas settings.
     */
    
    // Visual settings
    canvas.background_image = null;
    canvas.render_shadows = true;
    canvas.render_canvas_border = false;
    canvas.render_connections_shadows = true;
    canvas.render_curved_connections = true;
    canvas.render_connection_arrows = true;
    
    // Interaction settings
    canvas.allow_searchbox = true;
    canvas.allow_dragnodes = true;
    
    // Custom background color
    canvas.clear_background_color = "#1a1a1a";
    
    // Grid
    canvas.render_grid = true;
    canvas.ds.scale = 1;
}

// =============================================================================
// Module Palette
// =============================================================================

function populateModulePalette(modules) {
    /**
     * Populate the left sidebar with draggable module/method items.
     * Now includes workflow step dropdowns for each method.
     */
    
    const moduleList = document.getElementById("module-list");
    moduleList.innerHTML = "";
    
    for (const [moduleName, moduleSpec] of Object.entries(modules)) {
        // Create module group
        const moduleGroup = document.createElement("div");
        moduleGroup.className = "module-group";
        
        // Module header
        const header = document.createElement("div");
        header.className = "module-header";
        header.innerHTML = `
            <span>${moduleName}</span>
            <span class="toggle">▼</span>
        `;
        header.addEventListener("click", () => {
            const list = header.nextElementSibling;
            list.style.display = list.style.display === "none" ? "block" : "none";
            header.querySelector(".toggle").textContent = 
                list.style.display === "none" ? "▶" : "▼";
        });
        
        // Method list
        const methodList = document.createElement("div");
        methodList.className = "method-list";
        
        for (const [methodName, methodDef] of Object.entries(moduleSpec.methods || {})) {
            // Create method container with workflow dropdown
            const methodContainer = document.createElement("div");
            methodContainer.className = "method-container";
            
            // Method header row (with workflow toggle button)
            const methodHeader = document.createElement("div");
            methodHeader.className = "method-header";
            
            // Check if method has workflow steps
            const hasWorkflow = methodDef.workflow && methodDef.workflow.steps && methodDef.workflow.steps.length > 0;
            
            methodHeader.innerHTML = `
                ${hasWorkflow ? '<span class="workflow-toggle" title="View workflow steps">📋</span>' : ''}
                <span class="method-name" draggable="true" data-module="${moduleName}" data-method="${methodName}">
                    ${methodName}
                </span>
                <span class="method-version">v${methodDef.version}</span>
            `;
            
            // Get the method name span for drag/drop
            const methodNameSpan = methodHeader.querySelector(".method-name");
            
            // Drag start on method name
            methodNameSpan.addEventListener("dragstart", (e) => {
                console.log(`Drag started: ${moduleName}/${methodName}`);
                e.dataTransfer.setData("text/module", moduleName);
                e.dataTransfer.setData("text/method", methodName);
                e.dataTransfer.effectAllowed = "copy";
            });
            
            // Double-click on method name to add node
            methodNameSpan.addEventListener("dblclick", () => {
                console.log(`Double-click: ${moduleName}/${methodName}`);
                addNodeToGraph(moduleName, methodName);
            });
            
            methodContainer.appendChild(methodHeader);
            
            // Create workflow panel if method has workflow steps
            if (hasWorkflow) {
                const workflowPanel = createWorkflowPanel(methodDef.workflow);
                methodContainer.appendChild(workflowPanel);
                
                // Toggle workflow panel on click
                const toggleBtn = methodHeader.querySelector(".workflow-toggle");
                toggleBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    workflowPanel.classList.toggle("expanded");
                    toggleBtn.textContent = workflowPanel.classList.contains("expanded") ? "📖" : "📋";
                });
            }
            
            methodList.appendChild(methodContainer);
        }
        
        moduleGroup.appendChild(header);
        moduleGroup.appendChild(methodList);
        moduleList.appendChild(moduleGroup);
    }
}

/**
 * Creates a workflow panel with collapsible steps and sub-steps.
 */
function createWorkflowPanel(workflow) {
    const panel = document.createElement("div");
    panel.className = "workflow-panel";
    
    // Workflow header
    const header = document.createElement("div");
    header.className = "workflow-header";
    header.innerHTML = `Workflow Steps (${workflow.steps.length})`;
    panel.appendChild(header);
    
    // Create steps
    for (const step of workflow.steps) {
        const stepEl = createWorkflowStep(step);
        panel.appendChild(stepEl);
    }
    
    return panel;
}

/**
 * Creates a single workflow step element with optional sub-steps.
 */
function createWorkflowStep(step) {
    const stepDiv = document.createElement("div");
    stepDiv.className = "workflow-step";
    
    // Step header
    const stepHeader = document.createElement("div");
    stepHeader.className = "step-header";
    
    const hasSubsteps = step.substeps && step.substeps.length > 0;
    
    stepHeader.innerHTML = `
        <span class="step-number">${step.number}</span>
        <span class="step-name">${step.name}</span>
        ${step.function ? `<span class="step-function">${step.function}()</span>` : ''}
        ${step.critical ? `<span class="step-warning" title="${step.critical}">⚠️</span>` : ''}
        ${hasSubsteps ? `<span class="step-toggle">▶ (${step.substeps.length})</span>` : ''}
    `;
    
    stepDiv.appendChild(stepHeader);
    
    // Add inputs/outputs if available
    if (step.inputs || step.outputs) {
        const ioDiv = document.createElement("div");
        ioDiv.className = "step-io";
        
        if (step.inputs) {
            const inputs = Array.isArray(step.inputs) ? step.inputs : [step.inputs];
            inputs.forEach(input => {
                ioDiv.innerHTML += `<span class="io-badge input">📥 ${input}</span>`;
            });
        }
        
        if (step.outputs) {
            const outputs = Array.isArray(step.outputs) ? step.outputs : [step.outputs];
            outputs.forEach(output => {
                ioDiv.innerHTML += `<span class="io-badge output">📤 ${output}</span>`;
            });
        }
        
        stepDiv.appendChild(ioDiv);
    }
    
    // Create sub-steps list if any
    if (hasSubsteps) {
        const substepList = document.createElement("div");
        substepList.className = "substep-list";
        
        for (const substep of step.substeps) {
            const substepDiv = createSubstep(substep);
            substepList.appendChild(substepDiv);
        }
        
        stepDiv.appendChild(substepList);
        
        // Toggle sub-steps on header click
        stepHeader.addEventListener("click", () => {
            substepList.classList.toggle("expanded");
            const toggle = stepHeader.querySelector(".step-toggle");
            if (toggle) {
                toggle.textContent = substepList.classList.contains("expanded") 
                    ? `▼ (${step.substeps.length})` 
                    : `▶ (${step.substeps.length})`;
            }
        });
    }
    
    return stepDiv;
}

/**
 * Creates a sub-step element.
 */
function createSubstep(substep) {
    const div = document.createElement("div");
    div.className = "workflow-substep";
    
    const header = document.createElement("div");
    header.className = "substep-header";
    
    const hasChildren = substep.substeps && substep.substeps.length > 0;
    
    header.innerHTML = `
        <span class="substep-number">${substep.number}</span>
        <span class="substep-name">${substep.name}</span>
        ${substep.function ? `<span class="step-function">${substep.function}()</span>` : ''}
        ${hasChildren ? `<span class="step-toggle">▶ (${substep.substeps.length})</span>` : ''}
    `;
    
    div.appendChild(header);
    
    // Add purpose if available
    if (substep.purpose) {
        const purposeDiv = document.createElement("div");
        purposeDiv.className = "substep-purpose";
        purposeDiv.textContent = substep.purpose;
        div.appendChild(purposeDiv);
    }
    
    // Recursively create nested sub-steps
    if (hasChildren) {
        const nestedList = document.createElement("div");
        nestedList.className = "substep-list";
        
        for (const nestedSubstep of substep.substeps) {
            nestedList.appendChild(createSubstep(nestedSubstep));
        }
        
        div.appendChild(nestedList);
        
        // Toggle on click
        header.addEventListener("click", (e) => {
            e.stopPropagation();
            nestedList.classList.toggle("expanded");
            const toggle = header.querySelector(".step-toggle");
            if (toggle) {
                toggle.textContent = nestedList.classList.contains("expanded")
                    ? `▼ (${substep.substeps.length})`
                    : `▶ (${substep.substeps.length})`;
            }
        });
    }
    
    return div;
}

function addNodeToGraph(moduleName, methodName, x = null, y = null) {
    /**
     * Add a node to the graph.
     */
    
    const nodeType = `${moduleName}/${methodName}`;
    console.log(`Creating node of type: ${nodeType}`);
    
    const node = LiteGraph.createNode(nodeType);
    console.log(`Node created:`, node);
    
    if (node) {
        // Position node
        if (x === null || y === null) {
            // Center of visible canvas area
            const canvasRect = canvas.canvas.getBoundingClientRect();
            x = 200 + Math.random() * 200;
            y = 200 + Math.random() * 200;
        }
        
        node.pos = [x, y];
        
        // Set node color based on module
        node.color = getModuleColor(moduleName);
        node.bgcolor = "#353535";
        
        graph.add(node);
        updateNodeCount();
        
        log(`Added node: ${moduleName}/${methodName}`);
        console.log(`Node added to graph at [${x}, ${y}]`);
    } else {
        console.error(`Failed to create node: ${nodeType}`);
        console.log("Available node types:", Object.keys(LiteGraph.registered_node_types));
    }
    
    return node;
}

// =============================================================================
// Event Listeners
// =============================================================================

function setupEventListeners() {
    /**
     * Setup UI event listeners.
     */
    
    // Toolbar buttons
    document.getElementById("btn-validate").addEventListener("click", validateWorkflow);
    document.getElementById("btn-export").addEventListener("click", exportWorkflow);
    document.getElementById("btn-run").addEventListener("click", runWorkflow);
    document.getElementById("btn-clear").addEventListener("click", clearWorkflow);
    
    // Output tabs
    document.querySelectorAll(".output-tab-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            // Update active tab
            document.querySelectorAll(".output-tab-btn").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            
            // Show corresponding content
            const tabName = e.target.dataset.tab;
            document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
            document.getElementById(`output-${tabName}`).classList.add("active");
        });
    });
    
    // Canvas drop handler
    const canvasElement = document.getElementById("workflow-canvas");
    
    canvasElement.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
    });
    
    canvasElement.addEventListener("drop", (e) => {
        e.preventDefault();
        
        const moduleName = e.dataTransfer.getData("text/module");
        const methodName = e.dataTransfer.getData("text/method");
        
        console.log(`Drop event: ${moduleName}/${methodName}`);
        
        if (moduleName && methodName) {
            // Convert screen coords to graph coords
            const rect = canvasElement.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            console.log(`Drop position: [${x}, ${y}]`);
            addNodeToGraph(moduleName, methodName, x, y);
        }
    });
    
    // Track graph changes
    graph.onNodeAdded = () => updateNodeCount();
    graph.onNodeRemoved = () => updateNodeCount();
}

function setupTabSwitching() {
    /**
     * Setup main tab (Builder/History) switching.
     */
    
    document.querySelectorAll(".main-tab-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const viewName = e.target.dataset.view;
            
            // Update tab buttons
            document.querySelectorAll(".main-tab-btn").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            
            // Update views
            document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
            document.getElementById(`${viewName}-view`).classList.add("active");
            
            // Update toolbar visibility
            const toolbar = document.getElementById("toolbar");
            toolbar.style.display = viewName === "builder" ? "flex" : "none";
            
            // Resize canvas when switching to builder
            if (viewName === "builder") {
                const canvasElement = document.getElementById("workflow-canvas");
                resizeCanvas(canvasElement);
            }
        });
    });
}

// =============================================================================
// Workflow Operations
// =============================================================================

function buildWorkflowJSON() {
    /**
     * Build workflow JSON from current graph state.
     */
    
    const workflowName = document.getElementById("workflow-name").value || "Untitled";
    
    const steps = [];
    const connections = [];
    
    // Get all nodes
    const nodes = graph._nodes;
    
    for (const node of nodes) {
        // Build step
        const step = {
            node_id: node.id,
            module: node.moduleName,
            method: node.methodName,
            version: node.methodVersion,
            inputs: { ...node.properties },
            position: { x: node.pos[0], y: node.pos[1] }
        };
        steps.push(step);
        
        // Get connections from this node
        if (node.outputs) {
            for (let i = 0; i < node.outputs.length; i++) {
                const output = node.outputs[i];
                if (output.links) {
                    for (const linkId of output.links) {
                        const link = graph.links[linkId];
                        if (link) {
                            connections.push({
                                source_node: link.origin_id,
                                source_slot: output.name,
                                source_type: output.type,
                                target_node: link.target_id,
                                target_slot: graph.getNodeById(link.target_id)?.inputs[link.target_slot]?.name,
                                target_type: graph.getNodeById(link.target_id)?.inputs[link.target_slot]?.type
                            });
                        }
                    }
                }
            }
        }
    }
    
    return {
        name: workflowName,
        steps: steps,
        connections: connections
    };
}

async function validateWorkflow() {
    /**
     * Validate the current workflow.
     */
    
    setStatus("Validating...", "info");
    
    try {
        const workflow = buildWorkflowJSON();
        
        const response = await fetch("/api/workflow/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(workflow)
        });
        
        const result = await response.json();
        
        // Display result
        displayOutput(result);
        
        if (result.valid) {
            if (result.warnings.length > 0) {
                setStatus(`Valid with ${result.warnings.length} warning(s)`, "info");
                log("Validation warnings:");
                result.warnings.forEach(w => log(`  ⚠ ${w}`));
            } else {
                setStatus("Workflow is valid!", "success");
                log("Workflow validated successfully");
            }
        } else {
            setStatus(`Invalid: ${result.errors.length} error(s)`, "error");
            log("Validation errors:");
            result.errors.forEach(e => log(`  ❌ ${e}`));
        }
        
    } catch (error) {
        setStatus(`Validation failed: ${error.message}`, "error");
        log(`Error: ${error.message}`);
    }
}

function exportWorkflow() {
    /**
     * Export workflow as JSON.
     */
    
    const workflow = buildWorkflowJSON();
    displayOutput(workflow);
    
    // Also save to file
    const blob = new Blob([JSON.stringify(workflow, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${workflow.name.replace(/\s+/g, '_')}.json`;
    a.click();
    URL.revokeObjectURL(url);
    
    setStatus("Workflow exported", "success");
    log(`Exported workflow: ${workflow.name}`);
}

async function runWorkflow() {
    /**
     * Submit workflow for execution.
     */
    
    setStatus("Submitting workflow...", "info");
    
    try {
        const workflow = buildWorkflowJSON();
        
        // Validate first
        const validateResponse = await fetch("/api/workflow/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(workflow)
        });
        
        const validateResult = await validateResponse.json();
        
        if (!validateResult.valid) {
            setStatus("Cannot run: workflow has errors", "error");
            displayOutput(validateResult);
            return;
        }
        
        // Submit for execution
        const response = await fetch("/api/workflow/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(workflow)
        });
        
        const result = await response.json();
        displayOutput(result);
        
        setStatus(`Submitted! Job ID: ${result.job_id}`, "success");
        log(`Workflow submitted: ${result.message}`);
        log(`Job ID: ${result.job_id}`);
        
    } catch (error) {
        setStatus(`Submission failed: ${error.message}`, "error");
        log(`Error: ${error.message}`);
    }
}

function clearWorkflow() {
    /**
     * Clear all nodes from the graph.
     */
    
    if (confirm("Clear all nodes from the workspace?")) {
        graph.clear();
        updateNodeCount();
        setStatus("Workspace cleared", "info");
        log("Cleared workspace");
    }
}

// =============================================================================
// UI Helpers
// =============================================================================

function setStatus(message, type = "info") {
    /**
     * Update status bar message.
     */
    
    const statusBar = document.getElementById("status-bar");
    const statusText = document.getElementById("status-text");
    
    statusText.textContent = message;
    statusBar.className = type;
}

function updateNodeCount() {
    /**
     * Update node count display.
     */
    
    const count = graph ? graph._nodes.length : 0;
    document.getElementById("node-count").textContent = `Nodes: ${count}`;
}

function displayOutput(data) {
    /**
     * Display data in the JSON output panel.
     */
    
    const outputJson = document.getElementById("output-json");
    outputJson.textContent = JSON.stringify(data, null, 2);
    
    // Switch to JSON tab
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelector('.tab-btn[data-tab="json"]').classList.add("active");
    document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
    outputJson.classList.add("active");
}

function log(message) {
    /**
     * Add message to log panel.
     */
    
    const outputLog = document.getElementById("output-log");
    const timestamp = new Date().toLocaleTimeString();
    outputLog.textContent += `[${timestamp}] ${message}\n`;
    outputLog.scrollTop = outputLog.scrollHeight;
}

// =============================================================================
// Start Application
// =============================================================================

document.addEventListener("DOMContentLoaded", init);
