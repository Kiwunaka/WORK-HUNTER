ALTER TABLE hh_autopilot_search_cycles
ADD COLUMN distinct_vacancy_cap INTEGER
CHECK (
    distinct_vacancy_cap IS NULL
    OR (
        typeof(distinct_vacancy_cap) = 'integer'
        AND distinct_vacancy_cap >= 0
    )
);
