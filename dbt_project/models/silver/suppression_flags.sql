-- Silver: Compute suppression per team.
-- Teams with fewer than 4 respondents are suppressed to protect anonymity.

with respondent_counts as (
    select
        team_id,
        count(distinct respondent_id) as respondent_count
    from {{ ref('survey_responses_cleaned') }}
    group by team_id
)

select
    team_id,
    respondent_count,
    respondent_count < 4 as is_suppressed
from respondent_counts
