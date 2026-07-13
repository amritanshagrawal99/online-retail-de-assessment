-- Invoice-grain fact (bonus). One row per invoice.

{{ config(
    partition_by={'field': 'invoice_date', 'data_type': 'date'},
    cluster_by=['customer_sk']
) }}

select
    invoice_no,
    max(case when has_customer
             then to_hex(md5(cast(customer_id as string)))
             else null end)      as customer_sk,
    min(invoice_date)            as invoice_date,
    min(invoice_ts)              as invoice_ts,
    any_value(country)           as country,
    count(*)                     as n_lines,
    sum(quantity)                as n_units,
    sum(line_revenue)            as order_revenue,
    max(is_cancellation)         as is_cancellation
from {{ ref('stg_online_retail') }}
group by invoice_no
