-- DROP SCHEMA delivery;

CREATE SCHEMA delivery AUTHORIZATION tcmsdbadm;
-- delivery.published_count definition

-- Drop table

-- DROP TABLE delivery.published_count;

CREATE TABLE delivery.published_count (
	published_count_id uuid DEFAULT gen_random_uuid() NOT NULL,
	assembly_session_id uuid NOT NULL,
	"version" int4 NOT NULL,
	order_no int4 NOT NULL,
	location_id int8 NULL,
	sitecode_id int8 NOT NULL,
	qc_station_id int8 NULL,
	bucket_minutes int2 NULL,
	published_at timestamptz DEFAULT now() NOT NULL,
	published_by text NOT NULL,
	notes text NULL,
	content_hash_sha256 text NOT NULL,
	interval_start timestamp NOT NULL,
	interval_end timestamp NOT NULL,
	CONSTRAINT published_count_assembly_session_id_version_key UNIQUE (assembly_session_id, version),
	CONSTRAINT published_count_pkey PRIMARY KEY (published_count_id)
);
CREATE INDEX ix_pubcount_sitecode ON delivery.published_count USING btree (sitecode_id, version DESC);


-- delivery.published_lineage definition

-- Drop table

-- DROP TABLE delivery.published_lineage;

CREATE TABLE delivery.published_lineage (
	published_count_id uuid NOT NULL,
	lineage_key uuid NOT NULL,
	contributors jsonb NOT NULL,
	applied_rules jsonb NOT NULL,
	applied_overrides jsonb NOT NULL,
	qa_snapshot jsonb NULL,
	CONSTRAINT published_lineage_pkey PRIMARY KEY (published_count_id, lineage_key)
);


-- delivery.published_volume_by_movement definition

-- Drop table

-- DROP TABLE delivery.published_volume_by_movement;

CREATE TABLE delivery.published_volume_by_movement (
	published_count_id uuid NOT NULL,
	movement text NOT NULL,
	volume_count int4 NOT NULL,
	volume_count_by_class jsonb NULL,
	category_dimension assembly.category_dimension DEFAULT 'none'::assembly.category_dimension NOT NULL,
	bank_schema_id int8 NULL,
	category_breakdown jsonb NULL,
	lineage_key uuid NOT NULL,
	interval_start timestamp NOT NULL
);


-- delivery.published_volume_by_speed definition

-- Drop table

-- DROP TABLE delivery.published_volume_by_speed;

CREATE TABLE delivery.published_volume_by_speed (
	published_count_id uuid NOT NULL,
	movement text DEFAULT '~'::text NOT NULL,
	speed_mph int4 NOT NULL,
	vehicle_count int4 NOT NULL,
	vehicle_count_by_class jsonb NULL,
	category_dimension assembly.category_dimension DEFAULT 'none'::assembly.category_dimension NOT NULL,
	bank_schema_id int8 NULL,
	category_breakdown jsonb NULL,
	lineage_key uuid NOT NULL,
	interval_start timestamp NOT NULL
);


-- delivery.qc_publishing_station definition

-- Drop table

-- DROP TABLE delivery.qc_publishing_station;

CREATE TABLE delivery.qc_publishing_station (
	station_id uuid NOT NULL,
	anchor_id uuid NOT NULL,
	"name" text NOT NULL,
	slug text NOT NULL,
	status text NOT NULL,
	config_selection text NOT NULL,
	pinned_configuration_id uuid NULL,
	owner_office text NULL,
	created_by text NULL,
	created_at timestamptz DEFAULT now() NULL,
	notes text NULL,
	CONSTRAINT qc_publishing_station_anchor_id_key UNIQUE (anchor_id),
	CONSTRAINT qc_publishing_station_config_selection_check CHECK ((config_selection = ANY (ARRAY['latest_published'::text, 'pinned_version'::text]))),
	CONSTRAINT qc_publishing_station_pkey PRIMARY KEY (station_id),
	CONSTRAINT qc_publishing_station_slug_key UNIQUE (slug),
	CONSTRAINT qc_publishing_station_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'archived'::text])))
);


-- delivery.station_config_binding definition

-- Drop table

-- DROP TABLE delivery.station_config_binding;

CREATE TABLE delivery.station_config_binding (
	station_id uuid NOT NULL,
	configuration_id uuid NOT NULL,
	bound_at timestamptz DEFAULT now() NULL,
	bound_by text NULL,
	CONSTRAINT station_config_binding_pkey PRIMARY KEY (station_id, configuration_id)
);


-- delivery.published_count foreign keys

ALTER TABLE delivery.published_count ADD CONSTRAINT published_count_assembly_session_id_fkey FOREIGN KEY (assembly_session_id) REFERENCES assembly.assembly_session(assembly_session_id);


-- delivery.published_lineage foreign keys

ALTER TABLE delivery.published_lineage ADD CONSTRAINT published_lineage_published_count_id_fkey FOREIGN KEY (published_count_id) REFERENCES delivery.published_count(published_count_id) ON DELETE CASCADE;


-- delivery.published_volume_by_movement foreign keys

ALTER TABLE delivery.published_volume_by_movement ADD CONSTRAINT published_volume_by_movement_bank_schema_id_fkey FOREIGN KEY (bank_schema_id) REFERENCES ops_config.bin_schemes(id);
ALTER TABLE delivery.published_volume_by_movement ADD CONSTRAINT published_volume_by_movement_published_count_id_fkey FOREIGN KEY (published_count_id) REFERENCES delivery.published_count(published_count_id) ON DELETE CASCADE;


-- delivery.published_volume_by_speed foreign keys

ALTER TABLE delivery.published_volume_by_speed ADD CONSTRAINT published_volume_by_speed_bank_schema_id_fkey FOREIGN KEY (bank_schema_id) REFERENCES ops_config.bin_schemes(id);
ALTER TABLE delivery.published_volume_by_speed ADD CONSTRAINT published_volume_by_speed_published_count_id_fkey FOREIGN KEY (published_count_id) REFERENCES delivery.published_count(published_count_id) ON DELETE CASCADE;


-- delivery.qc_publishing_station foreign keys

ALTER TABLE delivery.qc_publishing_station ADD CONSTRAINT qc_publishing_station_anchor_id_fkey FOREIGN KEY (anchor_id) REFERENCES index_schema.roadway_anchor(anchor_id) ON DELETE RESTRICT;
ALTER TABLE delivery.qc_publishing_station ADD CONSTRAINT qc_publishing_station_pinned_configuration_id_fkey FOREIGN KEY (pinned_configuration_id) REFERENCES roadway_config."configuration"(configuration_id);


-- delivery.station_config_binding foreign keys

ALTER TABLE delivery.station_config_binding ADD CONSTRAINT station_config_binding_configuration_id_fkey FOREIGN KEY (configuration_id) REFERENCES roadway_config."configuration"(configuration_id) ON DELETE RESTRICT;
ALTER TABLE delivery.station_config_binding ADD CONSTRAINT station_config_binding_station_id_fkey FOREIGN KEY (station_id) REFERENCES delivery.qc_publishing_station(station_id) ON DELETE CASCADE;



-- DROP FUNCTION delivery.publish_preview(uuid, text, text);

CREATE OR REPLACE FUNCTION delivery.publish_preview(p_session_id uuid, p_published_by text, p_notes text DEFAULT NULL::text)
 RETURNS uuid
 LANGUAGE plpgsql
AS $function$
DECLARE
  v_session          assembly.assembly_session;
  v_latest_seq       integer;
  v_next_version     integer;
  v_published_id     uuid := gen_random_uuid();
  v_hash             text;
  v_bucket           int2;
BEGIN
  SELECT * INTO v_session FROM assembly.assembly_session WHERE assembly_session_id = p_session_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'assembly_session % not found', p_session_id;
  END IF;

  SELECT MAX(snapshot_seq) INTO v_latest_seq
  FROM assembly.preview_snapshot
  WHERE assembly_session_id = p_session_id;

  IF v_latest_seq IS NULL THEN
    RAISE EXCEPTION 'No preview snapshot exists for session %', p_session_id;
  END IF;

  SELECT bucket_minutes INTO v_bucket
  FROM assembly.preview_snapshot
  WHERE assembly_session_id = p_session_id AND snapshot_seq = v_latest_seq;

  SELECT COALESCE(MAX(version), 0) + 1 INTO v_next_version
  FROM delivery.published_count
  WHERE assembly_session_id = p_session_id;

  -- Compute deterministic hash over movement + (optionally) speed rows
  CREATE TEMP TABLE _pub_blob (payload bytea);

  INSERT INTO _pub_blob(payload)
  SELECT digest(string_agg(row_str, E'\n' ORDER BY row_str), 'sha256')
  FROM (
    SELECT
      format('M|%s|%s|%s|%s|%s|%s',
             to_char(pvm.interval_start, 'YYYY-MM-DD"T"HH24:MI:SSOF'),
             pvm.movement,
             pvm.volume_count,
             COALESCE(pvm.category_dimension::text,''),
             COALESCE(pvm.bank_schema_id::text,''),
             COALESCE(pvm.category_breakdown::text,'{}')
      ) AS row_str
    FROM assembly.preview_volume_by_movement pvm
    WHERE pvm.assembly_session_id = p_session_id AND pvm.snapshot_seq = v_latest_seq

    UNION ALL

    SELECT
      format('S|%s|%s|%s|%s|%s|%s|%s',
             to_char(pvs.interval_start, 'YYYY-MM-DD"T"HH24:MI:SSOF'),
             COALESCE(pvs.movement,''),
             pvs.speed_mph,
             pvs.vehicle_count,
             COALESCE(pvs.category_dimension::text,''),
             COALESCE(pvs.bank_schema_id::text,''),
             COALESCE(pvs.category_breakdown::text,'{}')
      ) AS row_str
    FROM assembly.preview_volume_by_speed pvs
    WHERE pvs.assembly_session_id = p_session_id AND pvs.snapshot_seq = v_latest_seq
  ) x;

  SELECT encode(payload, 'hex') INTO v_hash FROM _pub_blob LIMIT 1;

  -- Insert header (qc_station_id may be NULL if not resolved yet)
  INSERT INTO delivery.published_count (
    published_count_id, assembly_session_id, version,
    order_no, location_id, sitecode_id,
    qc_station_id,
    interval_start, interval_end, bucket_minutes,
    published_by, notes, content_hash_sha256
  ) VALUES (
    v_published_id, p_session_id, v_next_version,
    v_session.order_no, v_session.location_id, v_session.sitecode_id,
    NULL,
    v_session.interval_start, v_session.interval_end, v_bucket,
    p_published_by, p_notes, v_hash
  );

  -- Movement rows
  INSERT INTO delivery.published_volume_by_movement
    (published_count_id, interval_start, movement,
     volume_count, volume_count_by_class,
     category_dimension, bank_schema_id, category_breakdown, lineage_key)
  SELECT
    v_published_id, interval_start, movement,
    volume_count, volume_count_by_class,
    category_dimension, bank_schema_id, category_breakdown, lineage_key
  FROM assembly.preview_volume_by_movement
  WHERE assembly_session_id = p_session_id AND snapshot_seq = v_latest_seq;

  -- Speed rows (if you generate them)
  INSERT INTO delivery.published_volume_by_speed
    (published_count_id, interval_start, movement, speed_mph,
     vehicle_count, vehicle_count_by_class,
     category_dimension, bank_schema_id, category_breakdown, lineage_key)
  SELECT
    v_published_id, interval_start, movement, speed_mph,
    vehicle_count, vehicle_count_by_class,
    category_dimension, bank_schema_id, category_breakdown, lineage_key
  FROM assembly.preview_volume_by_speed
  WHERE assembly_session_id = p_session_id AND snapshot_seq = v_latest_seq;

  -- Lineage + QA snapshot
  INSERT INTO delivery.published_lineage
    (published_count_id, lineage_key, contributors, applied_rules, applied_overrides, qa_snapshot)
  SELECT
    v_published_id, lineage_key, contributors, applied_rules, applied_overrides,
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'qa_flag_id', q.qa_flag_id,
          'flag_type', q.flag_type,
          'status', q.status,
          'interval_range', q.interval_range,
          'movement', q.movement,
          'class_or_bank_key', q.class_or_bank_key,
          'reason_tags', q.reason_tags,
          'action_tags', q.action_tags,
          'note', q.note
        )
      )
      FROM assembly.qa_flag q
      WHERE q.assembly_session_id = p_session_id
    )
  FROM assembly.preview_lineage
  WHERE assembly_session_id = p_session_id AND snapshot_seq = v_latest_seq;

  -- Mark session as published (optional)
  UPDATE assembly.assembly_session
  SET status = 'published', updated_at = now()
  WHERE assembly_session_id = p_session_id;

  RETURN v_published_id;
END;
$function$
;
