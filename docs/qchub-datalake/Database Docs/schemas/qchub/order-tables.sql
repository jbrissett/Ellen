
-- primary order table
CREATE TABLE `order` (
  `OrderID` int(11) NOT NULL AUTO_INCREMENT,
  `ProjectID` int(11) DEFAULT NULL,  -- Foreign key to project.ProjectID
  `QCOfficeID` int(11) DEFAULT NULL, -- Foreign key to qcoffice.QCOfficeID
  `CompanyID` int(11) DEFAULT NULL, -- Foreign key to company.CompanyID
  `ContractID` int(11) DEFAULT NULL, -- Foreign key to contract.ContractID
  `OrderDate` datetime(3) DEFAULT NULL, 
  `DueDate` datetime(3) DEFAULT NULL,
  `QCLeadUserID` char(36) DEFAULT NULL, -- Foreign key to user.UserID
  `CustomerLeadUserID` char(36) DEFAULT NULL,
  `SpecialRequirements` longtext,
  `IsPublicData` tinyint(1) NOT NULL,
  `AllowThirdPartyAccess` tinyint(1) NOT NULL,
  `AllowResell` tinyint(1) NOT NULL,
  `DefaultPaymentCode` varchar(10) NOT NULL,
  `IsVideoOnly` tinyint(1) NOT NULL,
  `IsRush` tinyint(1) NOT NULL,
  `IsVRCInvolved` tinyint(1) NOT NULL,
  `ExpectedVRCArrivalDate` datetime(3) DEFAULT NULL,
  `SentToCustomerDate` datetime(3) DEFAULT NULL,
  `SentToCustomerByUserID` char(36) DEFAULT NULL,
  `SentToCustomerTrackingNumber` varchar(50) DEFAULT NULL,
  `ClientReferenceNo` varchar(50) DEFAULT NULL,
  `CreatedByUserID` char(36) DEFAULT NULL,
  `ModifiedByUserID` char(36) DEFAULT NULL,
  `ModifiedDate` datetime(3) DEFAULT NULL,
  `CombineGroups` tinyint(1) DEFAULT NULL,
  `ExcludeSetupFee` tinyint(1) DEFAULT NULL,
  `NextContactDate` datetime(3) DEFAULT NULL,
  `ProjectName` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `IsCancelled` tinyint(1) DEFAULT NULL,
  `ClientProjectNumber` varchar(50) DEFAULT NULL,
  `CreatedDate` datetime DEFAULT NULL,
  `DataPrivate` varchar(10) NOT NULL DEFAULT 'f',
  `PaymentTermID` int(11) DEFAULT '12',
  PRIMARY KEY (`OrderID`),
  KEY `i_Order_CompanyID` (`CompanyID`),
  KEY `i_Order_ContractID` (`ContractID`),
  KEY `i_Order_CustomerLeadUserId` (`CustomerLeadUserID`),
  KEY `i_Order_IsPublicData` (`IsPublicData`),
  KEY `i_Order_ProjectID` (`ProjectID`),
  KEY `i_Order_ProjectName` (`ProjectName`),
  KEY `i_Order_QCLeadUserID` (`QCLeadUserID`),
  KEY `i_Order_QCOfficeID` (`QCOfficeID`),
  KEY `i_Order_OrderID` (`OrderID`)
) ENGINE=InnoDB AUTO_INCREMENT=149296 DEFAULT CHARSET=latin1;

-- order service table
-- describes services associated with an order (type of counts)
CREATE TABLE `orderservice` (
  `OrderServiceID` int(11) NOT NULL AUTO_INCREMENT,
  `OrderID` int(11) NOT NULL,  -- Foreign key to order.OrderID
  `ServiceID` int(11) NOT NULL,
  `CreatedByUserID` char(36) NOT NULL,  -- Foreign key to user.UserID
  `CreatedDateTime` datetime(3) NOT NULL,
  `ModifiedByUserID` char(36) NOT NULL,
  `ModifiedDateTime` datetime(3) NOT NULL,
  `GroupNumber` int(11) DEFAULT NULL,
  `CustomServiceName` varchar(150) DEFAULT NULL,
  PRIMARY KEY (`OrderServiceID`),
  KEY `i_OrderService_OrderID` (`OrderID`),
  KEY `i_OrderService_ServiceID` (`ServiceID`),
  KEY `orderservice_OrderID_IDX` (`OrderID`,`ServiceID`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=40685 DEFAULT CHARSET=latin1;

-- servicetimeperiod table
-- reference from orderlocationtime.ServiceTimePeriodID
-- table describes the count times requested for an order service
CREATE TABLE `servicetimeperiod` (
  `ServiceTimePeriodID` int(11) NOT NULL AUTO_INCREMENT,
  `OrderServiceID` int(11) NOT NULL,  -- foreign key to orderservice.OrderServiceID
  `LocationPhotoImageID` int(11) DEFAULT NULL,
  `StartTime` time DEFAULT NULL,
  `EndTime` time DEFAULT NULL,
  `DueDate` datetime(3) DEFAULT NULL,
  `SpecialRequirements` longtext,
  `CreatedByUserID` char(36) DEFAULT NULL,
  `CreatedDate` datetime(3) DEFAULT NULL,
  `ModifiedByUserID` char(36) DEFAULT NULL,
  `ModifiedDate` datetime(3) DEFAULT NULL,
  `DurationHours` int(11) NOT NULL,
  `DurationMinutes` int(11) NOT NULL,
  `DurationSeconds` int(11) NOT NULL,
  PRIMARY KEY (`ServiceTimePeriodID`),
  KEY `i_ServiceTimePeriod_EndTime` (`EndTime`),
  KEY `i_ServiceTimePeriod_OrderServiceID` (`OrderServiceID`),
  KEY `i_ServiceTimePeriod_StartTime` (`StartTime`)
) ENGINE=InnoDB AUTO_INCREMENT=63210 DEFAULT CHARSET=latin1;

-- order locations table
-- describes locations associated with an order (where counts are taken)
CREATE TABLE `orderlocation` (
  `OrderLocationID` int(11) NOT NULL AUTO_INCREMENT,
  `OrderServiceID` int(11) NOT NULL, --foreign key to orderservice.OrderServiceID
  `City` varchar(200) DEFAULT NULL,
  `StateCode` char(2) DEFAULT NULL,
  `Zip` varchar(10) DEFAULT NULL,
  `SpecialRequirements` longtext,
  `PhotoImageID` int(11) DEFAULT NULL,
  `Latitude` double DEFAULT NULL,
  `Longitude` double DEFAULT NULL,
  `CreatedByUserID` char(36) DEFAULT NULL,
  `CreatedDate` datetime(3) DEFAULT NULL,
  `ModifiedByUserID` char(36) DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `LocationIndex` int(11) DEFAULT NULL,
  `x` float DEFAULT NULL,
  `y` float DEFAULT NULL,
  `z` float DEFAULT NULL,
  PRIMARY KEY (`OrderLocationID`),
  KEY `orderlocation_OrderServiceID_IDX` (`OrderServiceID`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=245898 DEFAULT CHARSET=latin1;

-- orderlocationstreet table
-- describes street information for order locations
CREATE TABLE `orderlocationstreet` (
  `OrderLocationStreetID` int(11) NOT NULL AUTO_INCREMENT,
  `OrderLocationID` int(11) NOT NULL, -- foreign key to orderlocation.OrderLocationID
  `StreetDirectionCode` varchar(10) NOT NULL,
  `StreetName` varchar(200) NOT NULL,
  `ClockwiseStreetOrder` int(11) NOT NULL,
  `CreatedByUserID` char(36) DEFAULT NULL,
  `CreatedDate` datetime(3) DEFAULT NULL,
  `ModifiedByUserID` char(36) DEFAULT NULL,
  `ModifiedDate` datetime(3) DEFAULT NULL,
  PRIMARY KEY (`OrderLocationStreetID`),
  KEY `i_OrderLocationStreet_StreetDirectionCode` (`StreetDirectionCode`),
  KEY `i_OrderLocationStreet_StreetName` (`StreetName`),
  KEY `i_OrderLocationStreet_OrderLocation` (`OrderLocationID`)
) ENGINE=InnoDB AUTO_INCREMENT=692487 DEFAULT CHARSET=latin1;

-- orderlocationtime table
-- describes count time information for order locations -- this is the table the SiteNumber (aka Sitecode) comes from
CREATE TABLE `orderlocationtime` (
  `OrderLocationTimeID` int(11) NOT NULL AUTO_INCREMENT,
  `SiteNumber` int(11) NOT NULL,
  `OrderLocationID` int(11) NOT NULL,  -- foreign key to orderlocation.OrderLocationID
  `ServiceTimePeriodID` int(11) NOT NULL, -- foreign key to servicetimeperiod.ServiceTimePeriodID
  `ExpectedVRCArrivalDate` datetime(3) DEFAULT NULL,
  `SentToCustomerDate` datetime(3) DEFAULT NULL,
  `SentToCustomerByUserID` char(36) DEFAULT NULL,
  `SentToCustomerTrackingNumber` varchar(50) DEFAULT NULL,
  `CreatedByUserID` char(36) DEFAULT NULL,
  `CreatedDate` datetime(3) DEFAULT NULL,
  `ModifiedByUserID` char(36) DEFAULT NULL,
  `ModifiedDate` datetime(3) DEFAULT NULL,
  `RateCategoryCode` varchar(10) DEFAULT NULL,
  `RateAmount` decimal(19,2) DEFAULT NULL,
  `VRCCountStartDateTime` datetime(3) DEFAULT NULL,
  `VRCCountEndDateTime` datetime(3) DEFAULT NULL,
  `TurnImage` longblob,
  `AdditionalRateAmount` decimal(19,2) DEFAULT NULL,
  `RequestedDate` datetime(3) DEFAULT NULL,  -- fallback count date if all other dates are null
  `DayToCount` varchar(10) DEFAULT NULL,
  `statusInvoice` varchar(45) NOT NULL DEFAULT '1',
  `description` varchar(250) DEFAULT NULL,
  PRIMARY KEY (`OrderLocationTimeID`),
  KEY `i_OrderLocationTime_OrderLocationID` (`OrderLocationID`),
  KEY `i_OrderLocationTime_RateCategoryCode` (`RateCategoryCode`),
  KEY `i_OrderLocationTime_ServiceTimePeriodID` (`ServiceTimePeriodID`),
  KEY `IX_OrderLocationTime_SiteNumber` (`SiteNumber`)
) ENGINE=InnoDB AUTO_INCREMENT=618185 DEFAULT CHARSET=latin1;



--- REFERENCE TABLES ---

-- Order Related Reference Tables
-- company table
-- reference from order.CompanyID
CREATE TABLE `company` (
  `CompanyID` int(11) NOT NULL AUTO_INCREMENT,
  `ParentCompanyID` int(11) DEFAULT NULL,
  `CompanyName` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `AccountPayableEmail` varchar(256) DEFAULT NULL,
  `MailingAttention` varchar(200) DEFAULT NULL,
  `MailingAddress1` varchar(200) NOT NULL,
  `MailingAddress2` varchar(200) DEFAULT NULL,
  `MailingCity` varchar(200) NOT NULL,
  `MailingStateCode` char(2) DEFAULT NULL,
  `MailingZip` varchar(10) NOT NULL,
  `BillingAttention` varchar(200) DEFAULT NULL,
  `BillingAddress1` varchar(200) NOT NULL,
  `BillingAddress2` varchar(200) DEFAULT NULL,
  `BillingCity` varchar(200) NOT NULL,
  `BillingStateCode` char(2) DEFAULT NULL,
  `BillingZip` varchar(10) NOT NULL,
  `BillingEmailAddress` varchar(50) DEFAULT NULL,
  `PhoneNumber` varchar(10) DEFAULT NULL,
  `FaxNumber` varchar(10) DEFAULT NULL,
  `DefaultPaymentTypeCode` varchar(10) NOT NULL,
  `Comments` longtext,
  `CompanyStatusCode` varchar(10) NOT NULL,
  `LegacyID` varchar(10) DEFAULT NULL,
  `CreatedByUserID` char(36) NOT NULL,
  `CreatedDateTime` datetime(3) NOT NULL,
  `ModifiedByUserID` char(36) NOT NULL,
  `updated_at` datetime DEFAULT NULL,
  `Latitude` float DEFAULT NULL,
  `Longitude` float DEFAULT NULL,
  PRIMARY KEY (`CompanyID`),
  KEY `company_CompanyName_IDX` (`CompanyName`) USING BTREE,
  KEY `company_ParentCompanyID_IDX` (`ParentCompanyID`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=2829 DEFAULT CHARSET=latin1;

-- contract table
-- reference from order.ContractID
CREATE TABLE `contract` (
  `ContractID` int(11) NOT NULL AUTO_INCREMENT,
  `CompanyID` int(11) NOT NULL,
  `ContractText` longtext NOT NULL,
  `ClientSignatureDate` datetime(3) DEFAULT NULL,
  `ClientSignatureUserID` char(36) DEFAULT NULL,
  `QCSignatureDate` datetime(3) DEFAULT NULL,
  `QCSignatureUserID` char(36) DEFAULT NULL,
  `ActivationDate` datetime(3) DEFAULT NULL,
  `DeactivationDate` datetime(3) DEFAULT NULL,
  `CreatedByUserID` char(36) NOT NULL,
  `CreatedDateTime` datetime(3) NOT NULL,
  `ModifiedByUserID` char(36) NOT NULL,
  `ModifiedDateTime` datetime(3) NOT NULL,
  `ContractName` varchar(100) NOT NULL,
  `PaymentTerms` varchar(50) DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`ContractID`)
) ENGINE=InnoDB AUTO_INCREMENT=209 DEFAULT CHARSET=latin1;

-- qc_office table
-- reference from order.QCOfficeID
CREATE TABLE `qcoffice` (
  `QCOfficeID` int(11) NOT NULL AUTO_INCREMENT,
  `QCOfficeTypeCode` varchar(10) NOT NULL,
  `OfficeName` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `StreetAddress1` varchar(200) NOT NULL,
  `StreetAddress2` varchar(200) DEFAULT NULL,
  `City` varchar(200) NOT NULL,
  `StateCode` char(2) NOT NULL,
  `ZipCode` varchar(10) NOT NULL,
  `PhoneNumber` varchar(10) DEFAULT NULL,
  `FaxNumber` varchar(10) DEFAULT NULL,
  `CellNumber` varchar(10) DEFAULT NULL,
  `TollFreeNumber` varchar(10) DEFAULT NULL,
  `AlternateNumber` varchar(10) DEFAULT NULL,
  `Latitude` decimal(11,8) DEFAULT NULL,
  `Longitude` decimal(11,8) DEFAULT NULL,
  `CreatedByUserID` char(36) NOT NULL,
  `CreatedDateTime` datetime(3) NOT NULL,
  `ModifiedByUserID` char(36) NOT NULL,
  `updated_at` datetime DEFAULT NULL,
  `QCOfficeCode` varchar(3) DEFAULT NULL,
  `Image` varchar(255) DEFAULT NULL,
  `Email` varchar(255) DEFAULT NULL,
  `Description` text,
  `ProjectExperience` text,
  `AreasServed` varchar(250) DEFAULT NULL,
  `ShowInWs` int(11) DEFAULT '1',
  `Status` int(11) DEFAULT '1',
  PRIMARY KEY (`QCOfficeID`)
) ENGINE=InnoDB AUTO_INCREMENT=40 DEFAULT CHARSET=latin1;

-- payment_term table
-- reference from order.PaymentTermID
CREATE TABLE `paymentterms` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `Concept` varchar(255) COLLATE utf8_unicode_ci NOT NULL,
  `Description` text COLLATE utf8_unicode_ci NOT NULL,
  `DaysUntilDue` smallint(6) NOT NULL DEFAULT '0',
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=13 DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- user table
-- reference from order.QCLeadUserID, order.CustomerLeadUserID
CREATE TABLE `user` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `UserID` varchar(255) COLLATE utf8_unicode_ci NOT NULL,
  `FirstName` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `LastName` varchar(200) CHARACTER SET utf8 DEFAULT NULL,
  `Position` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `Fax` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `Phone` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `Email` varchar(255) COLLATE utf8_unicode_ci NOT NULL,
  `Username` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `IsAnonymous` int(11) DEFAULT NULL,
  `Password` text COLLATE utf8_unicode_ci NOT NULL,
  `PasswordFormat` int(11) NOT NULL DEFAULT '0',
  `PasswordSalt` varchar(255) COLLATE utf8_unicode_ci NOT NULL,
  `PasswordQuestion` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `PasswordAnswer` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `IsApproved` tinyint(4) NOT NULL,
  `IsLockedOut` tinyint(4) NOT NULL,
  `LastLoginDate` date DEFAULT NULL,
  `LastPasswordChangedDate` date DEFAULT NULL,
  `LastLockoutDate` date DEFAULT NULL,
  `FailedPasswordAttemptCount` int(11) NOT NULL DEFAULT '0',
  `FailedPasswordAnswerAttemptCount` int(11) NOT NULL DEFAULT '0',
  `Photo` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
  `Description` text COLLATE utf8_unicode_ci,
  `Status` tinyint(1) NOT NULL DEFAULT '1',
  `ShowInWs` tinyint(4) DEFAULT '0',
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `NewPasswordRequest` int(11) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`,`UserID`),
  KEY `IX_UserID` (`UserID`),
  KEY `IX_IsApproved` (`IsApproved`)
) ENGINE=InnoDB AUTO_INCREMENT=8943 DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci;

-- project table
-- reference from order.ProjectID
CREATE TABLE `project` (
  `ProjectID` int(11) NOT NULL AUTO_INCREMENT,
  `CompanyID` int(11) NOT NULL,
  `ContractID` int(11) DEFAULT NULL,
  `ProjectName` varchar(100) NOT NULL,
  `CustomerOrderNumber` varchar(50) DEFAULT NULL,
  `QCLeadUserID` char(36) NOT NULL,
  `CustomerLeadUserID` char(36) DEFAULT NULL,
  `IsPublicData` tinyint(1) NOT NULL,
  `AllowThirdPartyAccess` tinyint(1) NOT NULL,
  `AllowResell` tinyint(1) NOT NULL,
  `DefaultPaymentCode` varchar(10) NOT NULL,
  `CreatedByUserID` char(36) NOT NULL,
  `CreatedDateTime` datetime(3) NOT NULL,
  `ModifiedByUserID` char(36) NOT NULL,
  `ModifiedDateTime` datetime(3) NOT NULL,
  PRIMARY KEY (`ProjectID`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- end of order related tables

-- Reference tables for orderservices
-- service table
-- reference from orderservice.ServiceID
CREATE TABLE `service` (
  `ServiceID` int(11) NOT NULL AUTO_INCREMENT,
  `ParentServiceID` int(11) DEFAULT NULL,
  `Name` varchar(50) NOT NULL,
  `Description` longtext NOT NULL,
  `IconImageID` int(11) DEFAULT NULL,
  `MainImageID` int(11) DEFAULT NULL,
  `CreatedByUserID` char(36) NOT NULL,
  `CreatedDateTime` datetime(3) NOT NULL,
  `ModifiedByUserID` char(36) NOT NULL,
  `ModifiedDateTime` datetime(3) NOT NULL,
  `IsHistorical` tinyint(1) NOT NULL DEFAULT '0',
  `IsActive` tinyint(1) DEFAULT NULL,
  `CheckListReportTypeCode` varchar(10) DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`ServiceID`)
) ENGINE=InnoDB AUTO_INCREMENT=103 DEFAULT CHARSET=latin1;

--- service table contents reference ---
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (33,NULL,'Survey','Various transportation data collection survey types are included in this category.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2021-03-19 00:00:00','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'S','2021-03-19 22:05:50'),
	 (34,NULL,'Tube Count','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2021-03-19 00:00:00','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'T','2021-03-19 22:00:41'),
	 (35,NULL,'Turning Movement Count','Manual Turning Movement Counts record vehicle turning movements at an intersection on 5-minute intervals over a 2-hour period. Pedestrian movements and heavy vehicle percentage are also recorded. The pedestrian movements will be recorded as the number of pedestrians crossing each intersection approach. The count request should identify the intersection, time period and day of the week customers wish the count to take place.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2021-03-19 00:00:00','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'V','2021-03-19 21:56:06'),
	 (36,33,'Custom Roadside Interview Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (37,33,'Land Use Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (38,33,'License Plate Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (39,33,'Origin - Destination Studies','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (40,33,'Parking Accumulation Counts','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (41,33,'Parking Lot Demand and Supply Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (42,33,'Pass-by/Internal/Diverted Trip Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50');
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (43,33,'Passenger Car Occupancy Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (44,33,'Pedestrian Volume Counts','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'S','2021-03-19 22:05:50'),
	 (45,33,'Radar Speed Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (46,33,'Road Inventory Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'S','2021-03-19 22:05:50'),
	 (47,33,'Saturation Flow Rate Studies','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (48,33,'Sign Inventory Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (49,33,'Speed Curve Indicator (Ball Bank) Survey','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','ED98C11A-E739-44D3-8172-C77122832D26','2015-09-22 05:28:43.990',0,0,'S','2021-03-19 22:05:50'),
	 (50,33,'Stop Sign Delay Studies','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (51,33,'Transit Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (52,33,'Travel Postcard Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50');
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (53,33,'Travel Time Surveys','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (54,33,'Vehicular Gap Studies','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (55,33,'Video Surveillance','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'S','2021-03-19 22:05:50'),
	 (56,33,'Bluetooth Survey','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'S','2021-03-19 22:05:50'),
	 (57,33,'Wavetronix Radar Survey','',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'S','2021-03-19 22:05:50'),
	 (58,34,'Volume','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,1,'T','2021-03-19 22:00:41'),
	 (59,34,'Volume, Class','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',1,1,'T','2021-03-19 22:00:41'),
	 (60,34,'Road Speed Survey','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'T','2021-03-19 22:00:41'),
	 (61,34,'Speed and Class','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',1,0,'T','2021-03-19 22:00:41'),
	 (62,34,'Volume, Speed','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',1,1,'T','2021-03-19 22:00:41');
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (63,34,'Volume, Speed, Class','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',1,1,'T','2021-03-19 22:00:41'),
	 (64,34,'Vehicle Classification','Road Tube Studies produce several types of information about the vehicles passing on the selected roadway. In addition to vehicle volume, tubes can be used to gather speed and vehicle classification (using the FHWA''s 13 types) information. Typical studies range from 24 to 48 hours, but may be requested for as long a period as needed. Data is summarized and delivered in Excel and PDF formats.',NULL,NULL,'1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390','1076A708-5CC7-4E3C-B21B-7A651D06B958','2015-07-10 17:34:55.390',0,0,'T','2021-03-19 22:00:41'),
	 (66,33,'Automated Video Count','Video ATR',NULL,NULL,'ED98C11A-E739-44D3-8172-C77122832D26','2015-09-22 05:28:43.987','ED98C11A-E739-44D3-8172-C77122832D26','2015-09-22 05:28:43.987',0,0,'S','2021-03-19 22:05:50'),
	 (67,33,'Mainline Manual Counts','',NULL,NULL,'','2019-03-07 00:00:00','','0000-00-00 00:00:00',0,0,'S','2021-03-19 22:05:50'),
	 (69,NULL,'Road Characteristic Inventory','Inventory of characteristics of assets in the roadway prism.',NULL,NULL,'','2019-11-19 00:00:00','','0000-00-00 00:00:00',0,1,NULL,'2019-11-19 14:24:57'),
	 (70,69,'Pavement Condition Inventory','Survey of pavement conditions on roadway.',NULL,NULL,'','2019-03-29 00:00:00','','0000-00-00 00:00:00',0,1,NULL,'2019-11-19 14:24:57'),
	 (71,69,'Signal Pole Inventory','Location and description of signal poles at intersections.',NULL,NULL,'','2019-03-29 00:00:00','','0000-00-00 00:00:00',0,1,'S','2019-11-19 14:24:57'),
	 (72,33,'Jim''s Test Subtype','',NULL,NULL,'','2019-03-29 00:00:00','','0000-00-00 00:00:00',0,0,'S','2021-03-19 22:05:50'),
	 (74,33,'Equipment Rental','',NULL,NULL,'','2019-11-20 00:00:00','','0000-00-00 00:00:00',0,1,'S','2021-03-19 22:05:50'),
	 (75,33,'Test SS','',NULL,NULL,'','2019-11-22 00:00:00','','0000-00-00 00:00:00',0,0,'S','2021-03-19 22:05:50');
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (76,35,'Turning Movement Count','',NULL,NULL,'','2020-12-15 00:00:00','','0000-00-00 00:00:00',0,0,'V','2021-03-19 21:56:06'),
	 (77,35,'Pedestrian Count','',NULL,NULL,'','2020-12-15 00:00:00','','0000-00-00 00:00:00',0,0,'V','2021-03-19 21:56:06'),
	 (78,35,'Queue Study','',NULL,NULL,'','2020-12-15 00:00:00','','0000-00-00 00:00:00',0,0,'V','2021-03-19 21:56:06'),
	 (79,33,'Other...','',NULL,NULL,'','2021-01-06 00:00:00','','0000-00-00 00:00:00',0,0,'S','2021-03-19 22:05:50'),
	 (80,33,'Custom...','',NULL,NULL,'','2021-01-06 00:00:00','','0000-00-00 00:00:00',0,0,'S','2021-03-19 22:05:50'),
	 (81,33,'Custom Video Survey...','',NULL,NULL,'','2021-01-06 00:00:00','','0000-00-00 00:00:00',0,1,'S','2021-03-19 22:05:50'),
	 (82,33,'Custom Non-Video Survey...','',NULL,NULL,'','2021-01-06 00:00:00','','0000-00-00 00:00:00',0,1,'S','2021-03-19 22:05:50'),
	 (84,34,'Radar - Volume','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'T',NULL),
	 (85,34,'Radar - Volume, Class','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'T',NULL),
	 (86,34,'Radar - Volume, Speed','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'T',NULL);
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (87,34,'Radar - Volume, Speed, Class','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'T',NULL),
	 (88,34,'Video ATR - Volume','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'T',NULL),
	 (89,33,'Support Services','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (90,33,'Historical Data','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (91,33,'Interview Survey','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (92,33,'License Plate O-D Study','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (93,33,'Occupancy Survey','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (94,33,'Queue Study','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (95,33,'Handheld Radar Survey','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (96,33,'Saturation Flow Rate Study','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL);
INSERT INTO qcapp_stg.service (ServiceID,ParentServiceID,Name,Description,IconImageID,MainImageID,CreatedByUserID,CreatedDateTime,ModifiedByUserID,ModifiedDateTime,IsHistorical,IsActive,CheckListReportTypeCode,updated_at) VALUES
	 (97,33,'Horizontal Curve Advisory Speed Survey','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (98,33,'Delay Study','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (99,33,'Transit Survey','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (100,33,'Floating Car Travel Time Survey','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (101,33,'Vehicular Gap Study (Video)','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL),
	 (102,33,'Parking Study','',NULL,NULL,'','2021-03-19 00:00:00','','0000-00-00 00:00:00',0,1,'S',NULL);

-- commentfororderlocation table
-- reference from commentfororderlocation.OrderLocationID
CREATE TABLE `commentfororderlocation` (
  `CommentForOrderLocationID` int(11) NOT NULL AUTO_INCREMENT,
  `OrderLocationID` int(11) NOT NULL,
  `CommentText` longtext NOT NULL,
  `CommentDate` datetime(3) NOT NULL,
  `CommentByUserID` char(36) NOT NULL,
  `IsPublic` tinyint(1) NOT NULL,
  `ShowOnInvoice` tinyint(1) NOT NULL,
  PRIMARY KEY (`CommentForOrderLocationID`)
) ENGINE=InnoDB AUTO_INCREMENT=87802 DEFAULT CHARSET=latin1;

-- end of orderservice related tables