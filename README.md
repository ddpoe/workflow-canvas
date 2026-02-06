# Workflow Canvas

A visual workflow builder and run history explorer using [LiteGraph.js](https://github.com/jagenjo/litegraph.js).

## Features

### Builder View
- **Node Palette** - Drag-and-drop modules with workflow step documentation
- **Visual Canvas** - Connect nodes to build pipelines
- **Type-safe Connections** - Color-coded ports prevent invalid connections

### History View  
- **Run Tree** - Hierarchical view of experiment runs with parent-child relationships
- **Filters** - Filter by data source, time range, or module
- **Detail Panel** - View run parameters, metrics, and artifacts

## Quick Start

```bash
# Install dependencies
poetry install

# Run the server
poetry run uvicorn server:app --reload

# Open http://127.0.0.1:8000
```

## Architecture

```
workflow-canvas/
├── server.py           # FastAPI backend
├── static/
│   ├── index.html      # Main HTML (Builder + History views)
│   ├── css/style.css   # All styles
│   └── js/
│       ├── app.js      # Main app logic, canvas setup
│       ├── nodes.js    # LiteGraph node definitions
│       └── history.js  # History view logic
```

## Data Sources

The app is designed to work with different data backends:

| Backend | Status | Use Case |
|---------|--------|----------|
| Mock Data | ✅ Working | Development/demo |
| MLflow | 🚧 Planned | Experiment tracking |
| Custom DB | 🚧 Planned | Production provenance |

## API Endpoints

- `GET /api/modules` - Available modules and methods (with workflow steps)
- `GET /api/types` - Data type metadata for UI styling
- `POST /api/workflow/validate` - Validate workflow connections
- `POST /api/workflow/run` - Execute workflow
- `GET /api/history/*` - Run history queries

## License

MIT
