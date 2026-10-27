-- LEGACY-SHAPE FIXTURE (frozen on purpose): the schema of wfc release 0.6.1.
--
-- This is the one case the testing SOP permits raw CREATE TABLE DDL
-- (Database-Schema Test Fixtures, schema-drift witness). It is NOT the current
-- schema and must not be "fixed" to match the models: the schema-drift test
-- opens a database built from it through bootstrap_engine and asserts the
-- result equals a fresh create_all database.
--
-- Produced by building a SQLite database with create_all from
-- wfc/persistence/schema.py at commit c4fd24e (the commit that set
-- version = "0.6.1" in pyproject.toml) and dumping sqlite_master.
-- Refresh it at each release from that release's models.

CREATE TABLE modules (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description VARCHAR, 
	PRIMARY KEY (id)
);

CREATE TABLE samples (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	source_path VARCHAR(500) NOT NULL, 
	registered_path VARCHAR(500) NOT NULL, 
	file_type VARCHAR(50) NOT NULL, 
	registered_at DATETIME, 
	file_size INTEGER, 
	file_mtime FLOAT, 
	file_count INTEGER, 
	description VARCHAR, 
	registration_mode VARCHAR(20) NOT NULL, 
	content_hash VARCHAR(36), 
	push_status VARCHAR(20) NOT NULL, 
	pushed_at DATETIME, 
	push_attempts INTEGER NOT NULL, 
	push_error VARCHAR, 
	PRIMARY KEY (id)
);

CREATE TABLE methods (
	id INTEGER NOT NULL, 
	module_id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	script_path VARCHAR(500), 
	env VARCHAR(50) DEFAULT '' NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(module_id) REFERENCES modules (id)
);

CREATE TABLE module_contracts (
	id INTEGER NOT NULL, 
	module_id INTEGER NOT NULL, 
	contract_type VARCHAR(50) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	value_type VARCHAR(100), 
	required BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(module_id) REFERENCES modules (id)
);

CREATE TABLE method_versions (
	id INTEGER NOT NULL, 
	method_id INTEGER NOT NULL, 
	code_fingerprint VARCHAR(64) NOT NULL, 
	git_commit VARCHAR(40), 
	recorded_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_method_version UNIQUE (method_id, code_fingerprint), 
	FOREIGN KEY(method_id) REFERENCES methods (id)
);

CREATE TABLE tracked_functions (
	id INTEGER NOT NULL, 
	method_id INTEGER NOT NULL, 
	function_name VARCHAR(200) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(method_id) REFERENCES methods (id)
);

CREATE TABLE method_contracts (
	id INTEGER NOT NULL, 
	method_id INTEGER NOT NULL, 
	input_slots JSON, 
	output_slots JSON, 
	params_schema JSON, 
	executor VARCHAR(50) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(method_id) REFERENCES methods (id)
);

CREATE TABLE param_defs (
	id INTEGER NOT NULL, 
	tracked_function_id INTEGER NOT NULL, 
	param_name VARCHAR(200) NOT NULL, 
	param_type VARCHAR(100), 
	default_value VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tracked_function_id) REFERENCES tracked_functions (id)
);

CREATE TABLE runs (
	id INTEGER NOT NULL, 
	method_id INTEGER NOT NULL, 
	params JSON, 
	sample VARCHAR(200), 
	status VARCHAR(50) NOT NULL, 
	pipeline_id VARCHAR(200), 
	nf_process_name VARCHAR(200), 
	started_at DATETIME, 
	finished_at DATETIME, 
	metrics JSON, 
	version_id INTEGER, 
	cache_key VARCHAR(64), 
	cache_source_run_id INTEGER, 
	env_fingerprint VARCHAR(32), 
	error_message VARCHAR, 
	error_traceback VARCHAR, 
	cancelled_due_to_run_id INTEGER, 
	nid VARCHAR(200), 
	node_id VARCHAR(200), 
	PRIMARY KEY (id), 
	FOREIGN KEY(method_id) REFERENCES methods (id), 
	FOREIGN KEY(version_id) REFERENCES method_versions (id), 
	FOREIGN KEY(cache_source_run_id) REFERENCES runs (id), 
	FOREIGN KEY(cancelled_due_to_run_id) REFERENCES runs (id)
);

CREATE TABLE run_annotations (
	run_id INTEGER NOT NULL, 
	favorite BOOLEAN NOT NULL, 
	tags JSON, 
	archived_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (run_id), 
	FOREIGN KEY(run_id) REFERENCES runs (id)
);

CREATE TABLE run_inputs (
	id INTEGER NOT NULL, 
	run_id INTEGER NOT NULL, 
	source_run_id INTEGER, 
	input_name VARCHAR(200), 
	source_slot VARCHAR(200), 
	artifact_path VARCHAR(500), 
	sample_name VARCHAR(200), 
	content_hash VARCHAR(36), 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES runs (id), 
	FOREIGN KEY(source_run_id) REFERENCES runs (id)
);

CREATE TABLE run_outputs (
	id INTEGER NOT NULL, 
	run_id INTEGER NOT NULL, 
	slot VARCHAR(200), 
	output_name VARCHAR(200), 
	artifact_path VARCHAR(500), 
	artifact_type VARCHAR(100) NOT NULL, 
	file_size INTEGER, 
	file_mtime FLOAT, 
	content_hash VARCHAR(36), 
	push_status VARCHAR(20) NOT NULL, 
	pushed_at DATETIME, 
	push_attempts INTEGER NOT NULL, 
	push_error VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES runs (id)
);

CREATE UNIQUE INDEX ix_modules_name ON modules (name);

CREATE INDEX ix_samples_push_status ON samples (push_status);

CREATE UNIQUE INDEX ix_samples_name ON samples (name);

CREATE INDEX ix_methods_module_id ON methods (module_id);

CREATE INDEX ix_methods_name ON methods (name);

CREATE INDEX ix_module_contracts_module_id ON module_contracts (module_id);

CREATE INDEX ix_method_versions_method_id ON method_versions (method_id);

CREATE INDEX ix_tracked_functions_method_id ON tracked_functions (method_id);

CREATE UNIQUE INDEX ix_method_contracts_method_id ON method_contracts (method_id);

CREATE INDEX ix_param_defs_tracked_function_id ON param_defs (tracked_function_id);

CREATE INDEX ix_runs_cancelled_due_to_run_id ON runs (cancelled_due_to_run_id);

CREATE INDEX ix_runs_version_id ON runs (version_id);

CREATE INDEX ix_runs_pipeline_id ON runs (pipeline_id);

CREATE INDEX ix_runs_sample ON runs (sample);

CREATE INDEX ix_runs_cache_key ON runs (cache_key);

CREATE INDEX ix_runs_method_id ON runs (method_id);

CREATE INDEX ix_run_inputs_run_id ON run_inputs (run_id);

CREATE INDEX ix_run_outputs_push_status ON run_outputs (push_status);

CREATE INDEX ix_run_outputs_run_id ON run_outputs (run_id);
