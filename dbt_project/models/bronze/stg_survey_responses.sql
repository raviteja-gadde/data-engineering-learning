-- Bronze: Stage raw survey responses with basic type casting.
-- No dedup, no business rules -- just make the types usable.

select
    response_id,
    respondent_id,
    team_id,
    project_id,
    question_id,
    cast(score as integer)       as score,
    cast(submitted_at as timestamp) as submitted_at
from {{ ref('raw_survey_responses') }}
