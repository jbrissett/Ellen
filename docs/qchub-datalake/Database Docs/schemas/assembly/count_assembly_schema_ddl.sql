-- DROP SCHEMA assembly;

CREATE SCHEMA assembly AUTHORIZATION tcmsdbadm;

-- DROP TYPE assembly.assembly_status;

CREATE TYPE assembly.assembly_status AS ENUM (
	'draft',
	'qa_in_progress',
	'ready_to_publish',
	'published',
	'archived');

-- DROP TYPE assembly.category_dimension;

CREATE TYPE assembly.category_dimension AS ENUM (
	'none',
	'class',
	'bank');

-- DROP TYPE assembly.count_data_type;

CREATE TYPE assembly.count_data_type AS ENUM (
	'movement',
	'speed',
	'both');

-- DROP TYPE assembly.flag_severity;

CREATE TYPE assembly.flag_severity AS ENUM (
	'info',
	'warn',
	'error');

-- DROP TYPE assembly.interval_mode;

CREATE TYPE assembly.interval_mode AS ENUM (
	'specific',
	'range',
	'predicate');

-- DROP TYPE assembly.leg;

CREATE TYPE assembly.leg AS ENUM (
	'N',
	'S',
	'E',
	'W');

-- DROP TYPE assembly.link_dir;

CREATE TYPE assembly.link_dir AS ENUM (
	'NB',
	'SB',
	'EB',
	'WB');

-- DROP TYPE assembly.link_origin;

CREATE TYPE assembly.link_origin AS ENUM (
	'auto',
	'manual');

-- DROP TYPE assembly.override_reason;

CREATE TYPE assembly.override_reason AS ENUM (
	'fix_noise',
	'backfill',
	'balance_adj',
	'other');

-- DROP TYPE assembly.qa_flag_type;

CREATE TYPE assembly.qa_flag_type AS ENUM (
	'no_data',
	'empty_interval',
	'low_interval',
	'high_interval',
	'impossible_movement',
	'suspicious_movement',
	'suspicious_classification',
	'other');

-- DROP TYPE assembly.qa_status;

CREATE TYPE assembly.qa_status AS ENUM (
	'open',
	'investigating',
	'resolved',
	'wont_fix');

-- DROP TYPE assembly.rule_action;

CREATE TYPE assembly.rule_action AS ENUM (
	'include',
	'exclude',
	'weighted_merge',
	'formula_adjusted');

-- DROP TYPE assembly.source_type;

CREATE TYPE assembly.source_type AS ENUM (
	'single',
	'weighted_merge',
	'exclude');

-- DROP TYPE assembly.study_type;

CREATE TYPE assembly.study_type AS ENUM (
	'tmc',
	'midblock',
	'survey',
	'other');

-- DROP SEQUENCE assembly.order_corridor_corridor_id_seq;

CREATE SEQUENCE assembly.order_corridor_corridor_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;-- assembly.order_assembly definition

-- Drop table

-- DROP TABLE assembly.order_assembly;

CREATE TABLE assembly.order_assembly (
	order_no text NOT NULL,
	project_name text NULL,
	company_id int8 NULL,
	company_name text NULL,
	office_id int8 NULL,
	office_name text NULL,
	order_date timestamp NULL,
	desired_delivery_date timestamp NULL,
	default_midblock_bin_scheme_id int8 NULL,
	default_tmc_bin_scheme_id int8 NULL,
	locations_total int4 DEFAULT 0 NULL,
	sitecodes_total int4 DEFAULT 0 NULL,
	percent_complete numeric(5, 2) DEFAULT 0 NULL,
	qchub_order_id int8 NULL,
	qchub_last_sync_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT order_assembly_pkey PRIMARY KEY (order_no)
);


-- assembly.order_note definition

-- Drop table

-- DROP TABLE assembly.order_note;

CREATE TABLE assembly.order_note (
	order_no text NOT NULL,
	"scope" text NOT NULL,
	order_location_id int8 NULL,
	note_text text NOT NULL,
	created_by text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT order_note_pkey PRIMARY KEY (order_no, scope, created_at)
);


-- assembly.location_grid definition

-- Drop table

-- DROP TABLE assembly.location_grid;

CREATE TABLE assembly.location_grid (
	grid_id uuid DEFAULT gen_random_uuid() NOT NULL,
	order_no text NOT NULL,
	title text NOT NULL,
	is_active bool DEFAULT false NULL,
	created_by text NULL,
	created_at timestamptz DEFAULT now() NULL,
	updated_at timestamptz DEFAULT now() NULL,
	CONSTRAINT location_grid_pkey PRIMARY KEY (grid_id),
	CONSTRAINT location_grid_order_no_fkey FOREIGN KEY (order_no) REFERENCES assembly.order_assembly(order_no) ON DELETE CASCADE
);


-- assembly.location_grid_edge definition

-- Drop table

-- DROP TABLE assembly.location_grid_edge;

CREATE TABLE assembly.location_grid_edge (
	grid_id uuid NOT NULL,
	from_order_location_id int8 NOT NULL,
	from_leg assembly.leg NOT NULL,
	to_order_location_id int8 NOT NULL,
	to_leg assembly.leg NOT NULL,
	link_dir assembly.link_dir NOT NULL,
	origin assembly.link_origin DEFAULT 'auto'::assembly.link_origin NOT NULL,
	edge_distance_meters numeric(3, 2) NULL,
	"locked" bool DEFAULT false NULL,
	auto_confidence numeric(3, 2) DEFAULT 0.00 NULL,
	meta jsonb DEFAULT '{}'::jsonb NULL,
	CONSTRAINT location_grid_edge_pkey PRIMARY KEY (grid_id, from_order_location_id, from_leg, to_order_location_id, to_leg),
	CONSTRAINT location_grid_edge_grid_id_fkey FOREIGN KEY (grid_id) REFERENCES assembly.location_grid(grid_id) ON DELETE CASCADE
);


-- assembly.location_grid_flag definition

-- Drop table

-- DROP TABLE assembly.location_grid_flag;

CREATE TABLE assembly.location_grid_flag (
	grid_id uuid NOT NULL,
	from_order_location_id int8 NOT NULL,
	from_leg assembly.leg NOT NULL,
	to_order_location_id int8 NOT NULL,
	to_leg assembly.leg NOT NULL,
	severity assembly.flag_severity NOT NULL,
	rule_key text NOT NULL,
	details jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT location_grid_flag_pkey PRIMARY KEY (grid_id, from_order_location_id, from_leg, to_order_location_id, to_leg, rule_key, created_at),
	CONSTRAINT location_grid_flag_grid_id_fkey FOREIGN KEY (grid_id) REFERENCES assembly.location_grid(grid_id) ON DELETE CASCADE
);


-- assembly.order_corridor definition

-- Drop table

-- DROP TABLE assembly.order_corridor;

CREATE TABLE assembly.order_corridor (
	order_no text NOT NULL,
	corridor_id bigserial NOT NULL,
	canonical_name text NOT NULL,
	name_aliases _text DEFAULT '{}'::text[] NULL,
	corridor_axis text NOT NULL,
	corridor_bearing_deg numeric(5, 2) NOT NULL,
	osm_way_ids _int8 DEFAULT '{}'::bigint[] NULL,
	created_at timestamptz DEFAULT now() NULL,
	CONSTRAINT order_corridor_corridor_axis_check CHECK ((corridor_axis = ANY (ARRAY['NS'::text, 'EW'::text]))),
	CONSTRAINT order_corridor_pkey PRIMARY KEY (corridor_id),
	CONSTRAINT order_corridor_order_no_fkey FOREIGN KEY (order_no) REFERENCES assembly.order_assembly(order_no) ON DELETE CASCADE
);


-- assembly.order_location definition

-- Drop table

-- DROP TABLE assembly.order_location;

CREATE TABLE assembly.order_location (
	order_no text NOT NULL,
	order_location_id int8 NOT NULL,
	location_status text NULL,
	location_name text NOT NULL,
	city text NULL,
	state_code text NULL,
	latitude numeric NULL,
	longitude numeric NULL,
	study_type assembly.study_type NULL,
	default_midblock_bin_scheme_id int8 NULL,
	default_tmc_bin_scheme_id int8 NULL,
	has_any_files bool DEFAULT false NULL,
	sitecodes_count int4 DEFAULT 0 NULL,
	collection_site_id int8 NULL,
	note text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	default_collection_date date NULL,
	default_requested_bucket_minutes int4 NULL,
	CONSTRAINT order_location_pkey PRIMARY KEY (order_no, order_location_id),
	CONSTRAINT order_location_order_no_fkey FOREIGN KEY (order_no) REFERENCES assembly.order_assembly(order_no) ON DELETE CASCADE
);
CREATE INDEX order_location_has_any_files_idx ON assembly.order_location USING btree (has_any_files);
CREATE INDEX order_location_location_status_idx ON assembly.order_location USING btree (location_status);
CREATE INDEX order_location_order_no_idx ON assembly.order_location USING btree (order_no);


-- assembly.order_location_corridor definition

-- Drop table

-- DROP TABLE assembly.order_location_corridor;

CREATE TABLE assembly.order_location_corridor (
	order_no text NOT NULL,
	order_location_id int8 NOT NULL,
	corridor_id int8 NOT NULL,
	local_bearing_deg numeric(5, 2) NULL,
	station_axis text NOT NULL,
	is_axis_overridden bool DEFAULT false NULL,
	CONSTRAINT order_location_corridor_pkey PRIMARY KEY (order_no, order_location_id, corridor_id),
	CONSTRAINT order_location_corridor_station_axis_check CHECK ((station_axis = ANY (ARRAY['NS'::text, 'EW'::text]))),
	CONSTRAINT order_location_corridor_corridor_id_fkey FOREIGN KEY (corridor_id) REFERENCES assembly.order_corridor(corridor_id) ON DELETE CASCADE,
	CONSTRAINT order_location_corridor_order_no_order_location_id_fkey FOREIGN KEY (order_no,order_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE
);


-- assembly.order_location_flag definition

-- Drop table

-- DROP TABLE assembly.order_location_flag;

CREATE TABLE assembly.order_location_flag (
	order_no text NOT NULL,
	order_location_id int8 NOT NULL,
	flag_key text NOT NULL,
	flag_value jsonb NOT NULL,
	created_by text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT order_location_flag_pkey PRIMARY KEY (order_no, order_location_id, flag_key),
	CONSTRAINT order_location_flag_order_no_order_location_id_fkey FOREIGN KEY (order_no,order_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE
);


-- assembly.order_location_sitecode definition

-- Drop table

-- DROP TABLE assembly.order_location_sitecode;

CREATE TABLE assembly.order_location_sitecode (
	order_no text NOT NULL,
	order_location_id int8 NOT NULL,
	sitecode text NOT NULL,
	start_time time NULL,
	end_time time NULL,
	duration_hours numeric(6, 2) NULL,
	days_json jsonb NULL,
	assembly_session_id uuid NULL,
	has_files bool DEFAULT false NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	target_bin_scheme_id int4 NULL,
	target_requested_minutes int2 NULL,
	initialized_at timestamptz NULL,
	initialized_by text NULL,
	CONSTRAINT order_location_sitecode_pkey PRIMARY KEY (order_no, order_location_id, sitecode),
	CONSTRAINT order_location_sitecode_order_no_order_location_id_fkey FOREIGN KEY (order_no,order_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE
);
CREATE INDEX order_location_sitecode_has_files_idx ON assembly.order_location_sitecode USING btree (has_files);
CREATE INDEX order_location_sitecode_sitecode_idx ON assembly.order_location_sitecode USING btree (sitecode);


-- assembly.order_location_street definition

-- Drop table

-- DROP TABLE assembly.order_location_street;

CREATE TABLE assembly.order_location_street (
	order_no text NOT NULL,
	order_location_id int8 NOT NULL,
	street_name text NOT NULL,
	street_direction_code text NOT NULL,
	sort_order int2 DEFAULT 0 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT order_location_street_pkey PRIMARY KEY (order_no, order_location_id, street_name, street_direction_code),
	CONSTRAINT order_location_street_order_no_order_location_id_fkey FOREIGN KEY (order_no,order_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE
);


-- assembly.assembly_session definition

-- Drop table

-- DROP TABLE assembly.assembly_session;

CREATE TABLE assembly.assembly_session (
	assembly_session_id uuid DEFAULT gen_random_uuid() NOT NULL,
	order_no text NOT NULL,
	location_id int8 NULL,
	sitecode_id int8 NOT NULL,
	status assembly.assembly_status DEFAULT 'draft'::assembly.assembly_status NOT NULL,
	requested_bucket_minutes int2 NULL,
	title text NULL,
	notes text NULL,
	source_filter_hint jsonb NULL,
	created_by text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	interval_start timestamp NOT NULL,
	interval_end timestamp NOT NULL,
	order_location_id int8 NULL,
	sitecode text NULL,
	target_bin_scheme_id int4 NULL,
	count_data_type assembly.count_data_type NULL,
	study_type assembly.study_type NULL,
	include_pedestrians bool DEFAULT false NOT NULL, -- If true, seed ped movements (e.g., E-NS).
	include_rtor bool DEFAULT true NOT NULL, -- If true, include RTOR movement columns.
	direction_axis text NULL, -- Midblock axis: ns or ew; null for TMC.
	target_speed_bins jsonb NULL, -- Canonical list of target speed bins (from/to/label).
	directional_pedestrians bool DEFAULT false NOT NULL, -- If true, seed directional crosswalk movements (e.g., E-NS, E-SN). If false, use legs only (E Leg, W Leg, N Leg, S Leg).
	CONSTRAINT assembly_session_direction_axis_chk CHECK (((direction_axis IS NULL) OR (direction_axis = ANY (ARRAY['ns'::text, 'ew'::text])))),
	CONSTRAINT assembly_session_pkey PRIMARY KEY (assembly_session_id),
	CONSTRAINT fk_session_sitecode FOREIGN KEY (order_no,order_location_id,sitecode) REFERENCES assembly.order_location_sitecode(order_no,order_location_id,sitecode)
);
CREATE INDEX ix_asm_session_status ON assembly.assembly_session USING btree (status);

-- Column comments

COMMENT ON COLUMN assembly.assembly_session.include_pedestrians IS 'If true, seed ped movements (e.g., E-NS).';
COMMENT ON COLUMN assembly.assembly_session.include_rtor IS 'If true, include RTOR movement columns.';
COMMENT ON COLUMN assembly.assembly_session.direction_axis IS 'Midblock axis: ns or ew; null for TMC.';
COMMENT ON COLUMN assembly.assembly_session.target_speed_bins IS 'Canonical list of target speed bins (from/to/label).';
COMMENT ON COLUMN assembly.assembly_session.directional_pedestrians IS 'If true, seed directional crosswalk movements (e.g., E-NS, E-SN). If false, use legs only (E Leg, W Leg, N Leg, S Leg).';


-- assembly.corridor_segment definition

-- Drop table

-- DROP TABLE assembly.corridor_segment;

CREATE TABLE assembly.corridor_segment (
	order_no text NOT NULL,
	corridor_id int8 NOT NULL,
	from_location_id int8 NOT NULL,
	to_location_id int8 NOT NULL,
	path_geom public.geometry(linestring, 4326) NULL,
	length_m int4 NOT NULL,
	side_junctions int4 DEFAULT 0 NULL,
	service_driveways int4 DEFAULT 0 NULL,
	one_way_share numeric(3, 2) DEFAULT 0.0 NULL,
	road_class_mode text NULL,
	leakage_score numeric(6, 3) DEFAULT 0.0 NULL,
	meta jsonb DEFAULT '{}'::jsonb NULL,
	CONSTRAINT corridor_segment_pkey PRIMARY KEY (order_no, corridor_id, from_location_id, to_location_id),
	CONSTRAINT corridor_segment_corridor_id_fkey FOREIGN KEY (corridor_id) REFERENCES assembly.order_corridor(corridor_id) ON DELETE CASCADE,
	CONSTRAINT corridor_segment_order_no_from_location_id_fkey FOREIGN KEY (order_no,from_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE,
	CONSTRAINT corridor_segment_order_no_to_location_id_fkey FOREIGN KEY (order_no,to_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE
);


-- assembly.location_grid_node definition

-- Drop table

-- DROP TABLE assembly.location_grid_node;

CREATE TABLE assembly.location_grid_node (
	grid_id uuid NOT NULL,
	order_no text NOT NULL,
	order_location_id int8 NOT NULL,
	i int4 NOT NULL,
	j int4 NOT NULL,
	rotation_deg int2 NOT NULL,
	"locked" bool DEFAULT false NULL,
	auto_confidence numeric(3, 2) DEFAULT 0.00 NULL,
	meta jsonb DEFAULT '{}'::jsonb NULL,
	CONSTRAINT location_grid_node_pkey PRIMARY KEY (grid_id, order_no, order_location_id),
	CONSTRAINT location_grid_node_grid_id_fkey FOREIGN KEY (grid_id) REFERENCES assembly.location_grid(grid_id) ON DELETE CASCADE,
	CONSTRAINT location_grid_node_order_no_order_location_id_fkey FOREIGN KEY (order_no,order_location_id) REFERENCES assembly.order_location(order_no,order_location_id) ON DELETE CASCADE
);


-- assembly.manual_override definition

-- Drop table

-- DROP TABLE assembly.manual_override;

CREATE TABLE assembly.manual_override (
	manual_override_id uuid DEFAULT gen_random_uuid() NOT NULL,
	assembly_session_id uuid NOT NULL,
	count_data_type assembly.count_data_type NOT NULL,
	movement text NULL,
	speed_mph int4 NULL,
	volume_count int4 NULL,
	volume_count_by_class jsonb NULL,
	vehicle_count int4 NULL,
	vehicle_count_by_class jsonb NULL,
	category_breakdown jsonb NULL,
	reason_code assembly.override_reason NOT NULL,
	note text NULL,
	created_by text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	interval_start timestamp NOT NULL,
	CONSTRAINT manual_override_pkey PRIMARY KEY (manual_override_id),
	CONSTRAINT manual_override_assembly_session_id_fkey FOREIGN KEY (assembly_session_id) REFERENCES assembly.assembly_session(assembly_session_id) ON DELETE CASCADE
);
CREATE INDEX ix_override_json ON assembly.manual_override USING gin (category_breakdown);
CREATE INDEX ix_override_movement ON assembly.manual_override USING btree (movement);


-- assembly.qa_flag definition

-- Drop table

-- DROP TABLE assembly.qa_flag;

CREATE TABLE assembly.qa_flag (
	qa_flag_id uuid DEFAULT gen_random_uuid() NOT NULL,
	assembly_session_id uuid NOT NULL,
	flag_type assembly.qa_flag_type NOT NULL,
	movement text NULL,
	class_or_bank_key text NULL,
	status assembly.qa_status DEFAULT 'open'::assembly.qa_status NOT NULL,
	reason_tags _text NULL,
	action_tags _text NULL,
	has_attachments bool DEFAULT false NOT NULL,
	note text NULL,
	created_by text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	resolved_by text NULL,
	resolved_at timestamptz NULL,
	interval_range tsrange NULL,
	CONSTRAINT qa_flag_pkey PRIMARY KEY (qa_flag_id),
	CONSTRAINT qa_flag_assembly_session_id_fkey FOREIGN KEY (assembly_session_id) REFERENCES assembly.assembly_session(assembly_session_id) ON DELETE CASCADE
);
CREATE INDEX ix_qaflag_session_status ON assembly.qa_flag USING btree (assembly_session_id, status);


-- assembly.qa_flag_link definition

-- Drop table

-- DROP TABLE assembly.qa_flag_link;

CREATE TABLE assembly.qa_flag_link (
	qa_flag_id uuid NOT NULL,
	file_id int8 NULL,
	summarized_data_interval_id int8 NULL,
	interval_start timestamptz NULL,
	movement text NULL,
	speed_mph int4 NULL,
	CONSTRAINT qa_flag_link_qa_flag_id_fkey FOREIGN KEY (qa_flag_id) REFERENCES assembly.qa_flag(qa_flag_id) ON DELETE CASCADE
);


-- assembly.preview_lineage definition

-- Drop table

-- DROP TABLE assembly.preview_lineage;

CREATE TABLE assembly.preview_lineage (
	assembly_session_id uuid NOT NULL,
	snapshot_seq int4 NOT NULL,
	lineage_key uuid NOT NULL,
	contributors jsonb NOT NULL,
	applied_rules jsonb NOT NULL,
	applied_overrides jsonb NOT NULL,
	CONSTRAINT preview_lineage_pkey PRIMARY KEY (assembly_session_id, snapshot_seq, lineage_key)
);
CREATE INDEX ix_prev_lineage_json ON assembly.preview_lineage USING gin (contributors jsonb_path_ops);


-- assembly.preview_snapshot definition

-- Drop table

-- DROP TABLE assembly.preview_snapshot;

CREATE TABLE assembly.preview_snapshot (
	assembly_session_id uuid NOT NULL,
	snapshot_seq int4 NOT NULL,
	generated_at timestamptz DEFAULT now() NOT NULL,
	derived_by text NULL,
	bucket_minutes int2 NULL,
	target_bin_scheme_id int4 NULL, -- Target bin scheme for this snapshot. All preview rows are aligned to this target.
	CONSTRAINT preview_snapshot_pkey PRIMARY KEY (assembly_session_id, snapshot_seq)
);
CREATE INDEX ix_prev_snap_session_seq ON assembly.preview_snapshot USING btree (assembly_session_id, snapshot_seq);

-- Column comments

COMMENT ON COLUMN assembly.preview_snapshot.target_bin_scheme_id IS 'Target bin scheme for this snapshot. All preview rows are aligned to this target.';


-- assembly.preview_volume_by_movement definition

-- Drop table

-- DROP TABLE assembly.preview_volume_by_movement;

CREATE TABLE assembly.preview_volume_by_movement (
	assembly_session_id uuid NOT NULL,
	snapshot_seq int4 NOT NULL,
	movement text NOT NULL,
	volume_count int4 NOT NULL,
	volume_count_by_class jsonb NULL,
	category_dimension assembly.category_dimension DEFAULT 'bank'::assembly.category_dimension NOT NULL,
	bank_schema_id int8 NULL,
	category_breakdown jsonb NULL,
	lineage_key uuid DEFAULT gen_random_uuid() NOT NULL,
	interval_start timestamp NOT NULL,
	is_missing bool DEFAULT true NOT NULL,
	CONSTRAINT preview_vbm_bank_guard CHECK (((category_dimension <> 'bank'::assembly.category_dimension) OR (bank_schema_id IS NOT NULL))) NOT VALID,
	CONSTRAINT uq_prev_mv_cell UNIQUE (assembly_session_id, snapshot_seq, interval_start, movement)
);
CREATE INDEX ix_prev_mv_session_seq_t0 ON assembly.preview_volume_by_movement USING btree (assembly_session_id, snapshot_seq, interval_start);


-- assembly.preview_volume_by_speed definition

-- Drop table

-- DROP TABLE assembly.preview_volume_by_speed;

CREATE TABLE assembly.preview_volume_by_speed (
	assembly_session_id uuid NOT NULL,
	snapshot_seq int4 NOT NULL,
	movement text DEFAULT '~'::text NOT NULL,
	speed_mph int4 NOT NULL,
	vehicle_count int4 NOT NULL,
	vehicle_count_by_class jsonb NULL,
	category_dimension assembly.category_dimension DEFAULT 'bank'::assembly.category_dimension NOT NULL,
	bank_schema_id int8 NULL,
	category_breakdown jsonb NULL,
	lineage_key uuid DEFAULT gen_random_uuid() NOT NULL,
	interval_start timestamp NOT NULL,
	is_missing bool DEFAULT true NOT NULL,
	speed_bin jsonb NULL, -- Full target bin definition for this row, e.g. {"from":1,"to":10,"label":"1-10"}
	CONSTRAINT preview_vbs_bank_guard CHECK (((category_dimension <> 'bank'::assembly.category_dimension) OR (bank_schema_id IS NOT NULL))) NOT VALID,
	CONSTRAINT uq_prev_sp_cell UNIQUE (assembly_session_id, snapshot_seq, interval_start, movement, speed_mph)
);
CREATE INDEX ix_prev_sp_session_seq_t0 ON assembly.preview_volume_by_speed USING btree (assembly_session_id, snapshot_seq, interval_start);

-- Column comments

COMMENT ON COLUMN assembly.preview_volume_by_speed.speed_bin IS 'Full target bin definition for this row, e.g. {"from":1,"to":10,"label":"1-10"}';


-- assembly.selection_rule definition

-- Drop table

-- DROP TABLE assembly.selection_rule;

CREATE TABLE assembly.selection_rule (
	selection_rule_id uuid DEFAULT gen_random_uuid() NOT NULL,
	assembly_session_id uuid NOT NULL,
	count_data_type assembly.count_data_type NOT NULL,
	movements _text NULL,
	interval_mode assembly.interval_mode DEFAULT 'range'::assembly.interval_mode NOT NULL,
	interval_predicate jsonb NULL,
	category_dimension assembly.category_dimension DEFAULT 'none'::assembly.category_dimension NOT NULL,
	bank_schema_id int8 NULL,
	category_include _text NULL,
	category_exclude _text NULL,
	rule_action assembly.rule_action DEFAULT 'include'::assembly.rule_action NOT NULL,
	source_bindings jsonb NOT NULL,
	formula jsonb NULL,
	priority int4 DEFAULT 100 NOT NULL,
	note text NULL,
	created_by text NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	interval_range tsrange NULL,
	interval_list _tsrange NULL,
	CONSTRAINT selection_rule_category_guard CHECK ((((category_include IS NULL) AND (category_exclude IS NULL) AND (category_dimension = ANY (ARRAY['none'::assembly.category_dimension, 'class'::assembly.category_dimension, 'bank'::assembly.category_dimension]))) OR ((category_dimension = ANY (ARRAY['class'::assembly.category_dimension, 'bank'::assembly.category_dimension])) AND ((category_dimension <> 'bank'::assembly.category_dimension) OR (bank_schema_id IS NOT NULL))))),
	CONSTRAINT selection_rule_pkey PRIMARY KEY (selection_rule_id)
);
CREATE INDEX ix_rule_bindings_gin ON assembly.selection_rule USING gin (source_bindings jsonb_path_ops);
CREATE INDEX ix_rule_cat_dim ON assembly.selection_rule USING btree (category_dimension, bank_schema_id);
CREATE INDEX ix_rule_movements_gin ON assembly.selection_rule USING gin (movements);
CREATE INDEX ix_rule_session_pri ON assembly.selection_rule USING btree (assembly_session_id, priority DESC);


-- assembly.selection_rule_diff definition

-- Drop table

-- DROP TABLE assembly.selection_rule_diff;

CREATE TABLE assembly.selection_rule_diff (
	selection_rule_id uuid NOT NULL,
	changed_at timestamptz DEFAULT now() NOT NULL,
	changed_by text NOT NULL,
	diff jsonb NOT NULL
);


-- assembly.preview_lineage foreign keys

ALTER TABLE assembly.preview_lineage ADD CONSTRAINT preview_lineage_assembly_session_id_snapshot_seq_fkey FOREIGN KEY (assembly_session_id,snapshot_seq) REFERENCES assembly.preview_snapshot(assembly_session_id,snapshot_seq) ON DELETE CASCADE;


-- assembly.preview_snapshot foreign keys

ALTER TABLE assembly.preview_snapshot ADD CONSTRAINT preview_snapshot_assembly_session_id_fkey FOREIGN KEY (assembly_session_id) REFERENCES assembly.assembly_session(assembly_session_id) ON DELETE CASCADE;
ALTER TABLE assembly.preview_snapshot ADD CONSTRAINT preview_snapshot_target_bin_scheme_id_fkey FOREIGN KEY (target_bin_scheme_id) REFERENCES ops_config.bin_schemes(id);


-- assembly.preview_volume_by_movement foreign keys

ALTER TABLE assembly.preview_volume_by_movement ADD CONSTRAINT preview_volume_by_movement_assembly_session_id_snapshot_se_fkey FOREIGN KEY (assembly_session_id,snapshot_seq) REFERENCES assembly.preview_snapshot(assembly_session_id,snapshot_seq) ON DELETE CASCADE;
ALTER TABLE assembly.preview_volume_by_movement ADD CONSTRAINT preview_volume_by_movement_bank_schema_id_fkey FOREIGN KEY (bank_schema_id) REFERENCES ops_config.bin_schemes(id);


-- assembly.preview_volume_by_speed foreign keys

ALTER TABLE assembly.preview_volume_by_speed ADD CONSTRAINT preview_volume_by_speed_assembly_session_id_snapshot_seq_fkey FOREIGN KEY (assembly_session_id,snapshot_seq) REFERENCES assembly.preview_snapshot(assembly_session_id,snapshot_seq) ON DELETE CASCADE;
ALTER TABLE assembly.preview_volume_by_speed ADD CONSTRAINT preview_volume_by_speed_bank_schema_id_fkey FOREIGN KEY (bank_schema_id) REFERENCES ops_config.bin_schemes(id);


-- assembly.selection_rule foreign keys

ALTER TABLE assembly.selection_rule ADD CONSTRAINT selection_rule_assembly_session_id_fkey FOREIGN KEY (assembly_session_id) REFERENCES assembly.assembly_session(assembly_session_id) ON DELETE CASCADE;
ALTER TABLE assembly.selection_rule ADD CONSTRAINT selection_rule_bank_schema_id_fkey FOREIGN KEY (bank_schema_id) REFERENCES ops_config.bin_schemes(id);


-- assembly.selection_rule_diff foreign keys

ALTER TABLE assembly.selection_rule_diff ADD CONSTRAINT selection_rule_diff_selection_rule_id_fkey FOREIGN KEY (selection_rule_id) REFERENCES assembly.selection_rule(selection_rule_id) ON DELETE CASCADE;


-- assembly.v_order_progress source

CREATE OR REPLACE VIEW assembly.v_order_progress
AS SELECT o.order_no,
    count(DISTINCT ols.sitecode) AS sitecodes_total,
    COALESCE(sum(
        CASE
            WHEN s.status = ANY (ARRAY['ready_to_publish'::assembly.assembly_status, 'published'::assembly.assembly_status]) THEN 1
            ELSE 0
        END), 0::bigint) AS sitecodes_done,
        CASE
            WHEN count(DISTINCT ols.sitecode) = 0 THEN 0::numeric
            ELSE round(100.0 * COALESCE(sum(
            CASE
                WHEN s.status = ANY (ARRAY['ready_to_publish'::assembly.assembly_status, 'published'::assembly.assembly_status]) THEN 1
                ELSE 0
            END), 0::bigint)::numeric / count(DISTINCT ols.sitecode)::numeric, 2)
        END::numeric(5,2) AS percent_complete
   FROM assembly.order_assembly o
     LEFT JOIN assembly.order_location ol ON ol.order_no = o.order_no
     LEFT JOIN assembly.order_location_sitecode ols ON ols.order_no = ol.order_no AND ols.order_location_id = ol.order_location_id
     LEFT JOIN assembly.assembly_session s ON s.order_no = o.order_no AND s.sitecode_id::text = ols.sitecode
  GROUP BY o.order_no;



-- DROP FUNCTION assembly.compute_preview_snapshot(uuid, text);

CREATE OR REPLACE FUNCTION assembly.compute_preview_snapshot(p_session_id uuid, p_assembler_tag text DEFAULT 'assembler_v2'::text)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
  v_session              assembly.assembly_session;
  v_next_snapshot_seq    integer;
  v_effective_bucket     int2;
BEGIN
  -- 0) Load and lock session row
  SELECT * INTO v_session
  FROM assembly.assembly_session
  WHERE assembly_session_id = p_session_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'assembly_session % not found', p_session_id;
  END IF;

  -- 1) Allocate snapshot id
  SELECT COALESCE(MAX(snapshot_seq), 0) + 1
    INTO v_next_snapshot_seq
  FROM assembly.preview_snapshot
  WHERE assembly_session_id = p_session_id;

  INSERT INTO assembly.preview_snapshot (assembly_session_id, snapshot_seq, derived_by)
  VALUES (p_session_id, v_next_snapshot_seq, p_assembler_tag);

  -- Baseline seed: bucket spine × movement set, marked as missing
  WITH params AS (
    SELECT v_session.requested_bucket_minutes AS bucket_minutes,
           v_session.interval_start AS i0,
           v_session.interval_end   AS i1,
           v_session.target_bin_scheme_id AS bank_schema_id,
           v_session.study_type     AS study_type
  ),
  buckets AS (
    SELECT gs AS interval_start
    FROM params p,
         generate_series(
           p.i0,
           p.i1 - make_interval(mins := p.bucket_minutes),
           make_interval(mins := p.bucket_minutes)
         ) gs
  ),
  rule_movements AS (
    SELECT DISTINCT unnest(r.movements) AS movement
    FROM assembly.selection_rule r
    WHERE r.assembly_session_id = p_session_id
      AND r.movements IS NOT NULL
  ),
  seen_movements AS (
    SELECT DISTINCT vm.movement
    FROM source_summary.volume_by_movement vm
    WHERE vm.interval_start >= v_session.interval_start
      AND vm.interval_start <  v_session.interval_end
  ),
  default_movements AS (
    SELECT * FROM (
      SELECT 'tmc'::assembly.study_type AS st, x.m AS movement
      FROM (VALUES
        ('NB Thru'),('NB Left'),('NB Right'),('NB U-Turn'),('NB RTOR'),
        ('SB Thru'),('SB Left'),('SB Right'),('SB U-Turn'),('SB RTOR'),
        ('EB Thru'),('EB Left'),('EB Right'),('EB U-Turn'),('EB RTOR'),
        ('WB Thru'),('WB Left'),('WB Right'),('WB U-Turn'),('WB RTOR')
      ) x(m)
      UNION ALL
      SELECT 'midblock', x.m FROM (VALUES ('NB'),('SB'),('EB'),('WB')) x(m)
    ) d
    WHERE d.st = v_session.study_type
  ),
  movements AS (
    SELECT movement FROM rule_movements
    UNION
    SELECT movement FROM seen_movements
    UNION
    SELECT movement FROM default_movements
  ),
  baseline AS (
    INSERT INTO assembly.preview_volume_by_movement (
      assembly_session_id, snapshot_seq, interval_start, movement,
      volume_count, volume_count_by_class, category_dimension, bank_schema_id,
      category_breakdown, lineage_key, is_missing
    )
    SELECT
      p_session_id, v_next_snapshot_seq, b.interval_start, m.movement,
      0, NULL, 'bank', v_session.bank_schema_id,
      NULL, gen_random_uuid(), true
    FROM buckets b
    CROSS JOIN movements m
    ON CONFLICT ON CONSTRAINT uq_prev_mv_cell DO NOTHING
    RETURNING lineage_key
  )
  INSERT INTO assembly.preview_lineage
    (assembly_session_id, snapshot_seq, lineage_key, contributors, applied_rules, applied_overrides)
  SELECT p_session_id, v_next_snapshot_seq, lineage_key, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb
  FROM baseline;

  -- ----------------------------------------------------------------------------------
  -- TEMP TABLES (session-scoped, dropped at end)
  -- ----------------------------------------------------------------------------------

  -- 2) Rules scoped to MOVEMENT type for this session
  CREATE TEMP TABLE _rules AS
  SELECT r.*
  FROM assembly.selection_rule r
  WHERE r.assembly_session_id = p_session_id
    AND r.count_data_type = 'movement';

  CREATE INDEX ON _rules (priority DESC);

  -- Normalize effective interval for rules
  ALTER TABLE _rules ADD COLUMN eff_range tsrange;

  UPDATE _rules
  SET eff_range =
    CASE
      WHEN interval_mode = 'range' AND interval_range IS NOT NULL
        THEN interval_range
      WHEN interval_mode = 'specific'
        THEN NULL     -- handled via interval_list expansion
      ELSE tsrange(v_session.interval_start, v_session.interval_end, '[)')
    END;

  -- 3) Candidate source cells in session window for this sitecode
  --    NOTE: We keep both class and bank JSONs; only one is non-null per row by constraint.
  CREATE TEMP TABLE _src AS
  SELECT
    vm.summarized_data_interval_id AS sdi_id,
    vm.interval_start,
    vm.movement,
    vm.volume_count,
    vm.volume_count_by_class,
    vm.volume_count_by_bank,
    f.file_id,
    cap.category_dimension,
    cap.bank_schema_id,
    cap.bucket_minutes
  FROM source_summary.volume_by_movement vm
  JOIN source_summary.summarized_data_interval sdi
    ON sdi.summarized_data_interval_id = vm.summarized_data_interval_id
   AND sdi.interval_start = vm.interval_start
  JOIN raw_metadata_catalog.files f
    ON f.file_id = sdi.file_id
  JOIN raw_metadata_catalog.file_summary_capability cap
    ON cap.file_id = f.file_id
  WHERE f.sitecode = v_session.sitecode_id::text
    AND vm.interval_start >= v_session.interval_start
    AND vm.interval_start <  v_session.interval_end;

  CREATE INDEX ON _src (interval_start, movement);
  CREATE INDEX ON _src (file_id);

  -- 4) Expand target cells (union of intervals/movements present)
  CREATE TEMP TABLE _target_cells AS
  SELECT DISTINCT interval_start, movement
  FROM _src;

 -- 5) Expand rules across time & movement without SRF-in-CASE
	--    Use UNION ALL between "range" rules (one row) and "specific" rules (1 row per list item)
	DROP TABLE IF EXISTS _rule_windows;
	CREATE TEMP TABLE _rule_windows AS
	-- A) RANGE / SESSION-WINDOW rules → single window per rule
	SELECT
	  r.selection_rule_id,
	  r.rule_action,
	  r.movements,
	  r.category_dimension,
	  r.bank_schema_id,
	  r.category_include,
	  r.category_exclude,
	  r.source_bindings,
	  r.priority,
	  -- pick effective range (explicit range or session window already computed as r.eff_range)
	  r.eff_range AS win
	FROM _rules r
	WHERE r.interval_mode <> 'specific' OR r.interval_list IS NULL
	
	UNION ALL
	
	-- B) SPECIFIC rules → expand interval_list via LATERAL unnest
	SELECT
	  r.selection_rule_id,
	  r.rule_action,
	  r.movements,
	  r.category_dimension,
	  r.bank_schema_id,
	  r.category_include,
	  r.category_exclude,
	  r.source_bindings,
	  r.priority,
	  rl.win
	FROM _rules r
	JOIN LATERAL (
	  SELECT u AS win
	  FROM unnest(r.interval_list) AS u
	) rl ON TRUE
	WHERE r.interval_mode = 'specific'
	  AND r.interval_list IS NOT NULL;

  -- 6) Exclusions: collect rule_action='exclude' windows
  -- 6) Exclusions: use _rule_windows directly (no duplicate movements)
	DROP TABLE IF EXISTS _exclusions;
	CREATE TEMP TABLE _exclusions AS
	SELECT *
	FROM _rule_windows
	WHERE rule_action = 'exclude';
	
	-- 7) Include/merge/formula candidates (non-excludes)
	DROP TABLE IF EXISTS _includes;
	CREATE TEMP TABLE _includes AS
	SELECT *
	FROM _rule_windows
	WHERE rule_action IN ('include','weighted_merge','formula_adjusted');

  -- 8) For each target cell, find candidate rules that match its time & movement
  --    and are not excluded by any overlapping exclusion rule (last-rule-wins realized by later priority).
  CREATE TEMP TABLE _cell_rule_candidates AS
  SELECT
    t.interval_start,
    t.movement,
    i.selection_rule_id,
    i.rule_action,
    i.source_bindings,
    i.category_dimension,
    i.bank_schema_id,
    i.category_include,
    i.category_exclude,
    i.priority
  FROM _target_cells t
  JOIN _includes i
    ON (i.win IS NULL OR t.interval_start <@ i.win)
   AND (i.movements IS NULL OR t.movement = ANY(i.movements))
  WHERE NOT EXISTS (
    SELECT 1
    FROM _exclusions x
    WHERE (x.win IS NULL OR t.interval_start <@ x.win)
      AND (x.movements IS NULL OR t.movement = ANY(x.movements))
      AND x.priority >= i.priority  -- exclusion at same or higher priority knocks this include out
  );

  CREATE INDEX ON _cell_rule_candidates (interval_start, movement, priority DESC);

  -- 9) LAST RULE WINS: pick the highest-priority rule per cell
  CREATE TEMP TABLE _top_rule AS
  SELECT DISTINCT ON (interval_start, movement)
         interval_start, movement,
         selection_rule_id, rule_action, source_bindings,
         category_dimension, bank_schema_id, category_include, category_exclude,
         priority
  FROM _cell_rule_candidates
  ORDER BY interval_start, movement, priority DESC, selection_rule_id;

  -- 10) Materialize bindings to rows
  --     Expected binding shape (baseline): [{"file_id":123, "weight":0.7}, ...]
  CREATE TEMP TABLE _bindings AS
  SELECT
    tr.interval_start,
    tr.movement,
    tr.selection_rule_id,
    tr.rule_action,
    (b->>'file_id')::bigint                 AS file_id,
    COALESCE((b->>'weight')::numeric, 1.0)  AS weight,
    tr.category_dimension,
    tr.bank_schema_id,
    tr.category_include,
    tr.category_exclude,
    tr.priority
  FROM _top_rule tr,
       LATERAL jsonb_array_elements(tr.source_bindings) AS b;

  CREATE INDEX ON _bindings (file_id);
  CREATE INDEX ON _bindings (interval_start, movement);

  -- 11) Join bindings to source rows
  CREATE TEMP TABLE _picked AS
  SELECT
    b.interval_start,
    b.movement,
    b.selection_rule_id,
    b.rule_action,
    b.file_id,
    s.sdi_id,
    s.volume_count,
    s.volume_count_by_class,
    s.volume_count_by_bank,
    s.category_dimension AS src_dim,
    s.bank_schema_id     AS src_bank_schema_id,
    s.bucket_minutes     AS src_bucket,
    b.weight,
    b.category_dimension AS tgt_dim_hint,
    b.bank_schema_id     AS tgt_bank_hint,
    b.category_include,
    b.category_exclude,
    b.priority
  FROM _bindings b
  JOIN _src s
    ON s.file_id = b.file_id
   AND s.interval_start = b.interval_start
   AND s.movement = b.movement;

  -- 12) Compute snapshot bucket = MAX bucket among SELECTED contributors (after last-rule-wins)
  --     NOTE: Some rules may be include/formula; weighted_merge doesn't affect bucket choice.
  SELECT GREATEST(
           COALESCE(v_session.requested_bucket_minutes, 0),
           COALESCE(MAX(src_bucket)::int, 0)
         )::int2
    INTO v_effective_bucket
  FROM _picked;

  IF v_effective_bucket IS NULL OR v_effective_bucket = 0 THEN
    -- No contributors selected: keep requested bucket or default to 5
    v_effective_bucket := COALESCE(v_session.requested_bucket_minutes, 5);
  END IF;

  UPDATE assembly.preview_snapshot
  SET bucket_minutes = v_effective_bucket
  WHERE assembly_session_id = p_session_id
    AND snapshot_seq = v_next_snapshot_seq;

  -- 13) Re-bucket the picked source rows up to v_effective_bucket
  --     (If src_bucket < target, aggregate; if equal, pass-through.)
  CREATE TEMP TABLE _picked_bucketed AS
  SELECT
    -- bucketed start:
    date_trunc('hour', p.interval_start)
      + make_interval(mins := (EXTRACT(MINUTE FROM p.interval_start)::int / v_effective_bucket) * v_effective_bucket)
      AS bucket_start,
    p.movement,
    p.selection_rule_id,
    p.rule_action,
    p.file_id,
    p.sdi_id,
    SUM(p.volume_count) AS volume_count,                      -- scalar sum when aggregating finer buckets
    -- For JSON, we sum per key later during assembly
    jsonb_agg(jsonb_build_object(
      'class', p.volume_count_by_class,
      'bank',  p.volume_count_by_bank
    )) AS jpayloads,
    p.src_dim,
    p.src_bank_schema_id,
    p.src_bucket,
    p.weight,
    p.tgt_dim_hint,
    p.tgt_bank_hint,
    p.category_include,
    p.category_exclude,
    p.priority
  FROM _picked p
  GROUP BY 1,2,3,4,5,6, p.src_dim, p.src_bank_schema_id, p.src_bucket, p.weight, p.tgt_dim_hint, p.tgt_bank_hint,
           p.category_include, p.category_exclude, p.priority;


	-- 14) Determine target category per bucketed cell: bank > class > none
	DROP TABLE IF EXISTS _cell_target;
	CREATE TEMP TABLE _cell_target AS
	SELECT
	  bucket_start,
	  movement,
	  BOOL_OR(src_dim = 'bank')  AS has_bank,
	  BOOL_OR(src_dim = 'class') AS has_class,
	  -- distinct bank schema stats among bank contributors
	  COUNT(DISTINCT CASE WHEN src_dim='bank' THEN src_bank_schema_id END) AS bank_schema_count,
	  MIN(CASE WHEN src_dim='bank' THEN src_bank_schema_id END)            AS only_bank_schema_id, -- valid only if count=1
	  -- final target fields (typed!)
	  CASE
	    WHEN BOOL_OR(src_dim = 'bank')  THEN 'bank'::assembly.category_dimension
	    WHEN BOOL_OR(src_dim = 'class') THEN 'class'::assembly.category_dimension
	    ELSE 'none'::assembly.category_dimension
	  END AS target_dim,
	  CASE
	    WHEN COUNT(DISTINCT CASE WHEN src_dim='bank' THEN src_bank_schema_id END) = 1
	      THEN MIN(CASE WHEN src_dim='bank' THEN src_bank_schema_id END)
	    ELSE NULL
	  END AS target_bank_schema_id
	FROM _picked_bucketed
	GROUP BY bucket_start, movement;

  -- Fail fast if mixed bank schemas would collide in a single cell
	PERFORM 1
	FROM _cell_target
	WHERE target_dim = 'bank'
	  AND target_bank_schema_id IS NULL;
	
	IF FOUND THEN
	  RAISE EXCEPTION 'Mixed bank schemas in same cell; split rules by bank schema.';
	END IF;

  -- 15) Assemble values per cell based on rule_action
  -- Normalize weights for weighted_merge; include/formula use proportion=1 per contributing binding.
  CREATE TEMP TABLE _weights AS
  SELECT
    bucket_start, movement, selection_rule_id, rule_action,
    SUM(weight) FILTER (WHERE rule_action='weighted_merge') AS sum_w
  FROM _picked_bucketed
  GROUP BY 1,2,3,4;

  CREATE TEMP TABLE _picked_norm AS
  SELECT
    p.bucket_start,
    p.movement,
    p.selection_rule_id,
    p.rule_action,
    p.file_id,
    p.sdi_id,
    CASE
      WHEN w.rule_action='weighted_merge' AND w.sum_w IS NOT NULL AND w.sum_w > 0
        THEN p.weight / w.sum_w
      ELSE 1.0
    END AS proportion,
    p.volume_count,
    p.jpayloads,
    p.src_dim,
    p.src_bank_schema_id,
    t.target_dim,
    t.target_bank_schema_id,
    p.category_include,
    p.category_exclude
  FROM _picked_bucketed p
  JOIN _weights w
    ON w.bucket_start     = p.bucket_start
   AND w.movement         = p.movement
   AND w.selection_rule_id= p.selection_rule_id
   AND w.rule_action      = p.rule_action
  JOIN _cell_target t
    ON t.bucket_start = p.bucket_start
   AND t.movement     = p.movement;

  -- Apply rule_action = formula_adjusted (simple scale support: {"scale": 0.98})
  -- We convert proportion := proportion * scale (applied once per binding)
  CREATE TEMP TABLE _picked_norm2 AS
  SELECT
    n.*,
    CASE
      WHEN r.rule_action='formula_adjusted'
       AND r.formula ? 'scale'
       AND (r.formula->>'scale')::numeric IS NOT NULL
      THEN n.proportion * (r.formula->>'scale')::numeric
      ELSE n.proportion
    END AS adj_proportion
  FROM _picked_norm n
  JOIN _rules r
    ON r.selection_rule_id = n.selection_rule_id;

  -- 16) Build assembled category JSON at target level (+ scalar)
  -- (A) explode per-binding payloads (no nested aggs)
	DROP TABLE IF EXISTS _exploded;
	CREATE TEMP TABLE _exploded AS
	SELECT
	  n.bucket_start,
	  n.movement,
	  n.selection_rule_id,
	  n.file_id,
	  n.sdi_id,
	  n.target_dim,
	  n.target_bank_schema_id,
	  n.adj_proportion AS prop,
	  -- category_json built in two steps per branch
	
	  -- BANK target, BANK source
	  CASE
	    WHEN n.target_dim = 'bank'::assembly.category_dimension
	     AND n.src_dim    = 'bank'::assembly.category_dimension
	    THEN (
	      WITH keyvals AS (
	        SELECT k::text AS key, ((pay->'bank'->>k)::bigint) AS v
	        FROM jsonb_array_elements(n.jpayloads) AS pay,
	             LATERAL jsonb_object_keys(pay->'bank') AS kk(k)
	      ),
	      summed AS (
	        SELECT key, SUM(v)::bigint AS total FROM keyvals GROUP BY key
	      )
	      SELECT jsonb_object_agg(key, total) FROM summed
	    )
	
	  -- BANK target, CLASS source (rollup)
	    WHEN n.target_dim = 'bank'::assembly.category_dimension
	     AND n.src_dim    = 'class'::assembly.category_dimension
	    THEN (
	      WITH keyvals AS (
	        SELECT k::text AS key, ((pay->'class'->>k)::bigint) AS v
	        FROM jsonb_array_elements(n.jpayloads) AS pay,
	             LATERAL jsonb_object_keys(pay->'class') AS kk(k)
	      ),
	      summed AS (
	        SELECT key, SUM(v)::bigint AS total FROM keyvals GROUP BY key
	      ),
	      class_json AS (
	        SELECT jsonb_object_agg(key, total) AS j FROM summed
	      )
	      SELECT taxonomy.rollup_class_json_to_bank_json((SELECT j FROM class_json), n.target_bank_schema_id)
	    )
	
	  -- CLASS target, CLASS source
	    WHEN n.target_dim = 'class'::assembly.category_dimension
	     AND n.src_dim    = 'class'::assembly.category_dimension
	    THEN (
	      WITH keyvals AS (
	        SELECT k::text AS key, ((pay->'class'->>k)::bigint) AS v
	        FROM jsonb_array_elements(n.jpayloads) AS pay,
	             LATERAL jsonb_object_keys(pay->'class') AS kk(k)
	      ),
	      summed AS (
	        SELECT key, SUM(v)::bigint AS total FROM keyvals GROUP BY key
	      )
	      SELECT jsonb_object_agg(key, total) FROM summed
	    )
	
	  ELSE NULL
	  END AS category_json,
	
	  n.volume_count AS scalar_total
	FROM _picked_norm2 n;
	
	-- (B) apply proportions per binding  — use e.prop, no join back to _picked_norm2
	DROP TABLE IF EXISTS _weighted_rows;
	CREATE TEMP TABLE _weighted_rows AS
	SELECT
	  e.bucket_start,
	  e.movement,
	  e.target_dim,
	  e.target_bank_schema_id,
	  (e.scalar_total * e.prop) AS w_scalar,
	  CASE
	    WHEN e.category_json IS NULL THEN NULL
	    ELSE (
	      WITH keys AS (
	        SELECT k::text AS key FROM jsonb_object_keys(e.category_json) AS kk(k)
	      )
	      SELECT jsonb_object_agg(keys.key, ((e.category_json->>keys.key)::numeric * e.prop))
	      FROM keys
	    )
	  END AS w_category
	FROM _exploded e;
	
	-- (C) scalar aggregation per cell
	DROP TABLE IF EXISTS _scalar_sum;
	CREATE TEMP TABLE _scalar_sum AS
	SELECT
	  bucket_start,
	  movement,
	  target_dim,
	  target_bank_schema_id,
	  ROUND(SUM(w_scalar))::int AS volume_count
	FROM _weighted_rows
	GROUP BY 1,2,3,4;
	
	-- (D1) explode category into key/val rows
	DROP TABLE IF EXISTS _cat_kv;
	CREATE TEMP TABLE _cat_kv AS
	SELECT
	  w.bucket_start,
	  w.movement,
	  w.target_dim,
	  w.target_bank_schema_id,
	  k::text AS key,
	  (w.w_category->>k)::numeric AS val
	FROM _weighted_rows w
	JOIN LATERAL jsonb_object_keys(w.w_category) AS kk(k) ON w.w_category IS NOT NULL;
	
	-- (D2) sum per key (no JSON yet)
	DROP TABLE IF EXISTS _cat_sum;
	CREATE TEMP TABLE _cat_sum AS
	SELECT
	  bucket_start,
	  movement,
	  target_dim,
	  target_bank_schema_id,
	  key,
	  SUM(val) AS total
	FROM _cat_kv
	GROUP BY 1,2,3,4,5;
	
	-- (D3) object_agg over summed rows (no nested aggs)
	DROP TABLE IF EXISTS _cat_json;
	CREATE TEMP TABLE _cat_json AS
	SELECT
	  bucket_start,
	  movement,
	  target_dim,
	  target_bank_schema_id,
	  jsonb_object_agg(key, ROUND(total)::int) AS category_breakdown
	FROM _cat_sum
	GROUP BY 1,2,3,4;
	
	-- (E) final assembled rows for movement path
	DROP TABLE IF EXISTS _assembled;
	CREATE TEMP TABLE _assembled AS
	SELECT
	  s.bucket_start,
	  s.movement,
	  s.volume_count,
	  -- only attach breakdown when target_dim is class or bank
	  CASE
	    WHEN s.target_dim IN ('class'::assembly.category_dimension, 'bank'::assembly.category_dimension)
	      THEN c.category_breakdown
	    ELSE NULL
	  END AS category_breakdown,
	  s.target_dim           AS category_dimension,
	  s.target_bank_schema_id AS bank_schema_id
	FROM _scalar_sum s
	LEFT JOIN _cat_json c
	  ON c.bucket_start = s.bucket_start
	 AND c.movement     = s.movement
	 AND c.target_dim   = s.target_dim
	 AND COALESCE(c.target_bank_schema_id,0) = COALESCE(s.target_bank_schema_id,0);

  -- 17) Apply category include/exclude filters from the top rule per cell (if any)
  CREATE TEMP TABLE _filters AS
  SELECT
    tr.interval_start AS bucket_start,
    tr.movement,
    tr.category_dimension,
    tr.bank_schema_id,
    tr.category_include,
    tr.category_exclude
  FROM _top_rule tr;

  CREATE TEMP TABLE _assembled_filtered AS
  SELECT
    a.bucket_start,
    a.movement,
    CASE
      WHEN a.category_dimension IN ('class','bank') AND (f.category_include IS NOT NULL OR f.category_exclude IS NOT NULL)
      THEN (SELECT filtered FROM taxonomy.filter_category_json(a.category_breakdown, f.category_include, f.category_exclude))
      ELSE a.category_breakdown
    END AS category_breakdown,
    CASE
      WHEN a.category_dimension IN ('class','bank') AND (f.category_include IS NOT NULL OR f.category_exclude IS NOT NULL)
      THEN COALESCE((SELECT total FROM taxonomy.filter_category_json(a.category_breakdown, f.category_include, f.category_exclude)), a.volume_count)
      ELSE a.volume_count
    END AS volume_count,
    a.category_dimension,
    a.bank_schema_id
  FROM _assembled a
  LEFT JOIN _filters f
    ON f.bucket_start = a.bucket_start
   AND f.movement     = a.movement;

  -- 18) Manual overrides (movement)
  CREATE TEMP TABLE _overrides AS
  SELECT
    o.interval_start AS bucket_start,
    o.movement,
    o.manual_override_id,
    o.volume_count,
    COALESCE(o.category_breakdown, o.volume_count_by_class) AS category_breakdown -- back-compat
  FROM assembly.manual_override o
  WHERE o.assembly_session_id = p_session_id
    AND o.count_data_type = 'movement'
    AND o.interval_start >= v_session.interval_start
    AND o.interval_start <  v_session.interval_end;

  -- 19) Final rows + lineage arrays
  CREATE TEMP TABLE _final AS
  SELECT
    a.bucket_start,
    a.movement,
    COALESCE(o.volume_count, a.volume_count) AS volume_count,
    -- if override supplies breakdown, use it
    COALESCE(o.category_breakdown, a.category_breakdown) AS category_breakdown,
    a.category_dimension,
    a.bank_schema_id,
    CASE WHEN o.manual_override_id IS NOT NULL
         THEN jsonb_build_array(to_jsonb(o.manual_override_id))
         ELSE '[]'::jsonb
    END AS applied_overrides
  FROM _assembled_filtered a
  LEFT JOIN _overrides o
    ON o.bucket_start = a.bucket_start
   AND o.movement     = a.movement;

  -- lineage: collect contributors & applied rules for each final cell
  -- We rebuild contributors from _picked_norm2 at the chosen bucket level.
  CREATE TEMP TABLE _contributors AS
  SELECT
    n.bucket_start,
    n.movement,
    jsonb_agg(jsonb_build_object(
      'file_id', n.file_id,
      'sdi_id',  n.sdi_id,
      'interval_start', n.bucket_start,
      'movement', n.movement,
      'proportion', n.adj_proportion
    )) AS contributors,
    jsonb_agg(DISTINCT to_jsonb(n.selection_rule_id)) AS applied_rules
  FROM _picked_norm2 n
  GROUP BY n.bucket_start, n.movement;

  -- 20) Insert preview rows
  INSERT INTO assembly.preview_volume_by_movement
    (assembly_session_id, snapshot_seq, interval_start, movement,
     volume_count, volume_count_by_class, category_dimension, bank_schema_id, category_breakdown, lineage_key, is_missing)
  SELECT
    p_session_id, v_next_snapshot_seq, f.bucket_start, f.movement,
    f.volume_count,
    CASE WHEN f.category_dimension='class' THEN f.category_breakdown ELSE NULL END,
    f.category_dimension, f.bank_schema_id, f.category_breakdown,
    gen_random_uuid(), false
  FROM _final f
  ON CONFLICT ON CONSTRAINT uq_prev_mv_cell
  DO UPDATE SET
    volume_count = EXCLUDED.volume_count,
    volume_count_by_class = EXCLUDED.volume_count_by_class,
    category_dimension = EXCLUDED.category_dimension,
    bank_schema_id = EXCLUDED.bank_schema_id,
    category_breakdown = EXCLUDED.category_breakdown,
    lineage_key = EXCLUDED.lineage_key,
    is_missing = false;

  -- 21) Lineage: map lineage_key and store arrays
  CREATE TEMP TABLE _lk_map AS
  SELECT interval_start, movement, lineage_key
  FROM assembly.preview_volume_by_movement
  WHERE assembly_session_id = p_session_id
    AND snapshot_seq = v_next_snapshot_seq;

  INSERT INTO assembly.preview_lineage
    (assembly_session_id, snapshot_seq, lineage_key, contributors, applied_rules, applied_overrides)
  SELECT
    p_session_id, v_next_snapshot_seq, m.lineage_key,
    c.contributors, c.applied_rules, f.applied_overrides
  FROM _lk_map m
  JOIN _contributors c
    ON c.bucket_start = m.interval_start AND c.movement = m.movement
  JOIN _final f
    ON f.bucket_start = m.interval_start AND f.movement = m.movement;

  -- Success
  RETURN v_next_snapshot_seq;
END;
$function$
;

-- DROP FUNCTION assembly.compute_preview_snapshot_blank(uuid, _text, _int4, bool, text);

CREATE OR REPLACE FUNCTION assembly.compute_preview_snapshot_blank(p_session_id uuid, p_movements text[], p_speed_bins integer[] DEFAULT NULL::integer[], p_include_speed_layer boolean DEFAULT false, p_derived_by text DEFAULT 'assembler_v2'::text)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
  v_sess                 assembly.assembly_session;
  v_bucket_minutes       int2;
  v_target_bin_scheme_id int4;
  v_next_seq             int;
BEGIN
  -- Load & lock session
  SELECT * INTO v_sess
  FROM assembly.assembly_session
  WHERE assembly_session_id = p_session_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'assembly_session % not found', p_session_id;
  END IF;

  IF p_movements IS NULL OR array_length(p_movements,1) IS NULL THEN
    RAISE EXCEPTION 'p_movements cannot be empty';
  END IF;

  v_bucket_minutes       := COALESCE(v_sess.requested_bucket_minutes, 5);
  v_target_bin_scheme_id := v_sess.target_bin_scheme_id;
  IF v_target_bin_scheme_id IS NULL THEN
    RAISE EXCEPTION 'target_bin_scheme_id is required on assembly_session %', p_session_id;
  END IF;

  -- Next snapshot seq
  SELECT COALESCE(MAX(snapshot_seq), 0) + 1
  INTO v_next_seq
  FROM assembly.preview_snapshot
  WHERE assembly_session_id = p_session_id;

  -- Create snapshot header
  INSERT INTO assembly.preview_snapshot (
    assembly_session_id, snapshot_seq, generated_at, derived_by,
    bucket_minutes, target_bin_scheme_id
  )
  VALUES (
    p_session_id, v_next_seq, now(), p_derived_by,
    v_bucket_minutes, v_target_bin_scheme_id
  );

  -- Generate time buckets
  WITH t_series AS (
    SELECT
      generate_series(
        v_sess.interval_start,
        v_sess.interval_end - make_interval(mins => v_bucket_minutes)::interval,
        make_interval(mins => v_bucket_minutes)::interval
      )::timestamp AS bucket_start
  ),
  mv AS (
    SELECT unnest(p_movements) AS movement
  )
  -- Seed movement grid (zeros; marked missing)
  INSERT INTO assembly.preview_volume_by_movement (
    assembly_session_id, snapshot_seq, movement,
    volume_count, volume_count_by_class,
    category_dimension, bank_schema_id, category_breakdown,
    lineage_key, interval_start, is_missing
  )
  SELECT
    p_session_id,
    v_next_seq,
    mv.movement,
    0 AS volume_count,
    NULL::jsonb AS volume_count_by_class,
    'bank'::assembly.category_dimension AS category_dimension, -- target is banked; no source schema here
    NULL::int4 AS bank_schema_id,
    NULL::jsonb AS category_breakdown,
    gen_random_uuid() AS lineage_key,
    ts.bucket_start AS interval_start,
    true AS is_missing
  FROM t_series ts
  CROSS JOIN mv
  ON CONFLICT (assembly_session_id, snapshot_seq, interval_start, movement) DO NOTHING;

  -- Optionally seed speed grid
  IF p_include_speed_layer AND p_speed_bins IS NOT NULL AND array_length(p_speed_bins,1) IS NOT NULL THEN
    WITH t_series AS (
      SELECT
        generate_series(
          v_sess.interval_start,
          v_sess.interval_end - make_interval(mins => v_bucket_minutes)::interval,
          make_interval(mins => v_bucket_minutes)::interval
        )::timestamp AS bucket_start
    ),
    mv AS (
      SELECT unnest(p_movements) AS movement
    ),
    sp AS (
      SELECT unnest(p_speed_bins) AS speed_mph
    )
    INSERT INTO assembly.preview_volume_by_speed (
      assembly_session_id, snapshot_seq, movement, speed_mph,
      vehicle_count, vehicle_count_by_class,
      category_dimension, bank_schema_id, category_breakdown,
      lineage_key, interval_start, is_missing
    )
    SELECT
      p_session_id,
      v_next_seq,
      mv.movement,
      sp.speed_mph,
      0 AS vehicle_count,
      NULL::jsonb AS vehicle_count_by_class,
      'none'::assembly.category_dimension AS category_dimension, -- speed layer not banked
      NULL::int4 AS bank_schema_id,
      NULL::jsonb AS category_breakdown,
      gen_random_uuid() AS lineage_key,
      ts.bucket_start AS interval_start,
      true AS is_missing
    FROM t_series ts
    CROSS JOIN mv
    CROSS JOIN sp
    ON CONFLICT (assembly_session_id, snapshot_seq, interval_start, movement, speed_mph) DO NOTHING;
  END IF;

  RETURN v_next_seq;
END;
$function$
;

-- DROP FUNCTION assembly.compute_preview_snapshot_blank_v2(uuid, _text, jsonb);

CREATE OR REPLACE FUNCTION assembly.compute_preview_snapshot_blank_v2(p_session_id uuid, p_movements text[] DEFAULT NULL::text[], p_speed_bins jsonb DEFAULT NULL::jsonb)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
DECLARE
  s              assembly.assembly_session;
  next_seq       int;
  bucket_min     int;
  ts_start       timestamp;
  ts_end         timestamp;
  -- movements / bins
  mvts           text[];
  bins           jsonb;
  -- banks
  banks_breakdown jsonb := '{}'::jsonb;
  _scheme_id      int;
BEGIN
  SELECT * INTO s
  FROM assembly.assembly_session
  WHERE assembly_session_id = p_session_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'assembly_session % not found', p_session_id;
  END IF;

  bucket_min := COALESCE(s.requested_bucket_minutes, 5);
  ts_start   := s.interval_start;
  ts_end     := s.interval_end;
  _scheme_id  := s.target_bin_scheme_id;

  -- Build default per-bank breakdown object:
  -- { "<bank_id>": {"count": null}, ... }
  IF _scheme_id IS NOT NULL THEN
    SELECT COALESCE(
      jsonb_object_agg(b.id::text, jsonb_build_object('count', NULL)),
      '{}'::jsonb
    )
    INTO banks_breakdown
    FROM ops_config.bin_scheme_banks b
    WHERE b.scheme_id = _scheme_id;
  END IF;

  -- next snapshot seq
  SELECT COALESCE(MAX(snapshot_seq),0)+1 INTO next_seq
  FROM assembly.preview_snapshot
  WHERE assembly_session_id = p_session_id;

  INSERT INTO assembly.preview_snapshot(assembly_session_id, snapshot_seq, bucket_minutes, derived_by)
  VALUES (p_session_id, next_seq, bucket_min, 'compute_preview_snapshot_blank_v4');

  -- ########## movement columns
  IF p_movements IS NOT NULL THEN
    mvts := p_movements;
  ELSE
    -- derive from session flags (same directional/RTOR/ped logic you have now)
    -- (for brevity, reuse your v3 derivation here)
    mvts := ARRAY[]::text[];
    IF s.study_type = 'tmc' THEN
      mvts := ARRAY[
        'NB Thru','NB Left','NB Right','NB U-turn',
        'SB Thru','SB Left','SB Right','SB U-turn',
        'EB Thru','EB Left','EB Right','EB U-turn',
        'WB Thru','WB Left','WB Right','WB U-turn'
      ];
      IF s.include_rtor THEN
        mvts := mvts || ARRAY['NB RTOR','SB RTOR','EB RTOR','WB RTOR'];
      END IF;
      IF s.include_pedestrians THEN
        IF s.directional_pedestrians THEN
          mvts := mvts || ARRAY['E - NS','E - SN','W - NS','W - SN','N - EW','N - WE','S - EW','S - WE'];
        ELSE
          mvts := mvts || ARRAY['E Leg','W Leg','N Leg','S Leg'];
        END IF;
      END IF;
    ELSE
      IF s.direction_axis IS NULL THEN s.direction_axis := 'ns'; END IF;
      IF s.direction_axis = 'ns' THEN
        mvts := ARRAY['NB Thru','SB Thru'];
        IF s.include_pedestrians THEN
          mvts := mvts || CASE WHEN s.directional_pedestrians
            THEN ARRAY['E - NS','E - SN','W - NS','W - SN']
            ELSE ARRAY['E Leg','W Leg'] END;
        END IF;
      ELSE
        mvts := ARRAY['EB Thru','WB Thru'];
        IF s.include_pedestrians THEN
          mvts := mvts || CASE WHEN s.directional_pedestrians
            THEN ARRAY['N - EW','N - WE','S - EW','S - WE']
            ELSE ARRAY['N Leg','S Leg'] END;
        END IF;
      END IF;
    END IF;
  END IF;

  -- seed movement grid (note: per-bank breakdown prefilled)
  INSERT INTO assembly.preview_volume_by_movement(
    assembly_session_id, snapshot_seq, movement,
    volume_count, volume_count_by_class,
    category_dimension, bank_schema_id, category_breakdown,
    interval_start, is_missing
  )
  SELECT
    p_session_id, next_seq, m.movement,
    0, NULL,
    'bank'::assembly.category_dimension, _scheme_id, banks_breakdown,
    t.ts, true
  FROM (
    SELECT generate_series(ts_start, ts_end - (bucket_min||' minutes')::interval, (bucket_min||' minutes')::interval) AS ts
  ) t
  CROSS JOIN LATERAL unnest(mvts) AS m(movement);

  -- ########## speed bins
  IF s.count_data_type IN ('speed','both') THEN
    bins := COALESCE(p_speed_bins, s.target_speed_bins);

    IF bins IS NULL OR jsonb_typeof(bins->'bins') <> 'array' THEN
      -- fallback default
      bins := jsonb_build_object(
        'bins', to_jsonb(ARRAY[
          jsonb_build_object('from',0,'to',0,'label','0'),
          jsonb_build_object('from',1,'to',10,'label','1-10'),
          jsonb_build_object('from',11,'to',20,'label','11-20'),
          jsonb_build_object('from',21,'to',30,'label','21-30'),
          jsonb_build_object('from',31,'to',40,'label','31-40'),
          jsonb_build_object('from',41,'to',50,'label','41-50'),
          jsonb_build_object('from',51,'to',60,'label','51-60'),
          jsonb_build_object('from',61,'to',70,'label','61-70'),
          jsonb_build_object('from',71,'to',80,'label','71-80'),
          jsonb_build_object('from',81,'to',90,'label','81-90'),
          jsonb_build_object('from',91,'to',NULL,'label','91+')
        ])
      );
    END IF;

    INSERT INTO assembly.preview_volume_by_speed(
      assembly_session_id, snapshot_seq, movement, speed_mph, speed_bin,
      vehicle_count, vehicle_count_by_class,
      category_dimension, bank_schema_id, category_breakdown,
      interval_start, is_missing
    )
    SELECT
      p_session_id, next_seq, '~',
      (b->>'from')::int,
      b,
      0, NULL,
      'bank'::assembly.category_dimension, _scheme_id, banks_breakdown,
      t.ts, true
    FROM (
      SELECT generate_series(ts_start, ts_end - (bucket_min||' minutes')::interval, (bucket_min||' minutes')::interval) AS ts
    ) t,
    LATERAL jsonb_array_elements(bins->'bins') AS b;
  END IF;

  RETURN next_seq;
END;
$function$
;
