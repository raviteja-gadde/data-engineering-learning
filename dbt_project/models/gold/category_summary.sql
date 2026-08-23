-- Gold: Organization-wide category-level aggregations.
-- One row per question category with overall mean and spread.

select
    category,
    round(avg(score), 2)    as mean_score,
    round(min(score), 2)    as min_score,
    round(max(score), 2)    as max_score,
    count(*)                as total_responses,
    count(distinct team_id) as teams_represented
from {{ ref('survey_responses_cleaned') }}
group by category
