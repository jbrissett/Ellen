-- DROP SCHEMA roadway_config;

CREATE SCHEMA roadway_config AUTHORIZATION tcmsdbadm;

-- DROP TYPE roadway_config.rc_control;

CREATE TYPE roadway_config.rc_control AS ENUM (
	'signal',
	'stop_all',
	'stop_minor',
	'yield',
	'uncontrolled');

-- DROP TYPE roadway_config.rc_dir_cardinal;

CREATE TYPE roadway_config.rc_dir_cardinal AS ENUM (
	'NB',
	'SB',
	'EB',
	'WB',
	'NEB',
	'NWB',
	'SEB',
	'SWB',
	'FWD',
	'REV');

-- DROP TYPE roadway_config.rc_directionality;

CREATE TYPE roadway_config.rc_directionality AS ENUM (
	'oneway',
	'twoway');

-- DROP TYPE roadway_config.rc_junction_style;

CREATE TYPE roadway_config.rc_junction_style AS ENUM (
	'standard',
	'roundabout');

-- DROP TYPE roadway_config.rc_lane_kind;

CREATE TYPE roadway_config.rc_lane_kind AS ENUM (
	'general',
	'left',
	'thru',
	'right',
	'left_thru',
	'thru_right',
	'left_thru_right',
	'uturn',
	'bike',
	'bus',
	'hov',
	'shoulder',
	'left_right');

-- DROP TYPE roadway_config.rc_move;

CREATE TYPE roadway_config.rc_move AS ENUM (
	'left',
	'thru',
	'right',
	'uturn');

-- DROP TYPE roadway_config.rc_site_kind;

CREATE TYPE roadway_config.rc_site_kind AS ENUM (
	'intersection',
	'midblock',
	'survey',
	'study');

-- DROP TYPE roadway_config.rc_status;

CREATE TYPE roadway_config.rc_status AS ENUM (
	'draft',
	'review',
	'published',
	'archived');
-- roadway_config.approach definition

-- Drop table

-- DROP TABLE roadway_config.approach;

CREATE TABLE roadway_config.approach (
	approach_id uuid NOT NULL,
	configuration_id uuid NOT NULL,
	road_id int8 NOT NULL,
	dir_cardinal roadway_config.rc_dir_cardinal NULL,
	"label" text NOT NULL,
	bearing_deg int4 NULL,
	speed_limit_mph int2 NULL,
	control_type roadway_config.rc_control NULL,
	has_channelized_right bool DEFAULT false NOT NULL,
	left_permitted bool NULL,
	left_protected bool NULL,
	right_permitted bool NULL,
	right_protected bool NULL,
	right_on_red_allowed bool NULL,
	axis_delta_deg int4 NULL,
	corridor_group_id uuid NULL,
	corridor_segment_id uuid NULL,
	corridor_dir text NULL,
	dir_cardinal_reason text NULL,
	osm_way_id int8 NULL,
	bearing_geom public.geometry(linestring, 4326) NULL,
	display_road_name text NULL,
	CONSTRAINT approach_pkey PRIMARY KEY (approach_id)
);
CREATE INDEX ix_approach_bearing_geom ON roadway_config.approach USING gist (bearing_geom);
CREATE INDEX ix_approach_config ON roadway_config.approach USING btree (configuration_id);
CREATE INDEX ix_approach_corridor ON roadway_config.approach USING btree (corridor_group_id, corridor_segment_id);
CREATE INDEX ix_approach_road ON roadway_config.approach USING btree (road_id);


-- roadway_config.audit_log definition

-- Drop table

-- DROP TABLE roadway_config.audit_log;

CREATE TABLE roadway_config.audit_log (
	configuration_id uuid NOT NULL,
	"version" int4 NOT NULL,
	edit_revision int4 NOT NULL,
	changed_at timestamptz DEFAULT now() NOT NULL,
	changed_by text NULL,
	reason text NULL,
	diff_schema_id text DEFAULT 'audit.diff.v1'::text NOT NULL,
	diff jsonb NOT NULL,
	CONSTRAINT audit_log_pkey PRIMARY KEY (configuration_id, version, edit_revision, changed_at)
);


-- roadway_config.capture definition

-- Drop table

-- DROP TABLE roadway_config.capture;

CREATE TABLE roadway_config.capture (
	capture_id uuid NOT NULL,
	anchor_id uuid NOT NULL,
	observed_at timestamptz NOT NULL,
	submitted_by text NOT NULL,
	submitted_at timestamptz DEFAULT now() NULL,
	payload_schema_id text DEFAULT 'capture.payload.v1'::text NOT NULL,
	payload jsonb NOT NULL,
	photos jsonb NULL,
	notes text NULL,
	change_score numeric NULL,
	status text NOT NULL,
	CONSTRAINT capture_pkey PRIMARY KEY (capture_id),
	CONSTRAINT capture_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved_edit'::text, 'approved_observed'::text, 'rejected'::text])))
);
CREATE INDEX ix_capture_anchor_status ON roadway_config.capture USING btree (anchor_id, status, submitted_at DESC);

-- Table Triggers

create trigger trg_assert_known_payload_schema before
insert
    or
update
    on
    roadway_config.capture for each row execute function json_contract.assert_known_payload_schema();


-- roadway_config."configuration" definition

-- Drop table

-- DROP TABLE roadway_config."configuration";

CREATE TABLE roadway_config."configuration" (
	configuration_id uuid NOT NULL,
	anchor_id uuid NOT NULL,
	site_kind roadway_config.rc_site_kind NOT NULL,
	junction_style roadway_config.rc_junction_style DEFAULT 'standard'::roadway_config.rc_junction_style NOT NULL,
	"version" int4 NOT NULL,
	edit_revision int4 DEFAULT 0 NOT NULL,
	status roadway_config.rc_status NOT NULL,
	effective_start_ts timestamptz NOT NULL,
	effective_end_ts timestamptz NULL,
	directionality roadway_config.rc_directionality NULL,
	has_center_left_turn_lane bool NULL,
	has_median bool NULL,
	notes text NULL,
	created_by text NULL,
	centerline_geom public.geometry(linestring, 4326) NULL,
	"period" tstzrange GENERATED ALWAYS AS (tstzrange(effective_start_ts, effective_end_ts, '[)'::text)) STORED NULL,
	CONSTRAINT chk_config_time_order CHECK (((effective_end_ts IS NULL) OR (effective_end_ts > effective_start_ts))),
	CONSTRAINT configuration_pkey PRIMARY KEY (configuration_id)
);
CREATE INDEX ix_config_anchor_status_start ON roadway_config.configuration USING btree (anchor_id, status, effective_start_ts DESC);
CREATE UNIQUE INDEX uq_config_anchor_version ON roadway_config.configuration USING btree (anchor_id, version);


-- roadway_config.lane definition

-- Drop table

-- DROP TABLE roadway_config.lane;

CREATE TABLE roadway_config.lane (
	lane_id uuid NOT NULL,
	approach_id uuid NOT NULL,
	idx_from_left int4 NOT NULL,
	lane_kind roadway_config.rc_lane_kind NOT NULL,
	width_m numeric NULL,
	pocket_length_m numeric NULL,
	CONSTRAINT lane_pkey PRIMARY KEY (lane_id)
);
CREATE INDEX ix_lane_approach ON roadway_config.lane USING btree (approach_id);
CREATE UNIQUE INDEX uq_lane_order_per_approach ON roadway_config.lane USING btree (approach_id, idx_from_left);


-- roadway_config.approach foreign keys

ALTER TABLE roadway_config.approach ADD CONSTRAINT approach_configuration_id_fkey FOREIGN KEY (configuration_id) REFERENCES roadway_config."configuration"(configuration_id) ON DELETE CASCADE;
ALTER TABLE roadway_config.approach ADD CONSTRAINT approach_road_id_fkey FOREIGN KEY (road_id) REFERENCES taxonomy.road(road_id) ON DELETE RESTRICT;


-- roadway_config.audit_log foreign keys

ALTER TABLE roadway_config.audit_log ADD CONSTRAINT audit_log_configuration_id_fkey FOREIGN KEY (configuration_id) REFERENCES roadway_config."configuration"(configuration_id);


-- roadway_config.capture foreign keys

ALTER TABLE roadway_config.capture ADD CONSTRAINT capture_anchor_id_fkey FOREIGN KEY (anchor_id) REFERENCES index_schema.roadway_anchor(anchor_id);


-- roadway_config."configuration" foreign keys

ALTER TABLE roadway_config."configuration" ADD CONSTRAINT configuration_anchor_id_fkey FOREIGN KEY (anchor_id) REFERENCES index_schema.roadway_anchor(anchor_id) ON DELETE CASCADE;


-- roadway_config.lane foreign keys

ALTER TABLE roadway_config.lane ADD CONSTRAINT lane_approach_id_fkey FOREIGN KEY (approach_id) REFERENCES roadway_config.approach(approach_id) ON DELETE CASCADE;


-- roadway_config.vw_qchub_legs_flat source

CREATE OR REPLACE VIEW roadway_config.vw_qchub_legs_flat
AS WITH buck AS (
         SELECT cfg.configuration_id,
                CASE
                    WHEN ap.bearing_deg IS NULL THEN ap.label
                    WHEN ap.bearing_deg >= 315 OR ap.bearing_deg < 45 THEN 'N'::text
                    WHEN ap.bearing_deg >= 45 AND ap.bearing_deg < 135 THEN 'E'::text
                    WHEN ap.bearing_deg >= 135 AND ap.bearing_deg < 225 THEN 'S'::text
                    ELSE 'W'::text
                END AS compass,
            ap.approach_id,
            ap.control_type
           FROM roadway_config.configuration cfg
             JOIN roadway_config.approach ap ON ap.configuration_id = cfg.configuration_id
          WHERE cfg.status = 'published'::roadway_config.rc_status
        )
 SELECT configuration_id,
    max(
        CASE
            WHEN compass = 'N'::text THEN control_type
            ELSE NULL::roadway_config.rc_control
        END) AS n_control,
    max(
        CASE
            WHEN compass = 'E'::text THEN control_type
            ELSE NULL::roadway_config.rc_control
        END) AS e_control,
    max(
        CASE
            WHEN compass = 'S'::text THEN control_type
            ELSE NULL::roadway_config.rc_control
        END) AS s_control,
    max(
        CASE
            WHEN compass = 'W'::text THEN control_type
            ELSE NULL::roadway_config.rc_control
        END) AS w_control
   FROM buck
  GROUP BY configuration_id;



-- DROP FUNCTION roadway_config.effective_config(uuid, timestamptz);

CREATE OR REPLACE FUNCTION roadway_config.effective_config(p_anchor uuid, p_at timestamp with time zone)
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
  SELECT configuration_id
  FROM roadway_config.configuration
  WHERE anchor_id = p_anchor
    AND status = 'published'
    AND effective_start_ts <= p_at
    AND (effective_end_ts IS NULL OR effective_end_ts > p_at)
  ORDER BY effective_start_ts DESC
  LIMIT 1
$function$
;

-- DROP FUNCTION roadway_config.effective_config_by_collection_site(int8, timestamptz);

CREATE OR REPLACE FUNCTION roadway_config.effective_config_by_collection_site(p_site_id bigint, p_at timestamp with time zone)
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
  SELECT roadway_config.effective_config(csam.anchor_id, p_at)
  FROM index_schema.collection_site_anchor_map csam
  WHERE csam.collection_site_id = p_site_id
$function$
;
