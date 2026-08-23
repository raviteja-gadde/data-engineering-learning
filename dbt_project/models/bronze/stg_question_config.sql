-- Bronze: Stage question configuration with typed boolean.

select
    question_id,
    question_text,
    category,
    cast(is_reverse_scored as boolean) as is_reverse_scored
from {{ ref('raw_question_config') }}
