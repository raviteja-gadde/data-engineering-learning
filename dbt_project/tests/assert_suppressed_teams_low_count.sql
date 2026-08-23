-- Custom test: Validate suppression logic.
-- Teams with respondent_count < 4 must be marked is_suppressed = true.
-- This query returns FAILING rows -- if it returns zero rows, the test passes.

select
    team_id,
    respondent_count,
    is_suppressed
from {{ ref('suppression_flags') }}
where respondent_count < 4
  and is_suppressed = false
