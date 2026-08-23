-- Bronze: Stage team hierarchy reference data.

select
    team_id,
    team_name,
    department,
    division
from {{ ref('raw_team_hierarchy') }}
