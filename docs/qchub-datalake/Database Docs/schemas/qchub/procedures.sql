CREATE DEFINER=`qcadmin_db18`@`%` PROCEDURE `qcapp_stg`.`ListTubeCountOrdersBySiteNumber`(IN SNumber int)
BEGIN
	
    SELECT DISTINCT 
		tc.QCOrderNo OrderID
		,tc.SiteNumber
		,tc.Location
		,tc.Upstream
		,tc.Downstream
		,tc.SpecificLocation
		,tc.City
		,tc.State
		,tc.StartDate
		,tc.EndDate
		,I1.IntervalType
		,(SELECT fnConcatDirections(tc.SiteNumber, 'Volume')) AS VolumeDirection
		,(SELECT fnConcatDirections(tc.SiteNumber, 'Speed')) AS SpeedDirection
		,(SELECT fnConcatDirections(tc.SiteNumber, 'Class')) AS VehicleClassDirection
	FROM legacytubecounts tc
	INNER JOIN 
	(
		SELECT 
			SiteNumber
			,IntervalType 
		FROM legacytubecountsdatamain
		WHERE SiteNumber IN (SNumber)
		GROUP BY SiteNumber, IntervalType
	) I1 ON tc.SiteNumber = I1.SiteNumber
	WHERE tc.SiteNumber IN (SNumber)
	ORDER BY tc.QCOrderNo DESC, tc.SiteNumber;
END

