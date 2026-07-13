-- One row per known customer. Identity covers every customer_id (so fct FKs
-- never orphan); value metrics use product lines only, returns netted.

with customer_lines as (
    select * from {{ ref('stg_online_retail') }} where has_customer
),
ids as (
    select distinct customer_id from customer_lines
),
country_counts as (
    select customer_id, country, count(*) as n
    from customer_lines
    group by customer_id, country
),
primary_country as (
    select customer_id, country as primary_country
    from country_counts
    qualify row_number() over (
        partition by customer_id order by n desc, country
    ) = 1
),
product_lines as (
    select * from customer_lines where not is_service_line
),
value_agg as (
    select
        customer_id,
        min(invoice_date)          as first_purchase_date,
        max(invoice_date)          as last_purchase_date,
        count(distinct invoice_no) as lifetime_orders,
        sum(line_revenue)          as lifetime_net_revenue
    from product_lines
    group by customer_id
)

select
    to_hex(md5(cast(ids.customer_id as string))) as customer_sk,
    ids.customer_id,
    pc.primary_country,
    v.first_purchase_date,
    v.last_purchase_date,
    coalesce(v.lifetime_orders, 0)               as lifetime_orders,
    coalesce(v.lifetime_net_revenue, 0)          as lifetime_net_revenue
from ids
join primary_country pc using (customer_id)
left join value_agg v using (customer_id)
