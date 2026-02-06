/**
 * Workflow Canvas - Custom Node Definitions
 * 
 * Dynamically creates LiteGraph nodes from module/method definitions.
 */

// =============================================================================
// Data Type Colors (will be updated from API)
// =============================================================================

const TYPE_COLORS = {
    anndata: "#4A90D9",
    labels: "#E9A847",
    feature_set: "#50C878",
    array: "#9B59B6",
    figure: "#E74C3C",
    string: "#95A5A6",
    number: "#95A5A6"
};

// Map type names to LiteGraph slot types (for connection validation)
const TYPE_SLOTS = {
    anndata: "ANNDATA",
    labels: "LABELS",
    feature_set: "FEATURE_SET",
    array: "ARRAY",
    figure: "FIGURE",
    string: "STRING",
    number: "NUMBER"
};

// =============================================================================
// Register Custom Slot Colors
// =============================================================================

function registerSlotColors() {
    // LiteGraph uses this to color the connection slots
    if (typeof LGraphCanvas !== "undefined" && LGraphCanvas.link_type_colors) {
        for (const [typeName, color] of Object.entries(TYPE_COLORS)) {
            const slotType = TYPE_SLOTS[typeName];
            LGraphCanvas.link_type_colors[slotType] = color;
        }
    }
}

// =============================================================================
// Node Factory - Creates node classes from method definitions
// =============================================================================

function createNodeClass(moduleName, methodName, methodDef) {
    /**
     * Creates a LiteGraph node class for a specific method.
     */
    
    // Create the node class by extending LGraphNode
    class MethodNode extends LGraphNode {
        constructor() {
            super();
            
            // Store metadata
            this.moduleName = moduleName;
            this.methodName = methodName;
            this.methodVersion = methodDef.version;
            this.title = methodName;
            
            // Node properties
            this.properties = {};
            this.properties.version = methodDef.version;
            
            // Add input slots
            for (const [inputName, inputSpec] of Object.entries(methodDef.inputs || {})) {
                const slotType = TYPE_SLOTS[inputSpec.type] || inputSpec.type.toUpperCase();
                this.addInput(inputName, slotType);
                
                // For primitive types without connections, add a widget
                if (inputSpec.type === "string") {
                    this.addWidget("text", inputName, inputSpec.default || "", (v) => {
                        this.properties[inputName] = v;
                    });
                    this.properties[inputName] = inputSpec.default || "";
                } else if (inputSpec.type === "number") {
                    this.addWidget("number", inputName, inputSpec.default || 0, (v) => {
                        this.properties[inputName] = v;
                    }, { min: 0, max: 1000, step: 1 });
                    this.properties[inputName] = inputSpec.default || 0;
                }
            }
            
            // Add output slots
            for (const [outputName, outputSpec] of Object.entries(methodDef.outputs || {})) {
                const slotType = TYPE_SLOTS[outputSpec.type] || outputSpec.type.toUpperCase();
                this.addOutput(outputName, slotType);
            }
            
            // Compute size after adding all slots/widgets
            this.size = this.computeSize();
            this.size[0] = Math.max(this.size[0], 200);
            this.size[1] += 20; // Extra space for module name
        }
        
        onExecute() {
            // This would be called during graph execution
        }
        
        onDrawForeground(ctx) {
            if (this.flags.collapsed) return;
            
            // Draw module name at top (smaller, muted)
            ctx.save();
            ctx.font = "10px Arial";
            ctx.fillStyle = "#888";
            ctx.textAlign = "left";
            ctx.fillText(this.moduleName, 8, -8);
            
            // Draw version badge (right side)
            ctx.textAlign = "right";
            ctx.fillText(`v${this.methodVersion}`, this.size[0] - 8, -8);
            ctx.restore();
        }
        
        onDrawBackground(ctx) {
            if (this.flags.collapsed) return;
            
            // Draw module header bar
            const moduleColor = getModuleColor(this.moduleName);
            ctx.fillStyle = moduleColor;
            ctx.fillRect(0, -LiteGraph.NODE_TITLE_HEIGHT, this.size[0], 4);
        }
    }
    
    // Static properties required by LiteGraph
    MethodNode.title = methodName;
    MethodNode.desc = methodDef.description || `${moduleName} - ${methodName}`;
    
    return MethodNode;
}

// =============================================================================
// Register All Nodes from Module Data
// =============================================================================

async function registerNodesFromAPI() {
    /**
     * Fetches module definitions and registers all nodes.
     */
    
    try {
        // Fetch module definitions
        const response = await fetch('/api/modules');
        const modules = await response.json();
        
        // Fetch type colors
        const typesResponse = await fetch('/api/types');
        const types = await typesResponse.json();
        
        // Update type colors
        for (const [typeName, typeSpec] of Object.entries(types)) {
            TYPE_COLORS[typeName] = typeSpec.color;
        }
        
        // Register slot colors with LiteGraph
        registerSlotColors();
        
        // Register each method as a node type
        for (const [moduleName, moduleSpec] of Object.entries(modules)) {
            for (const [methodName, methodDef] of Object.entries(moduleSpec.methods || {})) {
                const nodeClass = createNodeClass(moduleName, methodName, methodDef);
                const nodeTypeName = `${moduleName}/${methodName}`;
                
                try {
                    LiteGraph.registerNodeType(nodeTypeName, nodeClass);
                    console.log(`Registered node: ${nodeTypeName}`);
                } catch (e) {
                    console.error(`Failed to register ${nodeTypeName}:`, e);
                }
            }
        }
        
        console.log("All nodes registered from API");
        return modules;
        
    } catch (error) {
        console.error("Failed to register nodes:", error);
        throw error;
    }
}

// =============================================================================
// Utility Functions
// =============================================================================

function getModuleColor(moduleName) {
    // Return a consistent color for each module
    const moduleColors = {
        "DataLoading": "#3498db",
        "CellCyclePhaseAnnotations": "#e74c3c",
        "FeatureSelection": "#2ecc71",
        "Visualization": "#9b59b6"
    };
    return moduleColors[moduleName] || "#666666";
}

// =============================================================================
// Configure LiteGraph Defaults
// =============================================================================

function configureLiteGraph() {
    // Default settings
    LiteGraph.NODE_TITLE_HEIGHT = 24;
    LiteGraph.NODE_SLOT_HEIGHT = 20;
    LiteGraph.NODE_WIDGET_HEIGHT = 24;
    LiteGraph.NODE_TEXT_SIZE = 12;
    LiteGraph.DEFAULT_SHADOW_COLOR = "rgba(0,0,0,0.3)";
    
    // Enable type-based connection validation
    LiteGraph.validate_links_connection_types = true;
    
    // Register custom link colors
    LGraphCanvas.link_type_colors = LGraphCanvas.link_type_colors || {};
    registerSlotColors();
}

// Initialize on load
configureLiteGraph();
