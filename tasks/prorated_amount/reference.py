def prorated_amount(monthly_price_cents, active_days, days_in_month):
    if not 28 <= days_in_month <= 31 or active_days < 0 or active_days > days_in_month:
        raise ValueError("invalid days")
    return (2 * monthly_price_cents * active_days + days_in_month) // (2 * days_in_month)
