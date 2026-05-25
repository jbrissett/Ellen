-- DROP SCHEMA index_schema;

CREATE SCHEMA index_schema AUTHORIZATION tcmsdbadm;

-- DROP SEQUENCE index_schema.collection_site_sources_collection_site_source_id_seq;

CREATE SEQUENCE index_schema.collection_site_sources_collection_site_source_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE index_schema.collection_sites_collection_site_id_seq;

CREATE SEQUENCE index_schema.collection_sites_collection_site_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE index_schema.source_types_source_type_id_seq;

CREATE SEQUENCE index_schema.source_types_source_type_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;-- index_schema.collection_sites definition

-- Drop table

-- DROP TABLE index_schema.collection_sites;

CREATE TABLE index_schema.collection_sites (
	collection_site_id bigserial NOT NULL,
	location_name varchar(255) NULL,
	location_type varchar(50) NULL,
	latitude numeric NULL,
	longitude numeric NULL,
	point_geometry public.geometry(point, 4326) NULL,
	road_geometry public.geometry(linestring, 4326) NULL,
	other_geometry public.geometry(polygon, 4326) NULL,
	place_id varchar(50) NULL,
	osm_id varchar(50) NULL,
	osm_type varchar(50) NULL,
	osm_attributes jsonb NULL,
	"createdAt" timestamptz DEFAULT now() NOT NULL,
	"updatedAt" timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT collection_sites_pkey PRIMARY KEY (collection_site_id)
);
CREATE INDEX idx_collection_sites_point_geometry ON index_schema.collection_sites USING gist (point_geometry);


-- index_schema.roadway_anchor definition

-- Drop table

-- DROP TABLE index_schema.roadway_anchor;

CREATE TABLE index_schema.roadway_anchor (
	anchor_id uuid NOT NULL,
	kind roadway_config.rc_site_kind NOT NULL,
	canonical_point public.geometry(point, 4326) NOT NULL,
	canonical_bbox public.geometry(polygon, 4326) NULL,
	primary_name text NULL,
	alt_names _text NULL,
	created_by text NULL,
	created_at timestamptz DEFAULT now() NULL,
	notes text NULL,
	is_provisional bool DEFAULT false NOT NULL,
	provisional_seed_source text NULL,
	provisional_confidence numeric NULL,
	provisional_seeded_at timestamptz NULL,
	provisional_seed_key text NULL,
	updated_at timestamptz NULL,
	anchor_provenance jsonb NULL,
	is_locked bool DEFAULT false NULL,
	CONSTRAINT roadway_anchor_pkey PRIMARY KEY (anchor_id)
);
CREATE INDEX ix_anchor_geom ON index_schema.roadway_anchor USING gist (canonical_point);
CREATE INDEX ix_anchor_provisional ON index_schema.roadway_anchor USING btree (is_provisional);


-- index_schema.source_types definition

-- Drop table

-- DROP TABLE index_schema.source_types;

CREATE TABLE index_schema.source_types (
	source_type_id serial4 NOT NULL,
	type_name varchar(50) NOT NULL,
	description text NULL,
	json_schema jsonb NULL,
	CONSTRAINT source_types_pkey PRIMARY KEY (source_type_id),
	CONSTRAINT source_types_type_name_key UNIQUE (type_name)
);


-- index_schema.collection_site_anchor_map definition

-- Drop table

-- DROP TABLE index_schema.collection_site_anchor_map;

CREATE TABLE index_schema.collection_site_anchor_map (
	collection_site_id int8 NOT NULL,
	anchor_id uuid NOT NULL,
	"source" text NOT NULL,
	confidence numeric NULL,
	is_locked bool DEFAULT false NULL,
	linked_by text NULL,
	linked_at timestamptz DEFAULT now() NULL,
	match_confidence numeric NULL,
	match_method text NULL,
	match_explanation text NULL,
	is_approved bool DEFAULT false NULL,
	CONSTRAINT collection_site_anchor_map_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
	CONSTRAINT collection_site_anchor_map_pkey PRIMARY KEY (collection_site_id),
	CONSTRAINT collection_site_anchor_map_source_check CHECK ((source = ANY (ARRAY['auto'::text, 'import'::text, 'manual'::text]))),
	CONSTRAINT collection_site_anchor_map_anchor_id_fkey FOREIGN KEY (anchor_id) REFERENCES index_schema.roadway_anchor(anchor_id) ON DELETE CASCADE,
	CONSTRAINT collection_site_anchor_map_collection_site_id_fkey FOREIGN KEY (collection_site_id) REFERENCES index_schema.collection_sites(collection_site_id) ON DELETE CASCADE
);
CREATE INDEX ix_csam_anchor ON index_schema.collection_site_anchor_map USING btree (anchor_id);


-- index_schema.collection_site_sources definition

-- Drop table

-- DROP TABLE index_schema.collection_site_sources;

CREATE TABLE index_schema.collection_site_sources (
	collection_site_source_id serial4 NOT NULL,
	collection_site_id int8 NOT NULL,
	source_type_id int4 NOT NULL,
	source_attributes jsonb NULL,
	"createdAt" timestamptz DEFAULT now() NOT NULL,
	"updatedAt" timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT collection_site_sources_pkey PRIMARY KEY (collection_site_source_id),
	CONSTRAINT collection_site_sources_collection_site_id_fkey FOREIGN KEY (collection_site_id) REFERENCES index_schema.collection_sites(collection_site_id) ON DELETE CASCADE,
	CONSTRAINT collection_site_sources_source_type_id_fkey FOREIGN KEY (source_type_id) REFERENCES index_schema.source_types(source_type_id) ON DELETE CASCADE
);
CREATE INDEX idx_source_attributes_gin ON index_schema.collection_site_sources USING gin (source_attributes jsonb_path_ops);
CREATE INDEX idx_source_attributes_order_no ON index_schema.collection_site_sources USING btree (((source_attributes ->> 'order_no'::text)));


-- index_schema.anchor_corridor_map definition

-- Drop table

-- DROP TABLE index_schema.anchor_corridor_map;

CREATE TABLE index_schema.anchor_corridor_map (
	anchor_id uuid NOT NULL,
	corridor_segment_id uuid NOT NULL,
	"role" text NOT NULL,
	confidence numeric(4, 3) NOT NULL,
	status text DEFAULT 'provisional'::text NOT NULL,
	match_explanation jsonb NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	created_by text NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT anchor_corridor_map_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
	CONSTRAINT anchor_corridor_map_pkey PRIMARY KEY (anchor_id, corridor_segment_id, role),
	CONSTRAINT anchor_corridor_map_role_check CHECK ((role = ANY (ARRAY['through'::text, 'crossing'::text]))),
	CONSTRAINT anchor_corridor_map_status_check CHECK ((status = ANY (ARRAY['provisional'::text, 'confirmed'::text, 'rejected'::text])))
);
CREATE INDEX ix_acm_status_conf ON index_schema.anchor_corridor_map USING btree (status, confidence DESC);


-- index_schema.anchor_road definition

-- Drop table

-- DROP TABLE index_schema.anchor_road;

CREATE TABLE index_schema.anchor_road (
	anchor_id uuid NOT NULL,
	road_id int8 NOT NULL,
	"role" text NOT NULL,
	snapped_way_id int8 NULL,
	CONSTRAINT anchor_road_pkey PRIMARY KEY (anchor_id, road_id, role),
	CONSTRAINT anchor_road_role_check CHECK ((role = ANY (ARRAY['through'::text, 'crossing'::text, 'adjacent'::text])))
);


-- index_schema.anchor_corridor_map foreign keys

ALTER TABLE index_schema.anchor_corridor_map ADD CONSTRAINT anchor_corridor_map_anchor_id_fkey FOREIGN KEY (anchor_id) REFERENCES index_schema.roadway_anchor(anchor_id) ON DELETE CASCADE;
ALTER TABLE index_schema.anchor_corridor_map ADD CONSTRAINT anchor_corridor_map_corridor_segment_id_fkey FOREIGN KEY (corridor_segment_id) REFERENCES taxonomy.corridor_segment(corridor_segment_id) ON DELETE CASCADE;


-- index_schema.anchor_road foreign keys

ALTER TABLE index_schema.anchor_road ADD CONSTRAINT anchor_road_anchor_id_fkey FOREIGN KEY (anchor_id) REFERENCES index_schema.roadway_anchor(anchor_id) ON DELETE CASCADE;
ALTER TABLE index_schema.anchor_road ADD CONSTRAINT anchor_road_road_id_fkey FOREIGN KEY (road_id) REFERENCES taxonomy.road(road_id) ON DELETE RESTRICT;


-- index_schema.publishing_index definition

-- Drop table

-- DROP TABLE index_schema.publishing_index;

CREATE TABLE index_schema.publishing_index (
	publishing_index_id bigserial NOT NULL,
	source_system text NOT NULL,
	source_table text NOT NULL,
	source_record_id text NULL,
	source_updated_at timestamptz NULL,
	published_at timestamptz NULL,
	order_no int4 NULL,
	site_number int4 NULL,
	data_type text NOT NULL,
	order_location_id int4 NULL,
	qc_office_id int4 NULL,
	qc_office_name text NULL,
	customer_id int4 NULL,
	customer_name text NULL,
	collection_site_id int8 NULL,
	anchor_id uuid NULL,
	publishing_station_id uuid NULL,
	published_summary_id uuid NULL,
	location_name text NULL,
	latitude numeric NULL,
	longitude numeric NULL,
	city text NULL,
	state text NULL,
	point_geometry public.geometry(point, 4326) NULL,
	count_coverage_start timestamp NULL,
	count_coverage_end timestamp NULL,
	count_coverage_range tsrange GENERATED ALWAYS AS (tsrange(count_coverage_start, count_coverage_end, '[)'::text)) STORED,
	bin_scheme_id text NULL,
	has_turn_counts bool DEFAULT false NOT NULL,
	has_tube_counts bool DEFAULT false NOT NULL,
	has_survey_counts bool DEFAULT false NOT NULL,
	is_superseded bool DEFAULT false NOT NULL,
	superseded_by_id int8 NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT publishing_index_pkey PRIMARY KEY (publishing_index_id),
	CONSTRAINT publishing_index_source_system_check CHECK ((source_system = ANY (ARRAY['QCHUB'::text, 'DATALAKE'::text]))),
	CONSTRAINT publishing_index_data_type_check CHECK ((data_type = ANY (ARRAY['intersection'::text, 'midblock'::text, 'study'::text]))),
	CONSTRAINT publishing_index_source_table_check CHECK ((source_table = ANY (ARRAY['orderlocationtime'::text, 'legacycounts'::text, 'legacytubecounts'::text, 'legacysurveycounts'::text, 'publishing_station'::text, 'published_summary'::text])))
);
CREATE INDEX ix_pub_index_order_site ON index_schema.publishing_index USING btree (order_no, site_number);
CREATE INDEX ix_pub_index_data_type ON index_schema.publishing_index USING btree (data_type);
CREATE INDEX ix_pub_index_qc_office ON index_schema.publishing_index USING btree (qc_office_id);
CREATE INDEX ix_pub_index_customer ON index_schema.publishing_index USING btree (customer_id);
CREATE INDEX ix_pub_index_anchor ON index_schema.publishing_index USING btree (anchor_id);
CREATE INDEX ix_pub_index_collection_site ON index_schema.publishing_index USING btree (collection_site_id);
CREATE INDEX ix_pub_index_published_at ON index_schema.publishing_index USING btree (published_at DESC);
CREATE INDEX ix_pub_index_coverage_range ON index_schema.publishing_index USING gist (count_coverage_range);
CREATE INDEX ix_pub_index_geom ON index_schema.publishing_index USING gist (point_geometry);
CREATE INDEX ix_pub_index_source_table ON index_schema.publishing_index USING btree (source_table);
CREATE UNIQUE INDEX ux_pub_index_source_order_site_type ON index_schema.publishing_index USING btree (source_system, order_no, site_number, data_type);


-- index_schema.publishing_index_recon_run definition

-- Drop table

-- DROP TABLE index_schema.publishing_index_recon_run;

CREATE TABLE index_schema.publishing_index_recon_run (
	run_id uuid NOT NULL,
	source_system text NOT NULL,
	source_table text NOT NULL,
	window_start timestamptz NULL,
	window_end timestamptz NULL,
	watermark_before timestamptz NULL,
	watermark_after timestamptz NULL,
	rows_scanned int8 DEFAULT 0 NOT NULL,
	rows_inserted int8 DEFAULT 0 NOT NULL,
	rows_updated int8 DEFAULT 0 NOT NULL,
	rows_superseded int8 DEFAULT 0 NOT NULL,
	rows_skipped int8 DEFAULT 0 NOT NULL,
	rows_error int8 DEFAULT 0 NOT NULL,
	run_status text NOT NULL,
	run_notes text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT publishing_index_recon_run_pkey PRIMARY KEY (run_id),
	CONSTRAINT publishing_index_recon_run_status_check CHECK ((run_status = ANY (ARRAY['completed'::text, 'partial'::text, 'failed'::text]))),
	CONSTRAINT publishing_index_recon_run_source_system_check CHECK ((source_system = ANY (ARRAY['QCHUB'::text, 'DATALAKE'::text]))),
	CONSTRAINT publishing_index_recon_run_source_table_check CHECK ((source_table = ANY (ARRAY['orderlocationtime'::text, 'legacycounts'::text, 'legacytubecounts'::text, 'legacysurveycounts'::text, 'publishing_station'::text, 'published_summary'::text])))
);
