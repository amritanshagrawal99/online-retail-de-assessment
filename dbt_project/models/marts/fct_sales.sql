-- Line-grain sales fact. Keeps all lines, flagged, with FKs to the dimensions.

{{ config(
    partition_by={'field': 'invoice_date', 'data_type': 'date'},
    cluster_by=['customer_sk', 'product_sk']
) }}

select
    s.line_sk,
    s.invoice_no,
    case when s.has_customer
         then to_hex(md5(cast(s.customer_id as string)))
         else null end             as customer_sk,   -- FK -> dim_customer
    to_hex(md5(s.stock_code))      as product_sk,     -- FK -> dim_product
    s.invoice_date,
    s.invoice_ts,
    s.quantity,
    s.unit_price,
    s.line_revenue,
    s.is_cancellation,
    s.is_service_line,
    s.has_customer
from {{ ref('stg_online_retail') }} s
