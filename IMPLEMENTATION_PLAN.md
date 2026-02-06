# Blockly Workflow Builder - Implementation Plan

## Goal
Create a minimal FastAPI + Vanilla JS prototype to validate Blockly for visual workflow building. This is Option C: no React, no build step, just static HTML served by FastAPI.

## What This Prototype Demonstrates
1. Blockly workspace with custom module/method blocks
2. Blocks snap together based on output→input compatibility
3. Export workflow as JSON
4. POST workflow to FastAPI endpoint

## Project Structure

```
dFlow/gui/blockly/
├── server.py              # FastAPI backend
├── static/
│   ├── index.html         # Main page with Blockly workspace
│   ├── css/
│   │   └── style.css      # Basic styling
│   └── js/
│       ├── blocks.js      # Custom block definitions
│       ├── generators.js  # Code/JSON generators for blocks
│       └── app.js         # Main app logic (init, export, run)
└── IMPLEMENTATION_PLAN.md # This file
```

## Step-by-Step Implementation

### Step 1: FastAPI Backend (server.py)

Create minimal API with these endpoints:

```python
# GET /api/modules - Return available modules/methods
# POST /api/workflow/validate - Validate workflow JSON
# POST /api/workflow/run - Submit workflow for execution (mock)
```

Also serves static files from `static/` directory.

**Key code:**
- Use `FastAPI` with `StaticFiles` mount
- Return mock module data matching SystemConfig structure
- `/api/workflow/run` just returns `{"status": "submitted", "job_id": "abc123"}`

### Step 2: HTML Page (static/index.html)

Basic HTML structure:
- Load Blockly from CDN (`https://unpkg.com/blockly/blockly.min.js`)
- Toolbox div (left sidebar with available blocks)
- Workspace div (main drag-drop area)
- Control buttons: "Export JSON", "Run Workflow", "Clear"
- Output panel to show generated JSON

**Key elements:**
```html
<div id="toolbox">...</div>
<div id="blocklyDiv"></div>
<button id="exportBtn">Export JSON</button>
<button id="runBtn">Run Workflow</button>
<pre id="output"></pre>
```

### Step 3: Custom Block Definitions (static/js/blocks.js)

Define blocks for each module/method. Each block has:
- **Inputs**: Previous statement connection (what comes before)
- **Outputs**: Next statement connection (what comes after)
- **Fields**: Method name, version, parameters dropdown

**Block types to create:**

1. **Module blocks** (containers):
   - `module_cellcycle` - CellCyclePhaseAnnotations
   - `module_featureselection` - FeatureSelection

2. **Method blocks** (actual operations):
   - `method_g0mo3` - G0MO3PhasePrediction
   - `method_scvelo` - scVeloPhasePrediction
   - `method_randomforest` - RandomForestFS
   - `method_delve` - DelveFS

**Connection logic:**
- Each block declares what output type it produces
- Next block must accept that output type
- Blockly enforces this via `check` property on connections

Example block definition:
```javascript
Blockly.Blocks['method_g0mo3'] = {
  init: function() {
    this.appendDummyInput()
        .appendField("G0MO3PhasePrediction")
        .appendField("v1.3.0");
    this.setPreviousStatement(true, ["start", "any"]);
    this.setNextStatement(true, "phase_labels");
    this.setColour(120);
    this.setTooltip("Predict G0/M/O3 phases using GMM");
  }
};
```

### Step 4: JSON Generator (static/js/generators.js)

Convert Blockly workspace to workflow JSON:

```javascript
// Output format:
{
  "workflow_name": "My Workflow",
  "steps": [
    {
      "order": 1,
      "module": "CellCyclePhaseAnnotations",
      "method": "G0MO3PhasePrediction",
      "version": "1.3.0",
      "params": {}
    },
    {
      "order": 2,
      "module": "FeatureSelection",
      "method": "RandomForestFS",
      "version": "2.0.0",
      "params": {}
    }
  ]
}
```

Traverse workspace blocks in order, extract method info.

### Step 5: App Logic (static/js/app.js)

Main functionality:
1. **Initialize Blockly** - Inject workspace into div
2. **Load toolbox** - Fetch `/api/modules`, dynamically build toolbox XML
3. **Export button** - Generate JSON, display in output panel
4. **Run button** - POST JSON to `/api/workflow/run`, show response
5. **Clear button** - Reset workspace

```javascript
// Pseudocode
async function init() {
  const modules = await fetch('/api/modules').then(r => r.json());
  const toolbox = buildToolbox(modules);
  workspace = Blockly.inject('blocklyDiv', {toolbox: toolbox});
}

function exportWorkflow() {
  const json = generateWorkflowJSON(workspace);
  document.getElementById('output').textContent = JSON.stringify(json, null, 2);
}

async function runWorkflow() {
  const json = generateWorkflowJSON(workspace);
  const resp = await fetch('/api/workflow/run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(json)
  });
  const result = await resp.json();
  alert(`Workflow submitted! Job ID: ${result.job_id}`);
}
```

### Step 6: Styling (static/css/style.css)

Minimal CSS:
- Full-height workspace
- Toolbox on left
- Output panel on right or bottom
- Buttons styled

## Mock Data (matches Streamlit prototype)

```python
MOCK_MODULES = {
    "CellCyclePhaseAnnotations": {
        "outputs": ["PhaseLabelSavePath"],
        "methods": {
            "G0MO3PhasePrediction": {"version": "1.3.0", "output_type": "phase_labels"},
            "scVeloPhasePrediction": {"version": "2.1.0", "output_type": "phase_labels"}
        }
    },
    "FeatureSelection": {
        "outputs": ["FeatureSet"],
        "methods": {
            "RandomForestFS": {"version": "2.0.0", "output_type": "feature_set"},
            "DelveFS": {"version": "1.0.0", "output_type": "feature_set"}
        }
    }
}
```

## How to Run

```bash
cd dFlow/gui/blockly
poetry run uvicorn server:app --reload --port 8000
# Open http://localhost:8000
```

## Success Criteria

✅ Blockly workspace loads with module/method blocks in toolbox
✅ Can drag blocks onto workspace
✅ Blocks snap together (method → method)
✅ Invalid connections rejected (Blockly shows red highlight)
✅ Export button shows workflow JSON
✅ Run button POSTs to API and shows response

## Future Enhancements (Not in Prototype)

- Dynamic block generation from actual SystemConfig
- Parameter configuration dropdowns on blocks
- Validation warnings (missing required steps)
- Save/load workflows
- Integration with actual Nextflow execution
- Block colors by module category

## Files to Create (in order)

1. `server.py` - FastAPI app
2. `static/index.html` - Main page
3. `static/css/style.css` - Styling
4. `static/js/blocks.js` - Block definitions
5. `static/js/generators.js` - JSON generator
6. `static/js/app.js` - Main logic

## Estimated Time

| Task | Time |
|------|------|
| FastAPI server | 5 min |
| HTML structure | 5 min |
| Block definitions | 10 min |
| JSON generator | 5 min |
| App logic | 10 min |
| Styling | 5 min |
| Testing/polish | 10 min |
| **Total** | **~50 min** |

---

## Prompt for New Chat

Copy this to start the new chat:

```
I need you to implement a Blockly workflow builder prototype for my DMGTLAS project.

Read the implementation plan at: c:\Users\dap182\Documents\git\dFlow\gui\blockly\IMPLEMENTATION_PLAN.md

Create all 6 files as specified. Use the mock data structure shown. The goal is to validate that Blockly works for visual workflow building before we invest more.

Key requirements:
1. FastAPI serves static files + API endpoints
2. Vanilla JS (no React, no build step)
3. Custom blocks for modules/methods
4. Blocks enforce output→input compatibility
5. Export workflow as JSON
6. POST to /api/workflow/run

Start with server.py, then the static files in order.
```
