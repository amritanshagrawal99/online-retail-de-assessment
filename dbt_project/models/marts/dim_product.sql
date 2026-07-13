-- One row per distinct stock_code (products + service codes, flagged).

with base as (
    select * from {{ ref('stg_online_retail') }}
),
desc_counts as (
    select stock_code, description, count(*) as n
    from base
    where description <> 'UNKNOWN'
    group by stock_code, description
),
canonical_desc as (
    select stock_code, description
    from desc_counts
    qualify row_number() over (
        partition by stock_code order by n desc, description
    ) = 1
),
agg as (
    select
        stock_code,
        max(is_service_line) as is_service_line,
        min(invoice_date)    as first_seen_date,
        max(invoice_date)    as last_seen_date
    from base
    group by stock_code
)

select
    to_hex(md5(a.stock_code))            as product_sk,
    a.stock_code,
    coalesce(cd.description, 'UNKNOWN')  as description,
    a.is_service_line,
    a.first_seen_date,
    a.last_seen_date
from agg a
left join canonical_desc cd using (stock_code)
