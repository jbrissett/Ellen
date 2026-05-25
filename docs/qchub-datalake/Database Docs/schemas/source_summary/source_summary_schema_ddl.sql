-- DROP SCHEMA source_summary;

CREATE SCHEMA source_summary AUTHORIZATION tcmsdbadm;

-- DROP SEQUENCE source_summary.aoc_aoc_id_seq;

CREATE SEQUENCE source_summary.aoc_aoc_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE source_summary.aoc_notes_aoc_note_id_seq;

CREATE SEQUENCE source_summary.aoc_notes_aoc_note_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE source_summary.qa_metrics_qc_metrics_id_seq;

CREATE SEQUENCE source_summary.qa_metrics_qc_metrics_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE source_summary.summarized_data_interval_summarized_data_interval_id_seq;

CREATE SEQUENCE source_summary.summarized_data_interval_summarized_data_interval_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE source_summary.volume_by_movement_volume_by_movement_id_seq;

CREATE SEQUENCE source_summary.volume_by_movement_volume_by_movement_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;
-- DROP SEQUENCE source_summary.volume_by_speed_id_seq;

CREATE SEQUENCE source_summary.volume_by_speed_id_seq
	INCREMENT BY 1
	MINVALUE 1
	MAXVALUE 2147483647
	START 1
	CACHE 1
	NO CYCLE;-- source_summary.aoc definition

-- Drop table

-- DROP TABLE source_summary.aoc;

CREATE TABLE source_summary.aoc (
	aoc_id serial4 NOT NULL,
	file_id int4 NOT NULL,
	"version" int4 NOT NULL,
	start_summarized_data_interval_id int4 NOT NULL,
	start_interval_start timestamp NOT NULL,
	end_summarized_data_interval_id int4 NOT NULL,
	end_interval_start timestamp NOT NULL,
	created_at timestamptz DEFAULT now() NULL,
	created_by varchar(255) NOT NULL,
	reason_tags jsonb DEFAULT '[]'::jsonb NULL,
	action_tags jsonb DEFAULT '[]'::jsonb NULL,
	qa_status varchar(50) DEFAULT 'Open'::character varying NULL,
	has_attachments bool DEFAULT false NULL,
	is_active bool DEFAULT true NULL,
	CONSTRAINT aoc_pkey PRIMARY KEY (aoc_id)
);
CREATE INDEX aoc_file_id_idx ON source_summary.aoc USING btree (file_id, version);


-- source_summary.aoc_notes definition

-- Drop table

-- DROP TABLE source_summary.aoc_notes;

CREATE TABLE source_summary.aoc_notes (
	aoc_note_id serial4 NOT NULL,
	aoc_id int4 NOT NULL,
	note_time timestamptz DEFAULT now() NULL,
	note_by varchar(255) NOT NULL,
	note_text text NOT NULL,
	CONSTRAINT aoc_notes_pkey PRIMARY KEY (aoc_note_id)
);


-- source_summary.qa_metrics definition

-- Drop table

-- DROP TABLE source_summary.qa_metrics;

CREATE TABLE source_summary.qa_metrics (
	qc_metrics_id serial4 NOT NULL,
	summarized_data_interval_id int4 NOT NULL,
	interval_start timestamp NOT NULL,
	avg_detection_confidence numeric NULL,
	avg_classification_confidence numeric NULL,
	avg_detection_confidence_by_class jsonb NULL,
	avg_classification_confidence_by_class jsonb NULL,
	in_extrapolations int4 NULL,
	out_extrapolations int4 NULL,
	avg_between_gate_confidence numeric NULL,
	speed_15th_percentile numeric NULL,
	speed_50th_percentile numeric NULL,
	speed_85th_percentile numeric NULL,
	avg_detection_confidence_by_movement jsonb NULL,
	avg_classification_confidence_by_movement jsonb NULL,
	in_extrapolations_by_movement jsonb NULL,
	out_extrapolations_by_movement jsonb NULL,
	between_gate_conf_by_movement jsonb NULL,
	speed_15th_percentile_by_movement jsonb NULL,
	speed_50th_percentile_by_movement jsonb NULL,
	speed_85th_percentile_by_movement jsonb NULL,
	num_extrapolations int4 NULL,
	num_extrapolations_by_movement jsonb NULL,
	CONSTRAINT qa_metrics_pkey PRIMARY KEY (qc_metrics_id)
);
CREATE INDEX qa_sdi_interval ON source_summary.qa_metrics USING btree (summarized_data_interval_id, interval_start);


-- source_summary.summarized_data_interval definition

-- Drop table

-- DROP TABLE source_summary.summarized_data_interval;

CREATE TABLE source_summary.summarized_data_interval (
	summarized_data_interval_id serial4 NOT NULL,
	file_id int4 NOT NULL,
	interval_start timestamp NOT NULL,
	interval_end timestamp NOT NULL,
	created_at timestamp DEFAULT now() NULL,
	CONSTRAINT summarized_data_interval_pkey PRIMARY KEY (summarized_data_interval_id, interval_start)
)
PARTITION BY RANGE (interval_start);
CREATE INDEX summarized_data_interval_file_id_idx ON ONLY source_summary.summarized_data_interval USING btree (file_id);
CREATE INDEX t_sdi_brin_interval ON ONLY source_summary.summarized_data_interval USING brin (interval_start);
CREATE INDEX t_sdi_file_start ON ONLY source_summary.summarized_data_interval USING btree (file_id, interval_start);

-- source_summary.volume_by_movement definition

-- Drop table

-- DROP TABLE source_summary.volume_by_movement;

CREATE TABLE source_summary.volume_by_movement (
	volume_by_movement_id serial4 NOT NULL,
	summarized_data_interval_id int4 NOT NULL,
	interval_start timestamp NOT NULL,
	in_gate varchar(10) NULL,
	out_gate varchar(10) NULL,
	movement varchar(25) NULL,
	volume_count int4 NULL,
	volume_count_by_class jsonb NULL,
	volume_count_by_bank jsonb NULL,
	CONSTRAINT volume_by_movement_pkey PRIMARY KEY (volume_by_movement_id),
	CONSTRAINT fk_volume_summary FOREIGN KEY (summarized_data_interval_id,interval_start) REFERENCES source_summary.summarized_data_interval(summarized_data_interval_id,interval_start) ON DELETE CASCADE
);
CREATE INDEX vbm_sdi_interval_movement ON source_summary.volume_by_movement USING btree (summarized_data_interval_id, interval_start, movement);
CREATE INDEX volume_by_movement_summarized_data_interval_id_idx ON source_summary.volume_by_movement USING btree (summarized_data_interval_id, interval_start);

-- source_summary.volume_by_speed definition

-- Drop table

-- DROP TABLE source_summary.volume_by_speed;

CREATE TABLE source_summary.volume_by_speed (
	id serial4 NOT NULL,
	summarized_data_interval_id int4 NOT NULL,
	interval_start timestamp NOT NULL,
	speed_mph int4 NOT NULL,
	in_gate varchar(10) NULL,
	out_gate varchar(10) NULL,
	movement varchar(25) NULL,
	vehicle_count int4 NOT NULL,
	vehicle_count_by_class jsonb NULL,
	vehicle_count_by_bank jsonb NULL,
	CONSTRAINT volume_by_speed_pkey PRIMARY KEY (id),
	CONSTRAINT fk_summary FOREIGN KEY (summarized_data_interval_id,interval_start) REFERENCES source_summary.summarized_data_interval(summarized_data_interval_id,interval_start) ON DELETE CASCADE
);
CREATE INDEX vbs_sdi_interval_movement ON source_summary.volume_by_speed USING btree (summarized_data_interval_id, interval_start, movement, speed_mph);
CREATE INDEX volume_by_speed_summarized_data_interval_id_idx ON source_summary.volume_by_speed USING btree (summarized_data_interval_id, interval_start);
