-- Silver: Team dimension with hierarchy context.
-- In a production system this would track changes over time (SCD Type 2).
-- For now, it passes through the current snapshot from the seed.

select
    team_id,
    team_name,
    department,
    division,
    department || ' > ' || team_name as full_path
from {{ ref('stg_team_hierarchy') }}
