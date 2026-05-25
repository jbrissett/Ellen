-- DROP SCHEMA ops_config;

CREATE SCHEMA ops_config AUTHORIZATION tcmsdbadm;

-- DROP SEQUENCE ops_config.bin_scheme_banks_id_seq;

CREATE SEQUENCE ops_config.bin_scheme_banks_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE ops_config.bin_schemes_id_seq;

CREATE SEQUENCE ops_config.bin_schemes_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE ops_config.vehicle_classes_id_seq;

CREATE SEQUENCE ops_config.vehicle_classes_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;-- ops_config.bin_schemes definition

-- Drop table

-- DROP TABLE ops_config.bin_schemes;

CREATE TABLE ops_config.bin_schemes (
	id bigserial NOT NULL,
	"name" text NOT NULL,
	code text NULL,
	description text NULL,
	is_locked bool DEFAULT false NOT NULL,
	for_file_ingest bool DEFAULT false NOT NULL,
	for_reporting bool DEFAULT false NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT bin_schemes_code_key UNIQUE (code),
	CONSTRAINT bin_schemes_name_key UNIQUE (name),
	CONSTRAINT bin_schemes_pkey PRIMARY KEY (id)
);

-- Table Triggers

create trigger trg_bin_schemes_touch before
update
    on
    ops_config.bin_schemes for each row execute function ops_config.touch_updated_at();


-- ops_config.format_profiles definition

-- Drop table

-- DROP TABLE ops_config.format_profiles;

CREATE TABLE ops_config.format_profiles (
	profile_id text NOT NULL,
	source_group text NULL,
	granularity text NULL,
	container text NULL,
	detector jsonb NULL,
	"schema" jsonb NULL,
	validators _text DEFAULT '{}'::text[] NULL,
	processing_plan jsonb NULL,
	is_active bool DEFAULT true NULL,
	created_at timestamptz DEFAULT now() NULL,
	updated_at timestamptz DEFAULT now() NULL,
	CONSTRAINT format_profiles_granularity_check CHECK ((granularity = ANY (ARRAY['per_vehicle'::text, 'summarized'::text]))),
	CONSTRAINT format_profiles_pkey PRIMARY KEY (profile_id)
);


-- ops_config.vehicle_classes definition

-- Drop table

-- DROP TABLE ops_config.vehicle_classes;

CREATE TABLE ops_config.vehicle_classes (
	id bigserial NOT NULL,
	fhwa_class_no int2 NULL,
	vehicle_type text NOT NULL,
	fhwa_class_label text GENERATED ALWAYS AS (
CASE
    WHEN fhwa_class_no IS NULL THEN NULL::text
    ELSE 'Class '::text || fhwa_class_no::text
END) STORED NULL,
	fhwa_description text NULL,
	datalens_alias text NOT NULL,
	is_fhwa bool DEFAULT false NOT NULL,
	code text NULL,
	sort_order int4 DEFAULT 0 NOT NULL,
	notes text NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT vehicle_classes_datalens_alias_key UNIQUE (datalens_alias),
	CONSTRAINT vehicle_classes_fhwa_class_no_check CHECK (((fhwa_class_no >= 1) AND (fhwa_class_no <= 13))),
	CONSTRAINT vehicle_classes_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_vehicle_classes_fhwa_no ON ops_config.vehicle_classes USING btree (fhwa_class_no);
CREATE INDEX idx_vehicle_classes_sort ON ops_config.vehicle_classes USING btree (sort_order);

-- Table Triggers

create trigger trg_vehicle_classes_touch before
update
    on
    ops_config.vehicle_classes for each row execute function ops_config.touch_updated_at();


-- ops_config.bin_scheme_banks definition

-- Drop table

-- DROP TABLE ops_config.bin_scheme_banks;

CREATE TABLE ops_config.bin_scheme_banks (
	id bigserial NOT NULL,
	scheme_id int8 NOT NULL,
	bank_index int2 NOT NULL,
	"label" text NULL,
	is_zero_filled bool DEFAULT false NOT NULL,
	ped_column_semantics text DEFAULT 'none'::text NOT NULL,
	ped_uturn_bank int2 NULL,
	qchub_report_code VARCHAR NULL,
	CONSTRAINT bin_scheme_banks_pkey PRIMARY KEY (id),
	CONSTRAINT bin_scheme_banks_scheme_id_bank_index_key UNIQUE (scheme_id, bank_index),
	CONSTRAINT bin_scheme_banks_scheme_id_fkey FOREIGN KEY (scheme_id) REFERENCES ops_config.bin_schemes(id) ON DELETE CASCADE
);


-- ops_config.bin_scheme_uturn_rules definition

-- Drop table

-- DROP TABLE ops_config.bin_scheme_uturn_rules;

CREATE TABLE ops_config.bin_scheme_uturn_rules (
	scheme_id int8 NOT NULL,
	source_bank_index int2 NOT NULL,
	target_bank_index int2 NOT NULL,
	ped_column_target bool DEFAULT true NOT NULL,
	CONSTRAINT bin_scheme_uturn_rules_pkey PRIMARY KEY (scheme_id, source_bank_index),
	CONSTRAINT bin_scheme_uturn_rules_scheme_id_fkey FOREIGN KEY (scheme_id) REFERENCES ops_config.bin_schemes(id) ON DELETE CASCADE
);


-- ops_config.bin_scheme_bank_classes definition

-- Drop table

-- DROP TABLE ops_config.bin_scheme_bank_classes;

CREATE TABLE ops_config.bin_scheme_bank_classes (
	bank_id int8 NOT NULL,
	vehicle_class_id int8 NOT NULL,
	CONSTRAINT bin_scheme_bank_classes_pkey PRIMARY KEY (bank_id, vehicle_class_id),
	CONSTRAINT bin_scheme_bank_classes_bank_id_fkey FOREIGN KEY (bank_id) REFERENCES ops_config.bin_scheme_banks(id) ON DELETE CASCADE,
	CONSTRAINT bin_scheme_bank_classes_vehicle_class_id_fkey FOREIGN KEY (vehicle_class_id) REFERENCES ops_config.vehicle_classes(id) ON DELETE RESTRICT
);


-- ops_config.vw_vehicle_classes_display source

CREATE OR REPLACE VIEW ops_config.vw_vehicle_classes_display
AS SELECT id,
    COALESCE(fhwa_class_label, '—'::text) AS fhwa_class,
    vehicle_type,
    fhwa_description,
    datalens_alias,
    is_fhwa,
    code,
    sort_order
   FROM ops_config.vehicle_classes
  ORDER BY (
        CASE
            WHEN is_fhwa THEN 0
            ELSE 1
        END), sort_order, id;



-- DROP FUNCTION ops_config.touch_updated_at();

CREATE OR REPLACE FUNCTION ops_config.touch_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $function$
;
