"""Charts over the same scoped, priced facts and snapshot as the record list."""

from .owner_statistics import amount_status, serialize_row, unit_name


def chart_payload(report):
    filters = report.filters
    ranking = filters["chart"] == "ranking"
    quantity = filters["metric"] == "quantity"
    where, params = report.where()
    where += " AND category IN ('inbound','outbound')"
    direction = filters.get("category") or filters["direction"]
    if ranking and direction == "both":
        direction = "outbound"
    if direction != "both":
        where += " AND category = %s"
        params.append(direction)

    column = "base_unit" if quantity else "currency"
    options, dimension = chart_dimensions(report, column, where, params)
    payload = {
        **report.envelope(),
        "options": options,
        "dimension": dimension,
        "results": [],
        "next_page": None,
        "excluded_count": 0,
    }
    if not dimension:
        return payload
    where += f" AND {column} = %s"
    params.append(dimension["code"])
    value = "SUM(ABS(qty_delta))" if quantity else "SUM(amount)"
    measures = (
        f"{value} AS value, COUNT(amount) AS priced_count, SUM(amount IS NULL) AS unpriced_count"
    )
    if ranking:
        rows, payload["excluded_count"] = rank_products(report, where, params, measures, direction)
    else:
        rows, payload["next_page"] = trend_periods(report, where, params, measures, filters["page"])
    for row in rows:
        row["amount_status"] = amount_status(row)
        payload["results"].append(serialize_row(row))
    return payload


def chart_dimensions(report, column, where, params):
    quantity = column == "base_unit"
    label = "MIN(base_unit_name)" if quantity else "currency"
    options = report.query(
        f"SELECT {column} AS code, {label} AS label FROM statistics_facts "
        f"WHERE {where} GROUP BY {column} ORDER BY COUNT(DISTINCT product_id) DESC, {column}",
        params,
    )
    for item in options:
        item["key"] = f"{column}:{item['code']}"
        if quantity:
            item["label"] = unit_name(item["label"])
    dimension = next((o for o in options if o["key"] == report.filters.get("dimension")), None)
    dimension = (
        dimension
        or next((o for o in options if not quantity and o["code"] == "CNY"), None)
        or next(iter(options), None)
    )
    return options, dimension


def rank_products(report, where, params, measures, direction):
    rows = report.query(
        f", ranked AS (SELECT product_id, MIN(product_name) AS product_name, {measures} "
        f"FROM statistics_facts WHERE {where} GROUP BY product_id) "
        "SELECT ranked.*, SUM(value IS NULL) OVER () AS excluded_count FROM ranked "
        "ORDER BY value DESC, product_id LIMIT 10",
        params,
    )
    excluded = int(rows[0]["excluded_count"]) if rows else 0
    rows = [r for r in rows if r["value"] is not None]
    for row in rows:
        row["category"] = direction
        del row["excluded_count"]
    return rows, excluded


def trend_periods(report, where, params, measures, page):
    # Page whole periods, never split the two series at a page boundary.
    group = report.group_expression("periods")
    rows = report.query(
        f", series AS (SELECT {group} AS group_key, category, {measures} "
        f"FROM statistics_facts WHERE {where} AND posted_at IS NOT NULL "
        f"GROUP BY {group}, category), numbered AS (SELECT series.*, "
        "DENSE_RANK() OVER (ORDER BY group_key DESC) AS pos FROM series) "
        "SELECT * FROM numbered WHERE pos BETWEEN %s AND %s ORDER BY group_key, category",
        [*params, (page - 1) * 12 + 1, page * 12 + 1],
    )
    last = page * 12
    next_page = page + 1 if any(row["pos"] > last for row in rows) else None
    rows = [row for row in rows if row.pop("pos") <= last]
    for row in rows:
        report.period_bounds(row)
    return rows, next_page
