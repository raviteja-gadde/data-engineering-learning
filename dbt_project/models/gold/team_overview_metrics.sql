-- Gold: Mean score per team per question category.
-- Joins suppression flags so consumers know which teams can be reported.

select
    r.team_id,
    t.team_name,
    t.department,
    r.category,
    round(avg(r.score), 2)       as mean_score,
    count(*)                     as response_count,
    count(distinct r.respondent_id) as respondent_count,
    s.is_suppressed
from {{ ref('survey_responses_cleaned') }} r
join {{ ref('team_hierarchy_scd') }}       t using (team_id)
join {{ ref('suppression_flags') }}        s using (team_id)
group by
    r.team_id, t.team_name, t.department,
    r.category, s.is_suppressed
