--- count tables are separated into three types:  
--- 1. Turning movement counts -intersections - (ServiceID = 35) -- starts with legacycounts table
--- 2. Tube counts for road segments -midblocks- (ServiceID = 34) -- starts with legacytubecounts table
--- 3. Survey counts for various purposes -study- (ServiceID = 33) -- starts with legacysurveycount table 

-- count table for turning movement counts at intersections
CREATE TABLE `legacycounts` (
  `QCOrderNo` int(11) NOT NULL, -- Foreign key to orders.OrderID
  `SiteNumber` int(11) NOT NULL,  -- virtual Foreign key to orderlocationtime.SiteNumber -- acts as primary key for this table
  `GroupID` int(11) NOT NULL DEFAULT '1',
  `StreetNameNorthSouth` varchar(300) DEFAULT NULL,
  `StreetNameEastWest` varchar(300) DEFAULT NULL,
  `City` varchar(100) DEFAULT NULL,
  `State` varchar(2) DEFAULT NULL,
  `Comments` varchar(400) DEFAULT NULL,
  `StartTime` datetime(3) DEFAULT NULL,
  `EndTime` datetime(3) DEFAULT NULL,
  `DayToCount` varchar(50) DEFAULT NULL,
  `DVD` char(1) DEFAULT NULL,
  `Photo` char(1) DEFAULT NULL,
  `DateScheduledForCount` datetime DEFAULT NULL,  -- count date -- fallboack to orderlocationtime.ScheduledDate if null
  `CounterID` varchar(50) DEFAULT NULL,
  `WeatherConditions` varchar(50) DEFAULT NULL,
  `County` varchar(50) DEFAULT NULL,
  `NBLaneConfig` varchar(7) DEFAULT NULL,
  `SBLaneConfig` varchar(7) DEFAULT NULL,
  `EBLaneConfig` varchar(7) DEFAULT NULL,
  `WBLaneConfig` varchar(7) DEFAULT NULL,
  `ControlType` varchar(12) DEFAULT NULL,
  `StopControlled` varchar(5) DEFAULT NULL,
  `CameraAngles` varchar(10) DEFAULT NULL,
  `T_Intersection` varchar(5) DEFAULT NULL,
  `NBDirectionality` varchar(10) DEFAULT NULL,
  `SBDirectionality` varchar(10) DEFAULT NULL,
  `EBDirectionality` varchar(10) DEFAULT NULL,
  `WBDirectionality` varchar(10) DEFAULT NULL,
  `Confidential` tinyint(3) unsigned DEFAULT NULL,
  `Latitude` float DEFAULT NULL,
  `Longitude` float DEFAULT NULL,
  `SpecialStreetIssues` varchar(100) DEFAULT NULL,
  `Notes` varchar(300) DEFAULT NULL,
  `Rate` float DEFAULT NULL,
  `AdditionalRate` float DEFAULT NULL,
  `RateCategory` varchar(20) DEFAULT NULL,
  `IntID` varchar(50) DEFAULT NULL,
  `ClientSiteID` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`SiteNumber`),
  KEY `LegacyCounts_IX_LegacyCounts` (`QCOrderNo`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- count data main table for turning movement counts at intersections
CREATE TABLE `legacycountsdatamain` (
  `SiteNumber` int(11) NOT NULL, -- foreign key to legacycounts.SiteNumber
  `IntervalType` int(11) DEFAULT NULL, -- summarized minutes (5, 15, 30, 60)
  `BeginTime` datetime(3) DEFAULT NULL, -- time only field -- date part is 1900-01-01
  `MovementDataIndex` int(11) NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`MovementDataIndex`),
  KEY `LegacyCountsDataMain_IX_LegacyCountsDataMain` (`SiteNumber`)
) ENGINE=InnoDB AUTO_INCREMENT=5452914 DEFAULT CHARSET=latin1;

-- summarized count data table for turning movement counts at intersections
CREATE TABLE `legacycountsdatamovement` (
  `MovementDataIndex` int(11) NOT NULL, -- foreign key to legacycountsdatamain.MovementDataIndex
  `ModeType` varchar(20) DEFAULT NULL,
  `Direction` varchar(5) DEFAULT NULL,
  `RightCount` smallint(6) DEFAULT NULL,
  `ThruCount` smallint(6) DEFAULT NULL,
  `LeftCount` smallint(6) DEFAULT NULL,
  `Edited` int(1) DEFAULT '0',
  KEY `LegacyCountsDataMovement_IX_LegacyCountsDataMovement` (`MovementDataIndex`),
  KEY `IX_LegacyCountsDataMovement_1` (`ModeType`,`Direction`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- ModeType values: ALL (all vehical types), AUX (), CA (cars), TR (heavies/trucks), BI (bikes), BU (buses), SC (scooters), PD (pedestrians), RTOT (right turn on red), UT (u-turns)
-- Direction values: NB, SB, EB, WB


-- count table for tube counts on road segments (midblocks)
-- starts with legacytubecounts table
CREATE TABLE `legacytubecounts` (
  `QCOrderNo` int(11) DEFAULT NULL,  -- Foreign key to orders.OrderID
  `SiteNumber` int(11) NOT NULL, -- virtual Foreign key to orderlocationtime.SiteNumber -- acts as primary key for this table
  `GroupID` int(11) NOT NULL DEFAULT '1',
  `LocationType` varchar(50) DEFAULT NULL,  -- mostly null
  `VolumePresent` char(1) DEFAULT NULL, 
  `SpeedPresent` char(1) DEFAULT NULL,
  `VehicleClassPresent` char(1) DEFAULT NULL,
  `Location` varchar(200) DEFAULT NULL,  -- same as locacation field in orderlocationtime table
  `Downstream` varchar(150) DEFAULT NULL,
  `Upstream` varchar(150) DEFAULT NULL,
  `SpecificLocation` varchar(150) DEFAULT NULL,
  `City` varchar(50) DEFAULT NULL,
  `State` varchar(2) DEFAULT NULL,
  `Comments` varchar(400) DEFAULT NULL,
  `StartDate` datetime(3) DEFAULT NULL,  -- time only field -- date part is 0000-00-00
  `EndDate` datetime(3) DEFAULT NULL,  -- time only field -- date part is 0000-00-00
  `ClientRefNo` varchar(50) DEFAULT NULL, -- mostly null
  `PostedSpeed` int(11) DEFAULT NULL, -- mostly null
  `CounterID` varchar(50) DEFAULT NULL, -- mostly null
  `RoadwayStudyType` varchar(10) DEFAULT NULL, -- mostly null
  `DrivewayStudyType` varchar(10) DEFAULT NULL, -- mostly null
  `Notes` varchar(300) DEFAULT NULL, 
  `Rate` float DEFAULT NULL, -- mostly null
  `AdditionalRate` float DEFAULT NULL, -- mostly null
  `RateCategory` varchar(50) DEFAULT NULL, -- mostly null
  `VehicleClassImportHeaders` varchar(500) DEFAULT NULL, -- mostly null
  PRIMARY KEY (`SiteNumber`),
  KEY `LegacyTubeCounts_IX_LegacyTubeCounts` (`QCOrderNo`,`SiteNumber`),
  KEY `IX_LegacyTubeCounts_SiteNumber` (`SiteNumber`),
  KEY `i_Legacytubecounts_QCOrderNo` (`QCOrderNo`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- count data main table for tube counts on road segments (midblocks)
-- defines the intervals for which data is collected
CREATE TABLE `legacytubecountsdatamain` (
  `SiteNumber` int(11) NOT NULL, -- foreign key to legacytubecounts.SiteNumber
  `CountDateTime` datetime DEFAULT NULL, -- date and time of the interval (full datetime)
  `IntervalType` int(11) DEFAULT NULL, -- summarized minutes (5, 15, 30, 60)
  `DataIndex` int(11) NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`DataIndex`),
  KEY `IX_LegacyCountsDataMain_1` (`SiteNumber`,`CountDateTime`,`IntervalType`),
  KEY `IX_LegacyCountsDataMain_DataIndex` (`DataIndex`),
  KEY `LegacyTubeCountsDataMain_IX_LegacyTubeCountsDataMain` (`SiteNumber`,`DataIndex`),
  KEY `i_legacytubecountsdatamain_SiteNumber` (`SiteNumber`),
  KEY `i_legacytubecountsdatamain_SNIT` (`SiteNumber`,`IntervalType`)
) ENGINE=InnoDB AUTO_INCREMENT=11890540 DEFAULT CHARSET=latin1;

-- summarized count data table for tube counts on road segments (midblocks) -- class breakout
CREATE TABLE `legacyvehicleclassdata` (
  `DataIndex` int(11) NOT NULL, -- foreign key to legacytubecountsdatamain.DataIndex
  `Direction` varchar(4) NOT NULL, -- direction of travel (NB, SB, EB, WB)
  `VehicleClassIndex` int(11) NOT NULL, -- vehicle class index (1 to 13) FHWA vehicle classes
  `VehicleCount` smallint(6) DEFAULT NULL,
  KEY `LegacyVehicleClassData_IX_LegacyVehicleClassData` (`DataIndex`,`Direction`,`VehicleClassIndex`),
  KEY `IX_LegacyVehicleClassData_DataIndex` (`DataIndex`),
  KEY `IX_LegacyVehicleClassData_DataIndexDirection` (`DataIndex`,`Direction`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- summarized count data table for tube counts on road segments (midblocks) -- volume breakout
CREATE TABLE `legacyvolumedata` (
  `DataIndex` int(11) NOT NULL, -- foreign key to legacytubecountsdatamain.DataIndex
  `Direction` varchar(4) NOT NULL, -- direction of travel (NB, SB, EB, WB)
  `VolumeCount` smallint(6) DEFAULT NULL,
  KEY `LegacyVolumeData_IX_LegacyVolumeData` (`DataIndex`,`Direction`),
  KEY `IX_LegacyVolumeData_DataIndex` (`DataIndex`),
  KEY `IX_LegacyVolumeData_DataIndexDirection` (`DataIndex`,`Direction`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- summarized count data table for tube counts on road segments (midblocks) -- speed breakout
CREATE TABLE `legacyspeeddata` (
  `DataIndex` int(11) NOT NULL, -- foreign key to legacytubecountsdatamain.DataIndex
  `Direction` varchar(4) NOT NULL, -- direction of travel (NB, SB, EB, WB)
  `StartSpeed` smallint(6) NOT NULL, -- speed bin start value
  `EndSpeed` smallint(6) NOT NULL, -- speed bin end value
  `SpeedCount` smallint(6) DEFAULT NULL,
  KEY `LegacySpeedData_IX_LegacySpeedData` (`DataIndex`,`Direction`,`StartSpeed`),
  KEY `IX_LegacySpeedData_DataIndex` (`DataIndex`),
  KEY `IX_LegacySpeedData_DataIndexDirection` (`DataIndex`,`Direction`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- count table for survey counts for various purposes (study)
-- starts with legacysurveycount table
CREATE TABLE `legacysurveycounts` (
  `QCOrderNo` int(11) NOT NULL, -- Foreign key to orders.OrderID
  `SiteNumber` int(11) NOT NULL, -- virtual Foreign key to orderlocationtime.SiteNumber -- acts as primary key for this table
  `GroupID` int(11) NOT NULL DEFAULT '1',
  `SurveyType` int(11) NOT NULL DEFAULT '0',
  `Location` varchar(100) DEFAULT NULL,
  `DayType` varchar(50) DEFAULT NULL, -- mostly null
  `DateType` varchar(50) DEFAULT NULL, -- mostly null
  `DayToCount` varchar(50) DEFAULT NULL, -- days to count (e.g., Mon, Tue, Wed)
  `StartDate` datetime(3) DEFAULT NULL, -- time only field -- date part is 1900-01-01
  `EndDate` datetime(3) DEFAULT NULL,   -- time only field -- date part is 1900-01-01
  `StartTime` datetime(3) DEFAULT NULL, -- mostly null
  `EndTime` datetime(3) DEFAULT NULL, -- mostly null
  `City` varchar(75) DEFAULT NULL,
  `State` varchar(2) DEFAULT NULL,
  `Comments` varchar(400) DEFAULT NULL,
  `Notes` varchar(300) DEFAULT NULL,
  `Rate` float DEFAULT NULL,
  `NumberOfCounters` int(11) DEFAULT NULL,
  PRIMARY KEY (`SiteNumber`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- study data is file based and stored externally in dropbox or s3  file locations are in dropbox_specialproject tables
CREATE TABLE `dropbox_specialproject` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `OrderID` int(11) NOT NULL, -- Foreign key to orders.OrderID
  `SiteNumber` int(11) DEFAULT NULL, -- foreign key to legacysurveycounts.SiteNumber
  `MainStreet` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `NSStreet` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `EWStreet` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `City` varchar(200) CHARACTER SET latin1 DEFAULT NULL,
  `StateCode` varchar(2) CHARACTER SET latin1 DEFAULT NULL,
  `StudyType` varchar(200) COLLATE utf8_bin DEFAULT NULL,
  `FullPath` varchar(500) CHARACTER SET latin1 DEFAULT NULL,  -- dropbox or s3 full path
  `FileName` varchar(200) CHARACTER SET latin1 DEFAULT NULL, -- file name only
  `FileType` varchar(200) CHARACTER SET latin1 DEFAULT NULL, -- file extension/type
  `FileId` varchar(250) CHARACTER SET latin1 DEFAULT NULL,
  `FileSize` varchar(25) CHARACTER SET latin1 DEFAULT NULL,
  `Comments` text CHARACTER SET latin1,
  `ContentHash` varchar(500) CHARACTER SET latin1 DEFAULT NULL,
  `CountDate` varchar(10) COLLATE utf8_bin DEFAULT NULL, -- YYYYMMDD
  `StartTime` varchar(10) COLLATE utf8_bin DEFAULT NULL, -- HHMM
  `EndTime` varchar(10) COLLATE utf8_bin DEFAULT NULL, -- HHMM
  `IntervalSize` varchar(10) COLLATE utf8_bin DEFAULT NULL, -- in minutes
  `Status` varchar(10) CHARACTER SET latin1 DEFAULT NULL,
  `ClientModified` datetime DEFAULT NULL,
  `ServerModified` datetime DEFAULT NULL,
  `CreatedDate` datetime DEFAULT NULL,
  `ModifiedDate` datetime DEFAULT NULL,
  `ModifiedByUserID` varchar(40) CHARACTER SET latin1 DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=94 DEFAULT CHARSET=utf8 COLLATE=utf8_bin;