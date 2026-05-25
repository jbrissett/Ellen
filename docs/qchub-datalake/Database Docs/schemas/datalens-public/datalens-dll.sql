CREATE TABLE public.bucket_name (
	bucket_name_id int4 NOT NULL,
	"name" varchar(200) NULL,
	description varchar(200) NULL,
	CONSTRAINT bucket_name_pkey PRIMARY KEY (bucket_name_id)
);


-- public.count_operations definition

-- Drop table

-- DROP TABLE public.count_operations;

CREATE TABLE public.count_operations (
	count_operation_id serial4 NOT NULL,
	initiation_type varchar(50) NOT NULL,
	initiation_type_id int4 NOT NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	merge_initiated bool DEFAULT false NULL,
	CONSTRAINT count_operations_pk PRIMARY KEY (count_operation_id)
);


-- public.format_video definition

-- Drop table

-- DROP TABLE public.format_video;

CREATE TABLE public.format_video (
	format_video_id int4 NOT NULL,
	"name" varchar(50) NULL
);


-- public.gates definition

-- Drop table

-- DROP TABLE public.gates;

CREATE TABLE public.gates (
	id serial4 NOT NULL,
	short_name varchar(255) NULL,
	description varchar(255) NULL,
	gates_coordinates varchar(5000) NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	ped_gates varchar(255) NULL,
	gate_type int4 DEFAULT 1 NOT NULL,
	rtor varchar(255) NULL,
	review_area_coords varchar(5000) NULL,
	zoom_level numeric NULL,
	speed_gates varchar(5000) NULL,
	speed_distances varchar(50) NULL,
	CONSTRAINT gates_pkey PRIMARY KEY (id)
);


-- public.processing_log definition

-- Drop table

-- DROP TABLE public.processing_log;

CREATE TABLE public.processing_log (
	id serial4 NOT NULL,
	"FK_video_files_id" int4 NOT NULL,
	message_context varchar(255) NOT NULL,
	message_type varchar(255) NOT NULL,
	message text NULL,
	"notificationFlag" bool NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	CONSTRAINT processing_log_pkey PRIMARY KEY (id)
);


-- public.projects definition

-- Drop table

-- DROP TABLE public.projects;

CREATE TABLE public.projects (
	project_id int4 DEFAULT nextval('project_id_seq'::regclass) NOT NULL,
	order_no int4 NULL,
	project_name varchar(255) NOT NULL,
	order_date timestamptz NULL,
	qc_office varchar(255) NULL,
	qc_contact_name varchar(255) NULL,
	qc_video_processing_manager varchar(255) NULL,
	company varchar(255) NULL,
	desired_delivery_date timestamptz NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	notification bool NULL,
	"comments" varchar(512) NULL,
	project_status varchar(255) NULL,
	"FK_organization_id" int4 NULL,
	ops_checklist_location varchar(255) NULL,
	ops_checklist_link varchar(255) NULL,
	external_order_no varchar(10) NULL,
	near_miss_report_dir varchar(255) DEFAULT ''::character varying NULL,
	near_miss_report_queued bool DEFAULT false NULL,
	CONSTRAINT projects_id_unique UNIQUE (project_id),
	CONSTRAINT projects_pk PRIMARY KEY (project_id)
);
CREATE UNIQUE INDEX projects_order_no_idx ON public.projects USING btree (order_no);
CREATE INDEX projects_order_no_text_like_idx ON public.projects USING btree (((order_no)::text) text_pattern_ops);


-- public.rate_custom definition

-- Drop table

-- DROP TABLE public.rate_custom;

CREATE TABLE public.rate_custom (
	id serial4 NOT NULL,
	"type" varchar(255) NULL,
	"from" numeric(10, 2) NULL,
	"to" numeric(10, 2) NULL,
	rate numeric(10, 2) NULL,
	CONSTRAINT rate_custom_pkey PRIMARY KEY (id)
);


-- public.sources definition

-- Drop table

-- DROP TABLE public.sources;

CREATE TABLE public.sources (
	source_id int4 DEFAULT nextval('source_id_seq'::regclass) NOT NULL,
	"name" varchar NULL,
	description varchar NULL,
	CONSTRAINT source_id_pkey PRIMARY KEY (source_id)
);


-- public.locations definition

-- Drop table

-- DROP TABLE public.locations;

CREATE TABLE public.locations (
	location_id serial4 NOT NULL,
	"FK_project_id" int4 NOT NULL,
	"location" varchar(255) NOT NULL,
	latitude numeric NULL,
	longitude numeric NULL,
	count_type varchar(255) NULL,
	ops_checklist_location varchar(255) NULL,
	ops_checklist_link varchar(255) NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	rtor_required bool DEFAULT false NULL,
	"orderLocationID" int4 NULL,
	intersection_stop_control varchar(25) NULL,
	CONSTRAINT locations_pkey PRIMARY KEY (location_id),
	CONSTRAINT "location_FK_project_id_fkey" FOREIGN KEY ("FK_project_id") REFERENCES public.projects(project_id) ON DELETE CASCADE ON UPDATE CASCADE
);


-- public.sitecodes definition

-- Drop table

-- DROP TABLE public.sitecodes;

CREATE TABLE public.sitecodes (
	sc_id serial4 NOT NULL,
	"FK_location_id" int4 NOT NULL,
	sitecode int8 NULL,
	start_time time NOT NULL,
	end_time time NULL,
	count_duration_hrs int4 NULL,
	count_days _text NULL,
	count_classification varchar(255) NULL,
	"interval" int4 NULL,
	actual_start_time time NULL,
	actual_end_time time NULL,
	destination_dir varchar(255) NULL,
	actual_start_date date NULL,
	actual_duration_min int4 NULL,
	pedestrians bool NULL,
	bike bool NULL,
	rtor bool NULL,
	count_duration_minutes int4 NULL,
	external_sitecode varchar(15) NULL,
	count_duration_min int4 NULL,
	count_interval int4 NULL,
	separate_days_flag bool NULL,
	is_supplementary bool NULL,
	end_datetime timestamptz NULL,
	start_datetime timestamptz NULL,
	duration int4 NULL,
	actual_start_datetime timestamptz NULL,
	actual_end_datetime timestamptz NULL,
	CONSTRAINT sitecodes_pkey PRIMARY KEY (sc_id),
	CONSTRAINT "sitecodes_FK_location_id_fkey" FOREIGN KEY ("FK_location_id") REFERENCES public.locations(location_id) ON DELETE CASCADE ON UPDATE CASCADE
);

-- public.camera_setup definition

-- Drop table

-- DROP TABLE public.camera_setup;

CREATE TABLE public.camera_setup (
	camera_setup_id serial4 NOT NULL,
	"FK_location_id" int4 NOT NULL,
	latitude numeric NULL,
	longitude numeric NULL,
	sequence_number int4 NULL,
	camera_number varchar(255) NULL,
	camera_location_description varchar(500) NULL,
	view_direction_degrees int4 NULL,
	camera_type varchar(255) NULL,
	height int4 NULL,
	"comments" text NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	triangle json NULL,
	view_direction varchar(255) NULL,
	destination_dir varchar(255) NULL,
	conflict_report_generated bool DEFAULT false NULL,
	CONSTRAINT camera_setup_pkey PRIMARY KEY (camera_setup_id),
	CONSTRAINT "camera_setup_FK_location_id_fkey" FOREIGN KEY ("FK_location_id") REFERENCES public.locations(location_id) ON DELETE CASCADE ON UPDATE CASCADE
);


-- public.camera_setup_counts definition

-- Drop table

-- DROP TABLE public.camera_setup_counts;

CREATE TABLE public.camera_setup_counts (
	camera_setup_count_id serial4 NOT NULL,
	"FK_camera_setup_id" int4 NOT NULL,
	count_approach varchar(255) NOT NULL,
	count_type varchar(255) NOT NULL,
	count numeric NOT NULL,
	include_in_sitecode_count bool NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	"FK_sc_id" int4 NULL,
	CONSTRAINT camera_setup_counts_pkey PRIMARY KEY (camera_setup_count_id),
	CONSTRAINT "camera_setup_counts_FK_camera_setup_id_fkey" FOREIGN KEY ("FK_camera_setup_id") REFERENCES public.camera_setup(camera_setup_id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT camera_setup_counts_fk FOREIGN KEY ("FK_sc_id") REFERENCES public.sitecodes(sc_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX camera_setup_counts_fk_camera_setup_id_idx ON public.camera_setup_counts USING btree ("FK_camera_setup_id");
CREATE INDEX camera_setup_counts_fk_sc_id_idx ON public.camera_setup_counts USING btree ("FK_sc_id");


-- public.camera_sitecode_dir definition

-- Drop table

-- DROP TABLE public.camera_sitecode_dir;

CREATE TABLE public.camera_sitecode_dir (
	camera_sitecode_dir_id serial4 NOT NULL,
	"FK_camera_setup_id" int4 NOT NULL,
	"FK_sc_id" int4 NOT NULL,
	destination_dir varchar(255) NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	count_status varchar NULL,
	quality_metrics_status varchar(25) NULL,
	quality_metrics_current_version int4 NULL,
	registered_source_file_id int8 NULL,
	CONSTRAINT camera_sitecode_dir_pkey PRIMARY KEY (camera_sitecode_dir_id),
	CONSTRAINT "camera_sitecode_dir_FK_camera_setup_id_fkey" FOREIGN KEY ("FK_camera_setup_id") REFERENCES public.camera_setup(camera_setup_id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT "camera_sitecode_dir_FK_sc_id_fkey" FOREIGN KEY ("FK_sc_id") REFERENCES public.sitecodes(sc_id) ON DELETE CASCADE ON UPDATE CASCADE
);


-- public.video_files definition

-- Drop table

-- DROP TABLE public.video_files;

CREATE TABLE public.video_files (
	file_id serial4 NOT NULL,
	"FK_camera_setup_id" int4 NOT NULL,
	origin_name varchar(255) NOT NULL,
	base_name varchar(255) NOT NULL,
	bucket_name varchar(255) NULL,
	destination_dir varchar(255) NULL,
	start_datetime timestamptz NULL,
	duration int4 NULL,
	"size" numeric NULL,
	resolution varchar(255) NULL,
	frame_width int4 NULL,
	frame_height int4 NULL,
	frame_rate int4 NULL,
	bit_rate int4 NULL,
	file_type varchar(255) NULL,
	access_type varchar(255) NULL,
	dropbox_link varchar(255) NULL,
	dropbox_folder varchar(255) NULL,
	thumbnail_first varchar(255) NULL,
	thumbnail_middle varchar(255) NULL,
	thumbnail_last varchar(255) NULL,
	video_status varchar(255) NULL,
	status_message varchar(255) NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	automatic_process bool DEFAULT false NULL,
	actual_start_datetime timestamptz NULL,
	end_datetime timestamptz NULL,
	local_file_id int4 NULL,
	uploaded_by varchar NULL,
	start_datetime_confirmed bool NULL,
	supplemental bool DEFAULT false NULL,
	s3_storage_class varchar DEFAULT 'INTELLIGENT-TIERING'::character varying NULL,
	CONSTRAINT video_files_pkey PRIMARY KEY (file_id),
	CONSTRAINT "video_files_FK_camera_setup_id_fkey" FOREIGN KEY ("FK_camera_setup_id") REFERENCES public.camera_setup(camera_setup_id) ON UPDATE CASCADE
);


-- public.sitecode_video_files definition

-- Drop table

-- DROP TABLE public.sitecode_video_files;

CREATE TABLE public.sitecode_video_files (
	sitecode_video_file_id serial4 NOT NULL,
	"FK_sitecode" int4 NOT NULL,
	"FK_file_id" int4 NOT NULL,
	origin_name varchar(255) NOT NULL,
	base_name varchar(255) NOT NULL,
	bucket_name varchar(255) NOT NULL,
	start_datetime timestamptz NULL,
	duration_minutes int4 NULL,
	"size" numeric NULL,
	status varchar(255) NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	destination_dir varchar(255) NULL,
	zoom_level numeric NULL,
	review_area_coords varchar(5000) NULL,
	"FK_gate_id" int4 NULL,
	track_merge_complete bpchar(1) NULL,
	count_merge_complete bpchar(1) NULL,
	dropbox_path varchar(255) NULL,
	video_coverage_perc int4 NULL,
	conflict_report_generated bool DEFAULT false NULL,
	video_archive_path varchar(255) NULL,
	background_image varchar(255) NULL,
	annotation_pkg_key varchar(150) NULL,
	CONSTRAINT sitecode_video_files_pkey PRIMARY KEY (sitecode_video_file_id),
	CONSTRAINT "sitecode_video_files_FK_file_id_fkey" FOREIGN KEY ("FK_file_id") REFERENCES public.video_files(file_id) ON DELETE CASCADE ON UPDATE CASCADE,
	CONSTRAINT "sitecode_video_files_FK_gate_fkey" FOREIGN KEY ("FK_gate_id") REFERENCES public.gates(id) ON DELETE SET NULL ON UPDATE CASCADE,
	CONSTRAINT "sitecode_video_files_FK_sitecode_fkey" FOREIGN KEY ("FK_sitecode") REFERENCES public.sitecodes(sc_id) ON DELETE CASCADE ON UPDATE CASCADE
);


-- public.video_files_segments definition

-- Drop table

-- DROP TABLE public.video_files_segments;

CREATE TABLE public.video_files_segments (
	video_file_segment_id serial4 NOT NULL,
	"type" varchar(255) NOT NULL,
	origin_name varchar(255) NOT NULL,
	base_name varchar(255) NOT NULL,
	bucket_name varchar(255) NULL,
	"action" varchar(255) NULL,
	start_date timestamptz NULL,
	start_time time NULL,
	duration_minutes int4 NULL,
	status varchar(255) NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	destination_dir varchar(255) NULL,
	conflict_report_generated bool DEFAULT false NULL,
	track_images_created bool NULL,
	processing_date timestamptz(6) NULL,
	annotation_pkg_complete bool NULL,
	CONSTRAINT video_files_segments_pkey PRIMARY KEY (video_file_segment_id),
	CONSTRAINT "video_files_segments_FK_sitecode_video_id_fkey" FOREIGN KEY ("FK_sitecode_video_id") REFERENCES public.sitecode_video_files(sitecode_video_file_id) ON DELETE CASCADE ON UPDATE CASCADE
);


-- public.cam_setup_count_sc_videos definition

-- Drop table

-- DROP TABLE public.cam_setup_count_sc_videos;

CREATE TABLE public.cam_setup_count_sc_videos (
	id serial4 NOT NULL,
	"FK_camera_setup_id" int4 NOT NULL,
	"FK_sc_id" int4 NOT NULL,
	"FK_sitecode_video_id" int4 NOT NULL,
	"createdAt" timestamptz NOT NULL,
	"updatedAt" timestamptz NOT NULL,
	CONSTRAINT cam_setup_count_sc_videos_pk PRIMARY KEY (id),
	CONSTRAINT "camera_sc_video_FK_camera_setup_id_fkey" FOREIGN KEY ("FK_camera_setup_id") REFERENCES public.camera_setup(camera_setup_id),
	CONSTRAINT "camera_sc_video_FK_sc_id_fkey" FOREIGN KEY ("FK_sc_id") REFERENCES public.sitecodes(sc_id),
	CONSTRAINT "camera_sc_video_FK_sitecode_video_id_fkey" FOREIGN KEY ("FK_sitecode_video_id") REFERENCES public.sitecode_video_files(sitecode_video_file_id) ON DELETE CASCADE
);
CREATE INDEX cam_setup_count_sc_videos_fk_camera_setup_id_idx ON public.cam_setup_count_sc_videos USING btree ("FK_camera_setup_id");
CREATE INDEX cam_setup_count_sc_videos_fk_sc_id_idx ON public.cam_setup_count_sc_videos USING btree ("FK_sc_id");
CREATE INDEX cam_setup_count_sc_videos_fk_sitecode_video_id_idx ON public.cam_setup_count_sc_videos USING btree ("FK_sitecode_video_id");


-- public.near_misses definition

-- Drop table

-- DROP TABLE public.near_misses;

CREATE TABLE public.near_misses (
	near_miss_detection_id serial4 NOT NULL,
	"FK_video_file_segment_id" int4 NOT NULL,
	detection_start_date_time timestamptz NULL,
	detection_end_date_time timestamptz NULL,
	pet_seconds numeric NULL,
	first_movement_type varchar(255) NULL,
	first_movement_class varchar(255) NULL,
	second_movement_type varchar(255) NULL,
	second_movement_class varchar(255) NULL,
	video_clip bytea NULL,
	video_start_datetime timestamptz NULL,
	video_duration_seconds numeric NULL,
	video_size numeric NULL,
	video_format varchar(255) NULL,
	detector_processing_date timestamptz NULL,
	detector_version numeric NULL,
	"createdAt" timestamptz NULL,
	"updatedAt" timestamptz NULL,
	first_movement_track_id int4 NULL,
	second_movement_track_id int4 NULL,
	include_in_report bool DEFAULT true NULL,
	include_in_highlights bool DEFAULT false NULL,
	near_miss_video_s3_bucket varchar NULL,
	near_miss_video_s3_key varchar NULL,
	first_movement_det_bbox_xtl numeric NULL,
	first_movement_det_bbox_ytl numeric NULL,
	first_movement_det_bbox_xbr numeric NULL,
	first_movement_det_bbox_ybr numeric NULL,
	second_movement_det_bbox_xtl numeric NULL,
	second_movement_det_bbox_ytl numeric NULL,
	second_movement_det_bbox_xbr numeric NULL,
	second_movement_det_bbox_ybr numeric NULL,
	order_no int4 NULL,
	sitecode int8 NULL,
	camera_number int4 NULL,
	first_movement_speed_mph numeric NULL,
	second_movement_speed_mph numeric NULL,
	count_duration_hrs numeric NULL,
	location_id int4 NULL,
	"location" varchar(255) NULL,
	camera_setup_id int4 NULL,
	view_direction varchar(50) NULL,
	CONSTRAINT near_misses_pkey PRIMARY KEY (near_miss_detection_id),
	CONSTRAINT near_misses_fk FOREIGN KEY ("FK_video_file_segment_id") REFERENCES public.video_files_segments(video_file_segment_id) ON DELETE CASCADE
);
CREATE INDEX near_misses_fk_track_ids_idx ON public.near_misses USING btree ("FK_video_file_segment_id", first_movement_track_id, second_movement_track_id);
CREATE INDEX near_misses_fk_video_file_segment_id_idx ON public.near_misses USING btree ("FK_video_file_segment_id");



-- public.trajectory_hour_bundle definition

-- Drop table

-- DROP TABLE public.trajectory_hour_bundle;

CREATE TABLE public.trajectory_hour_bundle (
	trajectory_bundle_id bigserial NOT NULL,
	fk_video_file_segment_id int4 NOT NULL,
	fk_sitecode_video_id int4 NOT NULL,
	sitecode_id int4 NOT NULL,
	sitecode_number text NULL,
	parent_base_name text NOT NULL,
	segment_base_name text NOT NULL,
	hour_start timestamptz NOT NULL,
	hour_end timestamptz NOT NULL,
	image_width int4 NOT NULL,
	image_height int4 NOT NULL,
	s3_key_paths text NOT NULL,
	s3_key_index text NOT NULL,
	s3_key_endpoints text NOT NULL,
	s3_key_density text NULL,
	trajectories_count int4 NOT NULL,
	t0_ms int4 NOT NULL,
	t1_ms int4 NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	updated_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT thb_seg_unique UNIQUE (fk_video_file_segment_id),
	CONSTRAINT trajectory_hour_bundle_pkey PRIMARY KEY (trajectory_bundle_id),
	CONSTRAINT trajectory_hour_bundle_fk_sitecode_video_id_fkey FOREIGN KEY (fk_sitecode_video_id) REFERENCES public.sitecode_video_files(sitecode_video_file_id) ON DELETE CASCADE,
	CONSTRAINT trajectory_hour_bundle_fk_video_file_segment_id_fkey FOREIGN KEY (fk_video_file_segment_id) REFERENCES public.video_files_segments(video_file_segment_id) ON DELETE CASCADE,
	CONSTRAINT trajectory_hour_bundle_sitecode_id_fkey FOREIGN KEY (sitecode_id) REFERENCES public.sitecodes(sc_id) ON DELETE CASCADE
);
CREATE INDEX thb_parent_time_idx ON public.trajectory_hour_bundle USING btree (fk_sitecode_video_id, hour_start);
CREATE INDEX thb_sitecode_time_idx ON public.trajectory_hour_bundle USING btree (sitecode_id, hour_start);


-- public."vw_de-dupped_near_misses" source

CREATE OR REPLACE VIEW public."vw_de-dupped_near_misses"
AS WITH location_near_misses AS (
         SELECT nm.near_miss_detection_id,
            nm.detection_start_date_time,
            timezone('UTC'::text, nm.detection_start_date_time) AS start_date,
            p.order_no,
            s.sitecode,
            s.count_duration_hrs,
            s.sc_id,
            l.location_id,
            l.location,
            cs.camera_setup_id,
            nm.pet_seconds,
            nm.first_movement_type,
            nm.first_movement_class,
            nm.second_movement_type,
            nm.second_movement_class,
            nm.first_movement_track_id,
            nm.second_movement_track_id,
            nm.include_in_report,
            nm.include_in_highlights,
            nm."createdAt",
            nm."FK_video_file_segment_id",
            nm.near_miss_video_s3_key,
            vfs.destination_dir,
            cs.view_direction
           FROM near_misses nm
             JOIN video_files_segments vfs ON vfs.video_file_segment_id = nm."FK_video_file_segment_id"
             JOIN sitecode_video_files svf ON svf.sitecode_video_file_id = vfs."FK_sitecode_video_id"
             JOIN video_files vf ON vf.file_id = svf."FK_file_id"
             JOIN sitecodes s ON s.sc_id = svf."FK_sitecode"
             JOIN locations l ON l.location_id = s."FK_location_id"
             JOIN camera_setup cs ON cs.camera_setup_id = vf."FK_camera_setup_id"
             JOIN projects p ON l."FK_project_id" = p.project_id
        ), near_misses_with_lag AS (
         SELECT lag(location_near_misses.detection_start_date_time) OVER (ORDER BY location_near_misses.detection_start_date_time) AS prev_detection_time,
            lag(location_near_misses.camera_setup_id) OVER (ORDER BY location_near_misses.detection_start_date_time) AS prev_camera_setup_id,
            location_near_misses.near_miss_detection_id,
            location_near_misses.detection_start_date_time,
            location_near_misses.start_date,
            location_near_misses.order_no,
            location_near_misses.sitecode,
            location_near_misses.count_duration_hrs,
            location_near_misses.sc_id,
            location_near_misses.location_id,
            location_near_misses.location,
            location_near_misses.camera_setup_id,
            location_near_misses.pet_seconds,
            location_near_misses.first_movement_type,
            location_near_misses.first_movement_class,
            location_near_misses.second_movement_type,
            location_near_misses.second_movement_class,
            location_near_misses.first_movement_track_id,
            location_near_misses.second_movement_track_id,
            location_near_misses.include_in_report,
            location_near_misses.include_in_highlights,
            location_near_misses."createdAt",
            location_near_misses."FK_video_file_segment_id",
            location_near_misses.near_miss_video_s3_key,
            location_near_misses.destination_dir,
            location_near_misses.view_direction
           FROM location_near_misses
        ), proximity_groups AS (
         SELECT sum(
                CASE
                    WHEN near_misses_with_lag.prev_detection_time IS NULL THEN 1
                    WHEN date_part('epoch'::text, near_misses_with_lag.detection_start_date_time - near_misses_with_lag.prev_detection_time) > 2::double precision THEN 1
                    ELSE 0
                END) OVER (ORDER BY near_misses_with_lag.detection_start_date_time) AS time_group_id,
            near_misses_with_lag.prev_detection_time,
            near_misses_with_lag.prev_camera_setup_id,
            near_misses_with_lag.near_miss_detection_id,
            near_misses_with_lag.detection_start_date_time,
            near_misses_with_lag.start_date,
            near_misses_with_lag.order_no,
            near_misses_with_lag.sitecode,
            near_misses_with_lag.count_duration_hrs,
            near_misses_with_lag.sc_id,
            near_misses_with_lag.location_id,
            near_misses_with_lag.location,
            near_misses_with_lag.camera_setup_id,
            near_misses_with_lag.pet_seconds,
            near_misses_with_lag.first_movement_type,
            near_misses_with_lag.first_movement_class,
            near_misses_with_lag.second_movement_type,
            near_misses_with_lag.second_movement_class,
            near_misses_with_lag.first_movement_track_id,
            near_misses_with_lag.second_movement_track_id,
            near_misses_with_lag.include_in_report,
            near_misses_with_lag.include_in_highlights,
            near_misses_with_lag."createdAt",
            near_misses_with_lag."FK_video_file_segment_id",
            near_misses_with_lag.near_miss_video_s3_key,
            near_misses_with_lag.destination_dir,
            near_misses_with_lag.view_direction
           FROM near_misses_with_lag
        ), detection_counts AS (
         SELECT proximity_groups.time_group_id,
            count(*) AS total_detections,
            count(DISTINCT proximity_groups.camera_setup_id) AS distinct_cameras
           FROM proximity_groups
          GROUP BY proximity_groups.time_group_id
        ), ranked_detections AS (
         SELECT row_number() OVER (PARTITION BY pg.time_group_id ORDER BY pg.pet_seconds DESC) AS row_num_highest_pet,
            dc.total_detections,
            dc.distinct_cameras,
            pg.time_group_id,
            pg.prev_detection_time,
            pg.prev_camera_setup_id,
            pg.near_miss_detection_id,
            pg.detection_start_date_time,
            pg.start_date,
            pg.order_no,
            pg.sitecode,
            pg.count_duration_hrs,
            pg.sc_id,
            pg.location_id,
            pg.location,
            pg.camera_setup_id,
            pg.pet_seconds,
            pg.first_movement_type,
            pg.first_movement_class,
            pg.second_movement_type,
            pg.second_movement_class,
            pg.first_movement_track_id,
            pg.second_movement_track_id,
            pg.include_in_report,
            pg.include_in_highlights,
            pg."createdAt",
            pg."FK_video_file_segment_id",
            pg.near_miss_video_s3_key,
            pg.destination_dir,
            pg.view_direction
           FROM proximity_groups pg
             JOIN detection_counts dc ON pg.time_group_id = dc.time_group_id
        )
 SELECT near_miss_detection_id,
    order_no,
    location_id,
    location,
    sitecode,
    sc_id,
    count_duration_hrs,
    include_in_report,
    include_in_highlights,
    detection_start_date_time,
    timezone('UTC'::text, detection_start_date_time) AS start_date,
    camera_setup_id,
    pet_seconds,
    first_movement_type,
    first_movement_class,
    second_movement_type,
    second_movement_class,
    first_movement_track_id,
    second_movement_track_id,
    "createdAt",
    near_miss_video_s3_key,
    destination_dir,
    view_direction
   FROM ranked_detections
  WHERE total_detections > 2 OR total_detections = 1 OR total_detections = 2 AND distinct_cameras = 1 OR total_detections = 2 AND distinct_cameras > 1 AND row_num_highest_pet = 1
  ORDER BY sitecode, detection_start_date_time;


-- public.vw_locations_summary source

CREATE OR REPLACE VIEW public.vw_locations_summary
AS SELECT t.location_id,
    t."FK_project_id",
    t.location,
    t.latitude,
    t.longitude,
    t.count_type,
    t.ops_checklist_location,
    t.ops_checklist_link,
    t.rtor_required,
    t."createdAt",
    t."updatedAt",
    t."orderLocationID",
    t.num_counts,
    t.sc,
    t.num_sc_videos,
    count(cs.camera_setup_id) AS num_cameras,
    t.sc_creating,
    t.sc_ready,
    t.sc_processing,
    t.sc_ready_for_gates,
    t.sc_ready_for_count,
    t.sc_counting,
    t.sc_counted,
    t.sc_error_processing,
    t.sc_error_counting,
    t.sc_complete,
    t.sc_cancelled,
    t.sc_conflicted,
        CASE
            WHEN t.num_sc_videos = 0 THEN 'Pending videos'::text
            WHEN t.num_sc_videos = (t.sc_complete + t.sc_cancelled) THEN 'Complete'::text
            WHEN t.sc_ready_for_gates > 0 THEN 'Pending gates'::text
            WHEN t.sc_ready_for_count > 0 THEN 'Pending count'::text
            WHEN t.sc_counted > 0 THEN 'Count ready'::text
            WHEN t.sc_processing > 0 OR t.sc_creating > 0 OR t.sc_counting > 0 THEN 'Active'::text
            WHEN t.sc_ready > 0 THEN 'Videos uploaded'::text
            ELSE 'Review needed'::text
        END AS status
   FROM ( SELECT l.location_id,
            l."FK_project_id",
            l.location,
            l.latitude,
            l.longitude,
            l.count_type,
            l.ops_checklist_location,
            l.ops_checklist_link,
            l.rtor_required,
            l."createdAt",
            l."updatedAt",
            l."orderLocationID",
            count(DISTINCT s.sitecode) AS num_counts,
            string_agg(s.sitecode::character varying::text, ','::text ORDER BY s.sc_id, s.sitecode) AS sc,
            count(svf.sitecode_video_file_id) AS num_sc_videos,
            count(
                CASE
                    WHEN svf.status::text = 'Creating'::text OR svf.status::text = 'Segmenting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_creating,
            count(
                CASE
                    WHEN svf.status::text = 'Count videos created'::text OR svf.status::text = 'Created'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready,
            count(
                CASE
                    WHEN svf.status::text = 'Processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Ready for gates'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready_for_gates,
            count(
                CASE
                    WHEN svf.status::text = 'Ready for count'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready_for_count,
            count(
                CASE
                    WHEN svf.status::text = 'Counting'::text OR svf.status::text = 'Adding to camera'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Counted'::text OR svf.status::text = 'Done'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counted,
            count(
                CASE
                    WHEN svf.status::text = 'Error processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Error counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Complete'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_complete,
            count(
                CASE
                    WHEN svf.status::text = 'Cancelled'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_cancelled,
            count(
                CASE
                    WHEN svf.status::text = 'No coverage'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_nocoverage,
            count(
                CASE
                    WHEN svf.conflict_report_generated = true THEN 1
                    ELSE NULL::integer
                END) AS sc_conflicted
           FROM locations l
             JOIN sitecodes s ON s."FK_location_id" = l.location_id
             LEFT JOIN sitecode_video_files svf ON s.sc_id = svf."FK_sitecode"
          GROUP BY l.location_id) t
     LEFT JOIN camera_setup cs ON cs."FK_location_id" = t.location_id
  GROUP BY t.location_id, t."FK_project_id", t.location, t.latitude, t.longitude, t.count_type, t.ops_checklist_location, t.ops_checklist_link, t."createdAt", t."updatedAt", t."orderLocationID", t.num_counts, t.num_sc_videos, t.sc_creating, t.sc_ready, t.sc_processing, t.sc_ready_for_gates, t.sc_ready_for_count, t.sc_counting, t.sc_counted, t.sc_error_processing, t.sc_error_counting, t.sc_complete, t.sc_cancelled, t.sc_conflicted, t.sc, t.rtor_required;


-- public.vw_projects_external_summary source

CREATE OR REPLACE VIEW public.vw_projects_external_summary
AS SELECT project_id,
    order_no,
    project_name,
    order_date,
    qc_video_processing_manager,
    desired_delivery_date,
    notification,
    comments,
    num_locations,
    num_sc_videos,
    sc_creating,
    sc_processing,
    sc_counting,
    sc_counted,
    sc_error_processing,
    sc_error_counting,
    sc_complete,
    sc_cancelled,
        CASE
            WHEN num_sc_videos = 0 THEN 'Pending videos'::text
            WHEN num_sc_videos = sc_cancelled THEN 'Canceled'::text
            WHEN num_sc_videos = (sc_complete + sc_cancelled) THEN 'Complete'::text
            WHEN sc_counted > 0 THEN 'Count ready'::text
            WHEN sc_processing > 0 OR sc_creating > 0 OR sc_counting > 0 THEN 'Processing'::text
            ELSE 'Pending review'::text
        END AS status,
    "FK_organization_id"
   FROM ( SELECT p.project_id,
            p."FK_organization_id",
            p.order_no,
            p.project_name,
            p.order_date,
            p.qc_video_processing_manager,
            p.desired_delivery_date,
            p.notification,
            p.comments,
            ( SELECT count(*) AS count
                   FROM locations l1
                  WHERE l1."FK_project_id" = p.project_id) AS num_locations,
            count(svf.sitecode_video_file_id) AS num_sc_videos,
            count(
                CASE
                    WHEN svf.status::text = 'Creating'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_creating,
            count(
                CASE
                    WHEN svf.status::text = 'Processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Counted'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counted,
            count(
                CASE
                    WHEN svf.status::text = 'Error processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Error counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Complete'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_complete,
            count(
                CASE
                    WHEN svf.status::text = 'Cancelled'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_cancelled
           FROM projects p
             JOIN locations l ON p.project_id = l."FK_project_id"
             JOIN sitecodes s ON s."FK_location_id" = l.location_id
             LEFT JOIN sitecode_video_files svf ON s.sc_id = svf."FK_sitecode"
          GROUP BY p.project_id, p."FK_organization_id", p.order_no, p.project_name, p.order_date, p.qc_video_processing_manager, p.desired_delivery_date, p.notification, p.comments) t;


-- public.vw_projects_summary source

CREATE OR REPLACE VIEW public.vw_projects_summary
AS WITH sc AS (
         SELECT p_1.project_id,
            l.location_id,
            s.sc_id
           FROM projects p_1
             JOIN locations l ON l."FK_project_id" = p_1.project_id
             JOIN sitecodes s ON s."FK_location_id" = l.location_id
        ), svf_by_sc AS (
         SELECT sc.project_id,
            sc.sc_id,
            bool_or(svf.status::text = 'Creating'::text) AS has_creating,
            bool_or(svf.status::text = ANY (ARRAY['Count videos created'::character varying, 'Created'::character varying, 'Ready'::character varying]::text[])) AS has_ready,
            bool_or(svf.status::text = 'Processing'::text) AS has_processing,
            bool_or(svf.status::text = 'Ready for gates'::text) AS has_ready_for_gates,
            bool_or(svf.status::text = 'Ready for count'::text) AS has_ready_for_count,
            bool_or(svf.status::text = ANY (ARRAY['Counting'::character varying, 'Combining'::character varying]::text[])) AS has_counting,
            bool_or(svf.status::text = ANY (ARRAY['Counted'::character varying, 'Done'::character varying]::text[])) AS has_counted,
            bool_or(svf.status::text = 'Error processing'::text) AS has_error_processing,
            bool_or(svf.status::text = 'Error counting'::text) AS has_error_counting,
            bool_or(svf.status::text = 'Complete'::text) AS has_complete,
            bool_or(svf.status::text = 'Cancelled'::text) AS has_cancelled,
            count(svf.sitecode_video_file_id) AS video_count
           FROM sc
             LEFT JOIN sitecode_video_files svf ON svf."FK_sitecode" = sc.sc_id
          GROUP BY sc.project_id, sc.sc_id
        ), proj_rollup AS (
         SELECT sc.project_id,
            count(DISTINCT sc.location_id) AS num_locations,
            count(DISTINCT sc.sc_id) AS num_sitecodes,
            COALESCE(sum(svf_by_sc.video_count), 0::numeric) AS num_sc_videos,
            count(*) FILTER (WHERE svf_by_sc.has_creating) AS sc_creating,
            count(*) FILTER (WHERE svf_by_sc.has_ready) AS sc_ready,
            count(*) FILTER (WHERE svf_by_sc.has_processing) AS sc_processing,
            count(*) FILTER (WHERE svf_by_sc.has_ready_for_gates) AS sc_ready_for_gates,
            count(*) FILTER (WHERE svf_by_sc.has_ready_for_count) AS sc_ready_for_count,
            count(*) FILTER (WHERE svf_by_sc.has_counting) AS sc_counting,
            count(*) FILTER (WHERE svf_by_sc.has_counted) AS sc_counted,
            count(*) FILTER (WHERE svf_by_sc.has_error_processing) AS sc_error_processing,
            count(*) FILTER (WHERE svf_by_sc.has_error_counting) AS sc_error_counting,
            count(*) FILTER (WHERE svf_by_sc.has_complete) AS sc_complete,
            count(*) FILTER (WHERE svf_by_sc.has_cancelled) AS sc_cancelled,
            count(*) FILTER (WHERE svf_by_sc.has_complete) AS sitecodes_with_videos
           FROM sc
             LEFT JOIN svf_by_sc ON svf_by_sc.project_id = sc.project_id AND svf_by_sc.sc_id = sc.sc_id
          GROUP BY sc.project_id
        ), status_calc AS (
         SELECT pr_1.project_id,
                CASE
                    WHEN pr_1.num_sc_videos = 0::numeric THEN 'Pending videos'::text
                    WHEN pr_1.sc_ready_for_gates > 0 THEN 'Pending gates'::text
                    WHEN pr_1.sc_ready_for_count > 0 THEN 'Pending count'::text
                    WHEN pr_1.sc_counted > 0 THEN 'Count ready'::text
                    WHEN (pr_1.sc_creating + pr_1.sc_processing + pr_1.sc_counting) > 0 THEN 'Active'::text
                    WHEN pr_1.sc_ready > 0 THEN 'Videos uploaded'::text
                    WHEN (pr_1.sc_complete + pr_1.sc_cancelled) = pr_1.num_sitecodes AND pr_1.num_sitecodes > 0 THEN 'Complete'::text
                    ELSE 'Review needed'::text
                END AS status
           FROM proj_rollup pr_1
        )
 SELECT p.project_id,
    p.order_no,
    p.ops_checklist_link,
    p.project_name,
    p.order_date,
    p.qc_office,
    p.qc_contact_name,
    p.qc_video_processing_manager,
    p.company,
    p.desired_delivery_date,
    p.notification,
    p.comments,
    pr.num_locations,
    pr.num_sc_videos,
    pr.num_sitecodes,
    pr.sitecodes_with_videos,
    pr.sc_creating,
    pr.sc_ready,
    pr.sc_processing,
    pr.sc_ready_for_gates,
    pr.sc_ready_for_count,
    pr.sc_counting,
    pr.sc_counted,
    pr.sc_error_processing,
    pr.sc_error_counting,
    pr.sc_complete,
    pr.sc_cancelled,
    sc2.status
   FROM projects p
     JOIN proj_rollup pr ON pr.project_id = p.project_id
     JOIN status_calc sc2 ON sc2.project_id = p.project_id;


-- public.vw_projects_summary_old source

CREATE OR REPLACE VIEW public.vw_projects_summary_old
AS SELECT project_id,
    order_no,
    ops_checklist_link,
    project_name,
    order_date,
    qc_office,
    qc_contact_name,
    qc_video_processing_manager,
    company,
    desired_delivery_date,
    notification,
    comments,
    num_locations,
    num_sc_videos,
    sc_creating,
    sc_ready,
    sc_processing,
    sc_ready_for_gates,
    sc_ready_for_count,
    sc_counting,
    sc_counted,
    sc_error_processing,
    sc_error_counting,
    sc_complete,
    sc_cancelled,
    sitecodes_with_videos,
    num_sitecodes,
        CASE
            WHEN num_sc_videos = 0 THEN 'Pending videos'::text
            WHEN num_sc_videos > 0 AND num_sc_videos = (sc_complete + sc_cancelled) AND num_sitecodes = sitecodes_with_videos THEN 'Complete'::text
            WHEN sc_ready_for_gates > 0 THEN 'Pending gates'::text
            WHEN sc_ready_for_count > 0 THEN 'Pending count'::text
            WHEN sc_counted > 0 THEN 'Count ready'::text
            WHEN sc_processing > 0 OR sc_creating > 0 OR sc_counting > 0 THEN 'Active'::text
            WHEN sc_ready > 0 THEN 'Videos uploaded'::text
            ELSE 'Review needed'::text
        END AS status,
    "FK_organization_id"
   FROM ( SELECT p.project_id,
            p."FK_organization_id",
            p.ops_checklist_link,
            p.order_no,
            p.project_name,
            p.order_date,
            p.qc_office,
            p.qc_contact_name,
            p.qc_video_processing_manager,
            p.company,
            p.desired_delivery_date,
            p.notification,
            p.comments,
            ( SELECT count(*) AS count
                   FROM locations l1
                  WHERE l1."FK_project_id" = p.project_id) AS num_locations,
            count(DISTINCT s.sc_id) AS num_sitecodes,
            count(svf.sitecode_video_file_id) AS num_sc_videos,
            count(
                CASE
                    WHEN svf.status::text = 'Complete'::text THEN s.sc_id
                    ELSE NULL::integer
                END) AS sitecodes_with_videos,
            count(
                CASE
                    WHEN svf.status::text = 'Creating'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_creating,
            count(
                CASE
                    WHEN svf.status::text = 'Count videos created'::text OR svf.status::text = 'Created'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready,
            count(
                CASE
                    WHEN svf.status::text = 'Processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Ready for gates'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready_for_gates,
            count(
                CASE
                    WHEN svf.status::text = 'Ready for count'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready_for_count,
            count(
                CASE
                    WHEN svf.status::text = 'Counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Counted'::text OR svf.status::text = 'Done'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counted,
            count(
                CASE
                    WHEN svf.status::text = 'Error processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Error counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Complete'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_complete,
            count(
                CASE
                    WHEN svf.status::text = 'Cancelled'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_cancelled
           FROM projects p
             JOIN locations l ON p.project_id = l."FK_project_id"
             JOIN sitecodes s ON s."FK_location_id" = l.location_id
             LEFT JOIN sitecode_video_files svf ON s.sc_id = svf."FK_sitecode"
          GROUP BY p.project_id, p."FK_organization_id", p.ops_checklist_link, p.order_no, p.project_name, p.order_date, p.qc_office, p.qc_contact_name, p.qc_video_processing_manager, p.company, p.desired_delivery_date, p.notification, p.comments) t;


-- public.vw_sitecodes_summary source

CREATE OR REPLACE VIEW public.vw_sitecodes_summary
AS SELECT sc_id,
    "FK_location_id",
    sitecode,
    start_time,
    end_time,
    count_duration_hrs,
    count_duration_min,
    count_days,
    count_classification,
    "interval",
    actual_start_time,
    actual_end_time,
    actual_start_date,
    actual_duration_min,
    destination_dir,
    is_supplementary,
    num_sc_videos,
    sc_creating,
    sc_ready,
    sc_processing,
    sc_ready_for_gates,
    sc_ready_for_count,
    sc_counting,
    sc_counted,
    sc_error_processing,
    sc_error_counting,
    sc_complete,
    sc_cancelled,
    sc_conflicted,
    count_interval,
    separate_days_flag,
        CASE
            WHEN num_sc_videos = 0 THEN 'Pending videos'::text
            WHEN num_sc_videos = (sc_complete + sc_cancelled) THEN 'Complete'::text
            WHEN sc_ready_for_gates > 0 THEN 'Pending gates'::text
            WHEN sc_ready_for_count > 0 THEN 'Pending count'::text
            WHEN sc_counted > 0 THEN 'Count ready'::text
            WHEN sc_counting > 0 THEN 'Counting'::text
            WHEN sc_combining > 0 THEN 'Combining counts'::text
            WHEN sc_processing > 0 OR sc_creating > 0 THEN 'Active'::text
            WHEN sc_ready > 0 THEN 'Video uploaded'::text
            ELSE 'Review needed'::text
        END AS status
   FROM ( SELECT s.sc_id,
            s."FK_location_id",
            s.sitecode,
            s.start_time,
            s.end_time,
            s.count_duration_hrs,
            s.count_duration_min,
            s.count_days,
            s.count_classification,
            s."interval",
            s.actual_start_time,
            s.actual_end_time,
            s.actual_start_date,
            s.actual_duration_min,
            s.destination_dir,
            s.is_supplementary,
            s.count_interval,
            s.separate_days_flag,
            count(svf.sitecode_video_file_id) AS num_sc_videos,
            count(
                CASE
                    WHEN svf.status::text = 'Creating'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_creating,
            count(
                CASE
                    WHEN svf.status::text = 'Created'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready,
            count(
                CASE
                    WHEN svf.status::text = 'Processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Ready for gates'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready_for_gates,
            count(
                CASE
                    WHEN svf.status::text = 'Ready for count'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_ready_for_count,
            count(
                CASE
                    WHEN svf.status::text = 'Counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Counted'::text OR svf.status::text = 'Done'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_counted,
            count(
                CASE
                    WHEN svf.status::text = 'Error processing'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_processing,
            count(
                CASE
                    WHEN svf.status::text = 'Error counting'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_error_counting,
            count(
                CASE
                    WHEN svf.status::text = 'Complete'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_complete,
            count(
                CASE
                    WHEN svf.status::text = 'Cancelled'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_cancelled,
            count(
                CASE
                    WHEN svf.conflict_report_generated = true THEN 1
                    ELSE NULL::integer
                END) AS sc_conflicted,
            count(
                CASE
                    WHEN svf.status::text = 'Combining'::text OR svf.status::text = 'Adding to camera'::text THEN 1
                    ELSE NULL::integer
                END) AS sc_combining
           FROM sitecodes s
             LEFT JOIN sitecode_video_files svf ON s.sc_id = svf."FK_sitecode"
          GROUP BY s.sc_id) t;

