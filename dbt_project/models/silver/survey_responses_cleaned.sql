-- Silver: Deduplicated, typed, reverse-scale-corrected survey responses.
--
-- Three things happen here:
-- 1. Dedup: ROW_NUMBER picks the first occurrence of each response_id
-- 2. Reverse-scale: Q06 is reverse-scored, so raw 5 -> corrected 1 (6 - score)
-- 3. Validation: only scores 1-5 survive

with deduped as (
    select
        *,
        row_number() over (
            partition by response_id
            order by submitted_at desc
        ) as row_num
    from {{ ref('stg_survey_responses') }}
),

unique_responses as (
    select
        r.response_id,
        r.respondent_id,
        r.team_id,
        r.project_id,
        r.question_id,
        r.score       as raw_score,
        r.submitted_at,
        q.is_reverse_scored,
        q.category,
        case
            when q.is_reverse_scored then 6 - r.score
            else r.score
        end as score
    from deduped r
    join {{ ref('stg_question_config') }} q using (question_id)
    where r.row_num = 1
      and r.score between 1 and 5
)

select
    response_id,
    respondent_id,
    team_id,
    project_id,
    question_id,
    category,
    raw_score,
    score,
    is_reverse_scored,
    submitted_at
from unique_responses
