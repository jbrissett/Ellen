-- DROP SCHEMA raw_metadata_catalog;

CREATE SCHEMA raw_metadata_catalog AUTHORIZATION tcmsdbadm;

-- DROP SEQUENCE raw_metadata_catalog.data_types_data_type_id_seq;

CREATE SEQUENCE raw_metadata_catalog.data_types_data_type_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE raw_metadata_catalog.files_file_id_seq;

CREATE SEQUENCE raw_metadata_catalog.files_file_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE raw_metadata_catalog.format_registration_count_files_id_seq;

CREATE SEQUENCE raw_metadata_catalog.format_registration_count_files_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;-- raw_metadata_catalog.data_types definition

-- Drop table

-- DROP TABLE raw_metadata_catalog.data_types;

CREATE TABLE raw_metadata_catalog.data_types (
	data_type_id bigserial NOT NULL,
	"name" varchar(50) NOT NULL,
	description varchar(250) NOT NULL,
	regex text NULL,
	format_type text NULL,
	expected_columns jsonb NULL,
	classification_rules jsonb NULL,
	ai_signature_vector bytea NULL,
	CONSTRAINT data_types_pkey PRIMARY KEY (data_type_id)
);


-- raw_metadata_catalog.format_registration_count_files definition

-- Drop table

-- DROP TABLE raw_metadata_catalog.format_registration_count_files;

CREATE TABLE raw_metadata_catalog.format_registration_count_files (
	id bigserial NOT NULL,
	format_id text NOT NULL,
	ftype text NOT NULL,
	sample_id text NULL,
	source_bucket text NOT NULL,
	source_key text NOT NULL,
	repo_bucket text NOT NULL,
	hash_manifest_key text NOT NULL,
	vector_bucket text NOT NULL,
	vector_index text NOT NULL,
	vector_key text NOT NULL,
	props jsonb DEFAULT '{}'::jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	missing_attributes jsonb NULL,
	step_function_arn text NULL,
	CONSTRAINT format_registration_count_files_pkey PRIMARY KEY (id),
	CONSTRAINT uq_format UNIQUE (format_id)
);


-- raw_metadata_catalog.pending_file_formats definition

-- Drop table

-- DROP TABLE raw_metadata_catalog.pending_file_formats;

CREATE TABLE raw_metadata_catalog.pending_file_formats (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	order_no text NOT NULL,
	sitecode text NOT NULL,
	original_bucket text NOT NULL,
	original_key text NOT NULL,
	fallback_key text NULL,
	file_preview text NULL,
	file_type text NULL,
	ingestion_timestamp timestamptz DEFAULT now() NULL,
	triage_status text DEFAULT 'pending'::text NULL,
	triage_notes text NULL,
	suggested_format jsonb NULL,
	CONSTRAINT pending_file_formats_pkey PRIMARY KEY (id)
);


-- raw_metadata_catalog.stage_______files definition

-- Drop table

-- DROP TABLE raw_metadata_catalog.stage_______files;

CREATE UNLOGGED TABLE raw_metadata_catalog.stage_______files (
	order_no int8 NULL,
	sitecode int8 NULL,
	collection_site_id int8 NULL,
	data_type text NULL,
	"source" text NULL,
	original_bucket text NULL,
	original_key text NULL,
	target_bucket text NULL,
	target_key text NULL,
	target_parquet_key text NULL,
	creation_date timestamp NULL,
	data_start_date timestamp NULL,
	data_end_date timestamp NULL,
	data_interval int4 NULL,
	qc_office text NULL,
	customer text NULL,
	source_specific_metadata text NULL,
	count_parameters text NULL,
	ingestion_timestamp timestamp NULL,
	file_status text NULL,
	file_checksum text NULL,
	file_size int8 NULL,
	"version" int4 NULL,
	error_message text NULL,
	qa_notes text NULL,
	last_updated timestamp NULL
);


-- raw_metadata_catalog.file_summary_capability definition

-- Drop table

-- DROP TABLE raw_metadata_catalog.file_summary_capability;

CREATE TABLE raw_metadata_catalog.file_summary_capability (
	file_id int8 NOT NULL,
	category_dimension assembly.category_dimension NOT NULL,
	bin_scheme_id int8 NULL,
	class_schema_tag text NULL,
	bucket_minutes int2 DEFAULT 5 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT file_cap_bucket_check CHECK ((bucket_minutes = ANY (ARRAY[1, 5, 10, 15, 30, 60]))),
	CONSTRAINT file_summary_capability_pkey PRIMARY KEY (file_id)
);
CREATE INDEX ix_filecap_bucket ON raw_metadata_catalog.file_summary_capability USING btree (bucket_minutes);
CREATE INDEX ix_filecap_category ON raw_metadata_catalog.file_summary_capability USING btree (category_dimension, bin_scheme_id);


-- raw_metadata_catalog.files definition

-- Drop table

-- DROP TABLE raw_metadata_catalog.files;

CREATE TABLE raw_metadata_catalog.files (
	file_id bigserial NOT NULL,
	order_no varchar(50) NOT NULL,
	sitecode varchar(50) NOT NULL,
	collection_site_id int8 NULL,
	data_type varchar(50) NOT NULL,
	"source" varchar(50) NOT NULL,
	original_bucket varchar(255) NOT NULL,
	original_key varchar(1024) NOT NULL,
	target_bucket varchar(255) NOT NULL,
	target_key varchar(1024) NOT NULL,
	target_parquet_key varchar(1024) NULL,
	creation_date timestamptz NOT NULL,
	data_start_date timestamptz NULL,
	data_end_date timestamptz NULL,
	data_interval varchar(50) NULL,
	qc_office varchar(100) NULL,
	customer varchar(100) NULL,
	source_specific_metadata jsonb NULL,
	count_parameters jsonb NULL,
	ingestion_timestamp timestamptz DEFAULT now() NOT NULL,
	file_status varchar(50) NOT NULL,
	file_checksum varchar(100) NULL,
	file_size int8 NULL,
	"version" int4 DEFAULT 1 NULL,
	error_message text NULL,
	qa_notes text NULL,
	last_updated timestamptz DEFAULT now() NULL,
	checksum_sha256 text NULL,
	s3_version_id text NULL,
	uploader_user_id text NULL,
	upload_session_id text NULL,
	device_id text NULL,
	original_version_id text NULL,
	CONSTRAINT files_pkey PRIMARY KEY (file_id)
);


-- raw_metadata_catalog.file_summary_capability foreign keys

ALTER TABLE raw_metadata_catalog.file_summary_capability ADD CONSTRAINT file_summary_capability_bank_schema_id_fkey FOREIGN KEY (bin_scheme_id) REFERENCES ops_config.bin_schemes(id);
ALTER TABLE raw_metadata_catalog.file_summary_capability ADD CONSTRAINT file_summary_capability_file_id_fkey FOREIGN KEY (file_id) REFERENCES raw_metadata_catalog.files(file_id) ON DELETE CASCADE;


-- raw_metadata_catalog.files foreign keys

ALTER TABLE raw_metadata_catalog.files ADD CONSTRAINT fk_collection_site FOREIGN KEY (collection_site_id) REFERENCES index_schema.collection_sites(collection_site_id);

CREATE TABLE IF NOT EXISTS raw_metadata_catalog.file_artifacts (
  file_artifact_id bigserial PRIMARY KEY,
  file_id bigint NOT NULL,
  artifact_type raw_metadata_catalog.file_artifact_type NOT NULL DEFAULT 'trajectory_bundle',
  video_file_segment_id integer NOT NULL,
  encoder_profile varchar(50) NOT NULL DEFAULT 'qc-reels',
  encoder_version varchar(50) NOT NULL,
  status raw_metadata_catalog.file_artifact_status NOT NULL DEFAULT 'requested',
  request_id uuid NOT NULL DEFAULT gen_random_uuid(),
  requested_by text NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz NULL,
  completed_at timestamptz NULL,
  target_bucket varchar(255) NULL,
  target_key varchar(1024) NULL,
  meta_key varchar(1024) NULL,
  artifact_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text NULL,
  last_updated timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT file_artifacts_file_id_fkey
    FOREIGN KEY (file_id)
    REFERENCES raw_metadata_catalog.files(file_id)
    ON DELETE CASCADE,
  CONSTRAINT uq_file_artifacts_segment_version
    UNIQUE (file_id, artifact_type, video_file_segment_id, encoder_profile, encoder_version)
);

CREATE INDEX IF NOT EXISTS ix_file_artifacts_file_status
  ON raw_metadata_catalog.file_artifacts (file_id, status);

CREATE INDEX IF NOT EXISTS ix_file_artifacts_segment
  ON raw_metadata_catalog.file_artifacts (video_file_segment_id);

CREATE INDEX IF NOT EXISTS ix_file_artifacts_request_id
  ON raw_metadata_catalog.file_artifacts (request_id);

