-- DROP SCHEMA taxonomy;

CREATE SCHEMA taxonomy AUTHORIZATION tcmsdbadm;

-- DROP TYPE taxonomy.tx_dir;

CREATE TYPE taxonomy.tx_dir AS ENUM (
	'N',
	'S',
	'E',
	'W',
	'NE',
	'NW',
	'SE',
	'SW');

-- DROP SEQUENCE taxonomy.road_road_id_seq;

CREATE SEQUENCE taxonomy.road_road_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 9223372036854775807
	START 1
	CACHE 1
	NO CYCLE;-- taxonomy.road definition

-- Drop table

-- DROP TABLE taxonomy.road;

CREATE TABLE taxonomy.road (
	road_id bigserial NOT NULL,
	canonical_name text NOT NULL,
	prefix_dir taxonomy.tx_dir NULL,
	base_name text NULL,
	suffix_type text NULL,
	route_number text NULL,
	alt_names _text NULL,
	CONSTRAINT road_pkey PRIMARY KEY (road_id)
);
CREATE UNIQUE INDEX uq_taxonomy_road_canonical ON taxonomy.road USING btree (canonical_name);


-- taxonomy.suffix_norm definition

-- Drop table

-- DROP TABLE taxonomy.suffix_norm;

CREATE TABLE taxonomy.suffix_norm (
	raw text NOT NULL,
	canonical text NOT NULL,
	CONSTRAINT suffix_norm_pkey PRIMARY KEY (raw)
);


-- taxonomy.corridor_group definition

-- Drop table

-- DROP TABLE taxonomy.corridor_group;

CREATE TABLE taxonomy.corridor_group (
	corridor_group_id uuid DEFAULT gen_random_uuid() NOT NULL,
	road_id int8 NOT NULL,
	is_routed bool NOT NULL,
	network text NULL,
	"ref" text NULL,
	canonical_name text NOT NULL,
	admin_boundary_id text NULL,
	direction_axis text NOT NULL,
	provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
	"source" text DEFAULT 'auto'::text NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	first_seen_at timestamptz DEFAULT now() NOT NULL,
	last_seen_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT cg_routed_requires_keys CHECK ((((is_routed = true) AND (network IS NOT NULL) AND (ref IS NOT NULL)) OR ((is_routed = false) AND (admin_boundary_id IS NOT NULL)))),
	CONSTRAINT corridor_group_direction_axis_check CHECK ((direction_axis = ANY (ARRAY['NS'::text, 'EW'::text, 'NE'::text, 'NW'::text, 'SE'::text, 'SW'::text]))),
	CONSTRAINT corridor_group_pkey PRIMARY KEY (corridor_group_id),
	CONSTRAINT corridor_group_road_id_fkey FOREIGN KEY (road_id) REFERENCES taxonomy.road(road_id)
);
CREATE INDEX ix_cg_last_seen ON taxonomy.corridor_group USING btree (last_seen_at);
CREATE UNIQUE INDEX uq_cg_nonrouted ON taxonomy.corridor_group USING btree (is_routed, canonical_name, admin_boundary_id, direction_axis) WHERE (is_routed = false);
CREATE UNIQUE INDEX uq_cg_routed ON taxonomy.corridor_group USING btree (is_routed, network, ref, direction_axis) WHERE (is_routed = true);


-- taxonomy.corridor_segment definition

-- Drop table

-- DROP TABLE taxonomy.corridor_segment;

CREATE TABLE taxonomy.corridor_segment (
	corridor_segment_id uuid DEFAULT gen_random_uuid() NOT NULL,
	corridor_group_id uuid NOT NULL,
	local_scope_kind text NOT NULL,
	local_scope_id text NOT NULL,
	anchor_count int4 DEFAULT 0 NOT NULL,
	axis_bearing_sample numeric NULL,
	confidence_band text NULL,
	provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
	"source" text DEFAULT 'auto'::text NOT NULL,
	is_active bool DEFAULT true NOT NULL,
	first_seen_at timestamptz DEFAULT now() NOT NULL,
	last_seen_at timestamptz NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	segment_geom public.geometry(multilinestring, 4326) NULL,
	CONSTRAINT corridor_segment_confidence_band_check CHECK ((confidence_band = ANY (ARRAY['A'::text, 'B'::text, 'C'::text]))),
	CONSTRAINT corridor_segment_corridor_group_id_local_scope_kind_local_s_key UNIQUE (corridor_group_id, local_scope_kind, local_scope_id),
	CONSTRAINT corridor_segment_local_scope_kind_check CHECK ((local_scope_kind = ANY (ARRAY['admin'::text, 'tile'::text]))),
	CONSTRAINT corridor_segment_pkey PRIMARY KEY (corridor_segment_id),
	CONSTRAINT corridor_segment_corridor_group_id_fkey FOREIGN KEY (corridor_group_id) REFERENCES taxonomy.corridor_group(corridor_group_id) ON DELETE CASCADE
);
CREATE INDEX ix_cs_group_active ON taxonomy.corridor_segment USING btree (corridor_group_id, is_active);
CREATE INDEX ix_cs_last_seen ON taxonomy.corridor_segment USING btree (last_seen_at);


-- taxonomy.category_member source

CREATE OR REPLACE VIEW taxonomy.category_member
AS SELECT scheme_id AS category_schema_id,
    bank_index::text AS key,
    COALESCE(label, 'Bank '::text || bank_index) AS label,
    bank_index::integer AS ord,
    is_zero_filled,
    ped_column_semantics,
    ped_uturn_bank
   FROM ops_config.bin_scheme_banks b;


-- taxonomy.category_schema source

CREATE OR REPLACE VIEW taxonomy.category_schema
AS SELECT id AS category_schema_id,
    'bank'::assembly.category_dimension AS dimension,
    name,
    COALESCE(code, name) AS code,
    description,
    is_locked,
    created_at,
    updated_at
   FROM ops_config.bin_schemes bs;


-- taxonomy.class_member source

CREATE OR REPLACE VIEW taxonomy.class_member
AS SELECT id AS vehicle_class_id,
    datalens_alias AS key,
    COALESCE(code, datalens_alias) AS label,
    sort_order AS ord,
    is_fhwa,
    fhwa_class_no,
    vehicle_type
   FROM ops_config.vehicle_classes vc;


-- taxonomy.class_to_bank_map source

CREATE OR REPLACE VIEW taxonomy.class_to_bank_map
AS SELECT b.scheme_id AS bank_schema_id,
    b.bank_index::text AS bank_key,
    vc.datalens_alias AS class_key
   FROM ops_config.bin_scheme_banks b
     JOIN ops_config.bin_scheme_bank_classes bc ON bc.bank_id = b.id
     JOIN ops_config.vehicle_classes vc ON vc.id = bc.vehicle_class_id;



-- DROP FUNCTION taxonomy.filter_category_json(jsonb, _text, _text);

CREATE OR REPLACE FUNCTION taxonomy.filter_category_json(p_json jsonb, p_include text[] DEFAULT NULL::text[], p_exclude text[] DEFAULT NULL::text[])
 RETURNS TABLE(filtered jsonb, total integer)
 LANGUAGE plpgsql
AS $function$
DECLARE
  kv RECORD;
  v_sum int := 0;
  v_out jsonb := '{}'::jsonb;
  take boolean;
  val int;
BEGIN
  IF p_json IS NULL THEN
    RETURN QUERY SELECT NULL::jsonb, NULL::int;
    RETURN;
  END IF;

  FOR kv IN SELECT * FROM jsonb_each_text(p_json) LOOP
    take := true;
    val := COALESCE(kv.value::int, 0);

    IF p_include IS NOT NULL AND array_length(p_include,1) > 0 THEN
      take := take AND kv.key = ANY(p_include);
    END IF;
    IF p_exclude IS NOT NULL AND array_length(p_exclude,1) > 0 THEN
      take := take AND NOT (kv.key = ANY(p_exclude));
    END IF;

    IF take THEN
      v_out := v_out || jsonb_build_object(kv.key, val);
      v_sum := v_sum + val;
    END IF;
  END LOOP;

  RETURN QUERY SELECT v_out, v_sum;
END;
$function$
;

-- DROP FUNCTION taxonomy.rollup_class_json_to_bank_json(jsonb, int8);

CREATE OR REPLACE FUNCTION taxonomy.rollup_class_json_to_bank_json(p_class_json jsonb, p_bank_schema_id bigint)
 RETURNS jsonb
 LANGUAGE sql
AS $function$
WITH pairs AS (
  SELECT k AS class_key, (p_class_json ->> k)::numeric AS cnt
  FROM jsonb_object_keys(p_class_json) AS t(k)
),
mapped AS (
  SELECT m.bank_key, SUM(p.cnt)::bigint AS total
  FROM pairs p
  JOIN taxonomy.class_to_bank_map m
    ON m.bank_schema_id = p_bank_schema_id
   AND m.class_key      = p.class_key
  GROUP BY m.bank_key
)
SELECT COALESCE((SELECT jsonb_object_agg(bank_key, total) FROM mapped), '{}'::jsonb);
$function$
;
