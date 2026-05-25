-- DROP SCHEMA json_contract;

CREATE SCHEMA json_contract AUTHORIZATION tcmsdbadm;
-- json_contract.schema_registry definition

-- Drop table

-- DROP TABLE json_contract.schema_registry;

CREATE TABLE json_contract.schema_registry (
	schema_id text NOT NULL,
	json_schema jsonb NOT NULL,
	created_at timestamptz DEFAULT now() NULL,
	description text NULL,
	CONSTRAINT schema_registry_pkey PRIMARY KEY (schema_id)
);



-- DROP FUNCTION json_contract.assert_known_payload_schema();

CREATE OR REPLACE FUNCTION json_contract.assert_known_payload_schema()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  PERFORM 1 FROM json_contract.schema_registry WHERE schema_id = NEW.payload_schema_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Unknown payload schema id: %', NEW.payload_schema_id;
  END IF;
  RETURN NEW;
END $function$
;
