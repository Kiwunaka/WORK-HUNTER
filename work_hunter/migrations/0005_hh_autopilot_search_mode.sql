ALTER TABLE hh_autopilot_search_cycles
ADD COLUMN mode TEXT NOT NULL DEFAULT 'live'
CHECK (
    typeof(mode) = 'text'
    AND mode IN ('live', 'shadow')
);

UPDATE hh_autopilot_search_cycles
SET mode = CASE
    WHEN (
        SELECT run.trigger
        FROM hh_autopilot_runs AS run
        WHERE run.id = hh_autopilot_search_cycles.origin_run_id
    ) = 'shadow'
    THEN 'shadow'
    ELSE 'live'
END;
