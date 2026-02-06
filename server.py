"""
Workflow Canvas - FastAPI Backend

Visual workflow builder with LiteGraph.js and run history explorer.
Designed to be data-source agnostic (mock data, MLflow, custom DB).
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Any
import uuid

app = FastAPI(title="Workflow Canvas")

# =============================================================================
# Mock Data - Simulates what would come from SystemConfig
# =============================================================================

MOCK_MODULES = {
    "DataLoading": {
        "description": "Load and preprocess single-cell data",
        "methods": {
            "LoadAnnData": {
                "version": "1.0.0",
                "description": "Load AnnData from h5ad file",
                "inputs": {
                    "file_path": {"type": "string", "description": "Path to .h5ad file"}
                },
                "outputs": {
                    "adata": {"type": "anndata", "description": "Loaded AnnData object"}
                },
                "workflow": {
                    "steps": [
                        {
                            "number": "1",
                            "name": "Load raw data file",
                            "purpose": "Import single-cell measurements from disk",
                            "function": "scanpy.read_h5ad",
                            "inputs": ["File path"],
                            "outputs": ["Raw AnnData object"],
                            "substeps": [
                                {
                                    "number": "1.1",
                                    "name": "Validate file path",
                                    "purpose": "Check file exists and has correct format"
                                },
                                {
                                    "number": "1.2",
                                    "name": "Read h5ad file",
                                    "purpose": "Load the HDF5-backed AnnData"
                                }
                            ]
                        },
                        {
                            "number": "2",
                            "name": "Validate data structure",
                            "purpose": "Ensure required fields are present",
                            "inputs": ["Raw AnnData"],
                            "outputs": ["Validated AnnData"]
                        }
                    ]
                }
            },
            "LoadCSV": {
                "version": "1.0.0",
                "description": "Load expression matrix from CSV",
                "inputs": {
                    "file_path": {"type": "string", "description": "Path to CSV file"},
                    "gene_column": {"type": "string", "description": "Column containing gene names"}
                },
                "outputs": {
                    "adata": {"type": "anndata", "description": "Constructed AnnData object"}
                },
                "workflow": {
                    "steps": [
                        {
                            "number": "1",
                            "name": "Load raw CSV files",
                            "purpose": "Import single-cell measurements from disk",
                            "function": "read_experimental_data",
                            "critical": "Typically removes 15-25% of cells",
                            "inputs": ["Cell line identifier"],
                            "outputs": ["Raw pandas DataFrame"],
                            "substeps": [
                                {
                                    "number": "1.1",
                                    "name": "Locate data files",
                                    "purpose": "Find all CSV files for specified cell line",
                                    "inputs": ["Cell line name"],
                                    "outputs": ["List of file paths"]
                                },
                                {
                                    "number": "1.2",
                                    "name": "Read and concatenate",
                                    "purpose": "Load all files and combine into single DataFrame",
                                    "function": "read_and_merge_csvs",
                                    "substeps": [
                                        {
                                            "number": "1.2.1",
                                            "name": "Read individual files",
                                            "purpose": "Load each CSV into memory"
                                        },
                                        {
                                            "number": "1.2.2",
                                            "name": "Concatenate DataFrames",
                                            "purpose": "Combine all measurements into single table"
                                        }
                                    ]
                                },
                                {
                                    "number": "1.3",
                                    "name": "Add metadata columns",
                                    "purpose": "Annotate with experimental conditions and batch info"
                                }
                            ]
                        },
                        {
                            "number": "2",
                            "name": "Apply population-level filters",
                            "purpose": "Remove low-quality cells based on aggregate metrics",
                            "function": "apply_population_filters",
                            "critical": "Typically removes 15-25% of cells",
                            "inputs": ["Raw data"],
                            "outputs": ["Population-filtered DataFrame"],
                            "substeps": [
                                {
                                    "number": "2.1",
                                    "name": "Remove low-count outliers",
                                    "purpose": "Filter cells with suspiciously low total protein counts",
                                    "function": "filter_by_total_protein"
                                },
                                {
                                    "number": "2.2",
                                    "name": "Remove batch effect outliers",
                                    "purpose": "Identify and remove cells with extreme batch effects",
                                    "function": "remove_batch_outliers"
                                },
                                {
                                    "number": "2.3",
                                    "name": "Apply coefficient of variation filter",
                                    "purpose": "Remove cells with excessive measurement noise"
                                }
                            ]
                        },
                        {
                            "number": "3",
                            "name": "Convert to AnnData format",
                            "purpose": "Transform to scanpy-compatible structure",
                            "inputs": ["Filtered DataFrame"],
                            "outputs": ["AnnData object"]
                        }
                    ]
                }
            }
        }
    },
    "CellCyclePhaseAnnotations": {
        "description": "Annotate cell cycle phases",
        "methods": {
            "G0MO3PhasePrediction": {
                "version": "1.3.0",
                "description": "Predict G0/M/O3 phases using GMM clustering",
                "inputs": {
                    "adata": {"type": "anndata", "description": "Input AnnData"},
                    "n_components": {"type": "number", "default": 3, "description": "Number of GMM components"}
                },
                "outputs": {
                    "adata": {"type": "anndata", "description": "AnnData with phase annotations"},
                    "phase_labels": {"type": "labels", "description": "Cell cycle phase labels"}
                },
                "workflow": {
                    "steps": [
                        {
                            "number": "1",
                            "name": "Calculate performance metrics",
                            "purpose": "Compute accuracy, precision, recall, F1 for each model",
                            "function": "calculate_all_metrics",
                            "inputs": ["Trained models", "Test features", "Test labels"],
                            "outputs": ["Metrics dictionary"],
                            "substeps": [
                                {
                                    "number": "1.1",
                                    "name": "Compute classification metrics",
                                    "purpose": "Calculate accuracy, precision, recall, F1 per model",
                                    "function": "compute_classification_metrics",
                                    "substeps": [
                                        {
                                            "number": "1.1.1",
                                            "name": "Get predictions for each model",
                                            "purpose": "Generate predicted labels"
                                        },
                                        {
                                            "number": "1.1.2",
                                            "name": "Calculate accuracy",
                                            "purpose": "Measure fraction of correct predictions",
                                            "function": "calculate_accuracy"
                                        },
                                        {
                                            "number": "1.1.3",
                                            "name": "Calculate precision and recall",
                                            "purpose": "Measure true positive rate and positive predictive value",
                                            "function": "calculate_precision_recall"
                                        },
                                        {
                                            "number": "1.1.4",
                                            "name": "Calculate F1 scores",
                                            "purpose": "Compute harmonic mean of precision and recall"
                                        }
                                    ]
                                },
                                {
                                    "number": "1.2",
                                    "name": "Compute calibration metrics",
                                    "purpose": "Assess quality of probability predictions",
                                    "function": "compute_calibration_metrics"
                                }
                            ]
                        },
                        {
                            "number": "2",
                            "name": "Train classifier suite",
                            "purpose": "Build and calibrate models for all feature sets",
                            "function": "train_classifier_suite",
                            "critical": "Takes 30-60 minutes, uses significant memory",
                            "inputs": ["Feature matrix", "Labels", "Metadata"],
                            "outputs": ["Trained and calibrated models"]
                        },
                        {
                            "number": "3",
                            "name": "Evaluate and generate reports",
                            "purpose": "Assess model performance and create visualizations",
                            "function": "evaluate_and_report",
                            "inputs": ["Trained models", "Test data"],
                            "outputs": ["Performance metrics", "Plots", "HTML report"]
                        }
                    ]
                }
            },
            "scVeloPhasePrediction": {
                "version": "2.1.0",
                "description": "Predict phases using RNA velocity",
                "inputs": {
                    "adata": {"type": "anndata", "description": "Input AnnData with spliced/unspliced"},
                    "min_shared_counts": {"type": "number", "default": 20, "description": "Minimum shared counts"}
                },
                "outputs": {
                    "adata": {"type": "anndata", "description": "AnnData with velocity phases"},
                    "phase_labels": {"type": "labels", "description": "Velocity-based phase labels"}
                },
                "workflow": {
                    "steps": [
                        {
                            "number": "1",
                            "name": "Preprocess velocity data",
                            "purpose": "Filter genes and normalize counts for velocity analysis",
                            "function": "scv.pp.filter_and_normalize",
                            "inputs": ["Raw AnnData"],
                            "outputs": ["Normalized AnnData"]
                        },
                        {
                            "number": "2",
                            "name": "Compute RNA velocity",
                            "purpose": "Estimate transcription dynamics",
                            "function": "scv.tl.velocity",
                            "critical": "Takes 5-10 minutes",
                            "inputs": ["Normalized AnnData"],
                            "outputs": ["Velocity AnnData"]
                        },
                        {
                            "number": "3",
                            "name": "Project velocity onto embedding",
                            "purpose": "Visualize velocity vectors in reduced dimensions",
                            "function": "scv.tl.velocity_graph",
                            "inputs": ["Velocity data"],
                            "outputs": ["Velocity graph"]
                        }
                    ]
                }
            }
        }
    },
    "FeatureSelection": {
        "description": "Select informative features",
        "methods": {
            "RandomForestFS": {
                "version": "2.0.0",
                "description": "Random Forest feature importance",
                "inputs": {
                    "adata": {"type": "anndata", "description": "Input AnnData"},
                    "labels": {"type": "labels", "description": "Target labels for classification"},
                    "n_features": {"type": "number", "default": 50, "description": "Number of top features"}
                },
                "outputs": {
                    "feature_set": {"type": "feature_set", "description": "Selected feature names"},
                    "importance_scores": {"type": "array", "description": "Feature importance values"}
                },
                "workflow": {
                    "steps": [
                        {
                            "number": "1",
                            "name": "Initialize model storage",
                            "purpose": "Create dictionary to store trained models",
                            "outputs": ["Empty model dictionary"],
                            "critical": "Takes 5-10 minutes per feature set"
                        },
                        {
                            "number": "2",
                            "name": "Train individual classifiers",
                            "purpose": "Build model for each feature set",
                            "function": "train_individual_models",
                            "critical": "Takes 5-10 minutes per feature set",
                            "inputs": ["Features", "Labels", "Metadata"],
                            "outputs": ["Dictionary of trained models"],
                            "substeps": [
                                {
                                    "number": "2.1",
                                    "name": "Extract feature sets",
                                    "purpose": "Get list of all feature combinations to train"
                                },
                                {
                                    "number": "2.2",
                                    "name": "Train each model",
                                    "purpose": "Fit logistic regression for each feature set",
                                    "function": "train_single_model",
                                    "substeps": [
                                        {
                                            "number": "2.2.1",
                                            "name": "Configure hyperparameters",
                                            "purpose": "Set regularization and solver parameters",
                                            "function": "select_hyperparameters"
                                        },
                                        {
                                            "number": "2.2.2",
                                            "name": "Initialize model",
                                            "purpose": "Create logistic regression instance with parameters"
                                        },
                                        {
                                            "number": "2.2.3",
                                            "name": "Fit model to data",
                                            "purpose": "Train classifier on features and labels"
                                        },
                                        {
                                            "number": "2.2.4",
                                            "name": "Compute training metrics",
                                            "purpose": "Assess model performance on training data",
                                            "function": "compute_training_metrics"
                                        }
                                    ]
                                },
                                {
                                    "number": "2.3",
                                    "name": "Log training summary",
                                    "purpose": "Record which models were successfully trained"
                                }
                            ]
                        },
                        {
                            "number": "3",
                            "name": "Calibrate probability predictions",
                            "purpose": "Apply isotonic regression for better probability estimates",
                            "function": "calibrate_all_models",
                            "inputs": ["Trained models", "Validation data"],
                            "outputs": ["Calibrated models"]
                        },
                        {
                            "number": "4",
                            "name": "Validate model quality",
                            "purpose": "Check models meet minimum performance thresholds",
                            "function": "validate_model_suite",
                            "inputs": ["Calibrated models", "Test data"],
                            "outputs": ["Validated models only"]
                        }
                    ]
                }
            },
            "DelveFS": {
                "version": "1.0.0",
                "description": "DELVE feature selection",
                "inputs": {
                    "adata": {"type": "anndata", "description": "Input AnnData"},
                    "n_features": {"type": "number", "default": 100, "description": "Number of features"}
                },
                "outputs": {
                    "feature_set": {"type": "feature_set", "description": "DELVE-selected features"}
                }
            }
        }
    },
    "Visualization": {
        "description": "Create visualizations",
        "methods": {
            "UMAPPlot": {
                "version": "1.0.0",
                "description": "Generate UMAP embedding and plot",
                "inputs": {
                    "adata": {"type": "anndata", "description": "Input AnnData"},
                    "color_by": {"type": "labels", "description": "Labels for coloring points"},
                    "n_neighbors": {"type": "number", "default": 15, "description": "UMAP neighbors"}
                },
                "outputs": {
                    "figure": {"type": "figure", "description": "Matplotlib figure"},
                    "adata": {"type": "anndata", "description": "AnnData with UMAP coords"}
                },
                "workflow": {
                    "steps": [
                        {
                            "number": "1",
                            "name": "Generate visualizations",
                            "purpose": "Create ROC curves, calibration plots, confusion matrices",
                            "function": "generate_plots",
                            "inputs": ["Models", "Metrics", "Output directory"],
                            "outputs": ["Plot files saved to disk"],
                            "substeps": [
                                {
                                    "number": "1.1",
                                    "name": "Create ROC curves",
                                    "purpose": "Plot true positive rate vs false positive rate"
                                },
                                {
                                    "number": "1.2",
                                    "name": "Create calibration plots",
                                    "purpose": "Visualize predicted vs actual probability distributions"
                                },
                                {
                                    "number": "1.3",
                                    "name": "Create confusion matrices",
                                    "purpose": "Show classification error breakdown"
                                }
                            ]
                        },
                        {
                            "number": "2",
                            "name": "Create summary tables",
                            "purpose": "Build performance comparison tables",
                            "function": "create_summary_tables",
                            "inputs": ["Metrics"],
                            "outputs": ["CSV and HTML tables"]
                        },
                        {
                            "number": "3",
                            "name": "Generate HTML report",
                            "purpose": "Compile all results into interactive HTML report",
                            "function": "build_html_report",
                            "inputs": ["Metrics", "Plots", "Tables"],
                            "outputs": ["report.html file"],
                            "substeps": [
                                {
                                    "number": "3.1",
                                    "name": "Create report template",
                                    "purpose": "Set up HTML structure with CSS styling"
                                },
                                {
                                    "number": "3.2",
                                    "name": "Insert content sections",
                                    "purpose": "Add metrics, plots, and tables to template",
                                    "function": "populate_template"
                                },
                                {
                                    "number": "3.3",
                                    "name": "Write to file",
                                    "purpose": "Save HTML report to disk"
                                }
                            ]
                        }
                    ]
                }
            },
            "FeatureHeatmap": {
                "version": "1.0.0",
                "description": "Heatmap of selected features",
                "inputs": {
                    "adata": {"type": "anndata", "description": "Input AnnData"},
                    "features": {"type": "feature_set", "description": "Features to display"},
                    "group_by": {"type": "labels", "description": "Grouping labels"}
                },
                "outputs": {
                    "figure": {"type": "figure", "description": "Heatmap figure"}
                }
            }
        }
    }
}

# Data type metadata for UI (colors, shapes)
DATA_TYPES = {
    "anndata": {"color": "#4A90D9", "label": "AnnData"},
    "labels": {"color": "#E9A847", "label": "Labels"},
    "feature_set": {"color": "#50C878", "label": "FeatureSet"},
    "array": {"color": "#9B59B6", "label": "Array"},
    "figure": {"color": "#E74C3C", "label": "Figure"},
    "string": {"color": "#95A5A6", "label": "String"},
    "number": {"color": "#95A5A6", "label": "Number"}
}


# =============================================================================
# API Models
# =============================================================================

class WorkflowStep(BaseModel):
    node_id: int
    module: str
    method: str
    version: str
    inputs: dict[str, Any]
    position: dict[str, float]


class Workflow(BaseModel):
    name: str
    steps: list[WorkflowStep]
    connections: list[dict[str, Any]]


class WorkflowResponse(BaseModel):
    status: str
    job_id: str
    message: str


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/")
async def root():
    """Serve the main HTML page."""
    return FileResponse("static/index.html")


@app.get("/api/modules")
async def get_modules():
    """Return available modules and their methods."""
    return MOCK_MODULES


@app.get("/api/types")
async def get_types():
    """Return data type metadata for UI styling."""
    return DATA_TYPES


@app.post("/api/workflow/validate")
async def validate_workflow(workflow: Workflow):
    """Validate a workflow configuration."""
    errors = []
    warnings = []
    
    # Basic validation
    if not workflow.steps:
        errors.append("Workflow has no steps")
    
    # Check for disconnected required inputs
    for step in workflow.steps:
        module = MOCK_MODULES.get(step.module)
        if not module:
            errors.append(f"Unknown module: {step.module}")
            continue
            
        method = module["methods"].get(step.method)
        if not method:
            errors.append(f"Unknown method: {step.method}")
            continue
        
        # Check required inputs
        for input_name, input_spec in method["inputs"].items():
            if "default" not in input_spec and input_name not in step.inputs:
                # Check if connected
                has_connection = any(
                    c.get("target_node") == step.node_id and 
                    c.get("target_slot") == input_name
                    for c in workflow.connections
                )
                if not has_connection:
                    warnings.append(
                        f"{step.method}: Input '{input_name}' is not connected or configured"
                    )
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }


@app.post("/api/workflow/run", response_model=WorkflowResponse)
async def run_workflow(workflow: Workflow):
    """Submit a workflow for execution (mock)."""
    # In real implementation, this would:
    # 1. Convert to Nextflow/Snakemake pipeline
    # 2. Submit to execution backend
    # 3. Return job tracking ID
    
    job_id = str(uuid.uuid4())[:8]
    
    return WorkflowResponse(
        status="submitted",
        job_id=job_id,
        message=f"Workflow '{workflow.name}' submitted with {len(workflow.steps)} steps"
    )


@app.post("/api/workflow/save")
async def save_workflow(workflow: Workflow):
    """Save workflow to storage (mock)."""
    # Would save to database/file in real implementation
    return {"status": "saved", "name": workflow.name}


# =============================================================================
# Static Files
# =============================================================================

app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8500)
