-- Staging: type, rename, flag, de-duplicate (line grain preserved).
-- Mirrors sql/02_load_transform.sql, sourced via dbt ref/source.

with source as (
    select * from {{ source('online_retail', 'raw_online_retail') }}
),

-- Remove exact-duplicate lines (12,133 in the source).
deduped as (
    select *
    from source
    qualify row_number() over (
        partition by _record_hash
        order by _source_sheet, _source_row
    ) = 1
),

typed as (
    select
        _record_hash                                            as line_sk,
        invoice                                                 as invoice_no,
        stock_code,
        coalesce(nullif(trim(description), ''), 'UNKNOWN')      as description,
        quantity,
        cast(price as numeric)                                  as unit_price,
        cast(quantity * price as numeric)                       as line_revenue,
        invoice_date                                            as invoice_ts,
        date(invoice_date)                                      as invoice_date,
        safe_cast(customer_id as int64)                         as customer_id,
        coalesce(nullif(trim(country), ''), 'Unspecified')      as country,
        starts_with(invoice, 'C')                               as is_cancellation,
        (quantity <= 0)                                         as is_return_or_zero,
        (price <= 0)                                            as is_nonpositive_price,
        (safe_cast(substr(stock_code, 1, 5) as int64) is null)  as is_service_line,
        (customer_id is not null)                               as has_customer,
        _source_file                                            as source_file,
        _loaded_at                                              as loaded_at
    from deduped
)

select
    *,
    (not is_cancellation
     and not is_return_or_zero
     and not is_nonpositive_price
     and not is_service_line
     and has_customer)                                          as is_valid_sale
from typed
