from datetime import datetime, timedelta, timezone

from .query_language import compile_query


def build_search_options(data, config, now=None):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    for name in ("keywords", "field", "expression"):
        if name in data and not isinstance(data[name], str):
            raise ValueError(f"{name} must be a string.")
    mode = data.get("mode", config.default_mode)
    if mode not in ("count", "days"):
        raise ValueError("Search mode must be count or days.")
    include = data.get("include_abstract", config.include_abstract)
    if not isinstance(include, bool):
        raise ValueError("include_abstract must be true or false.")
    result = {
        "keywords": data.get("keywords", "").strip(),
        "field": data.get("field", "all"),
        "expression": data.get("expression", "").strip(),
        "mode": mode,
        "include_abstract": include,
        "limit": None,
        "recent_days": None,
        "date_start": None,
        "date_end": None,
    }
    date_query = None
    if mode == "count":
        limit = data.get("limit", config.default_limit)
        if type(limit) is not int or not 1 <= limit <= config.max_limit:
            raise ValueError(f"Paper limit must be an integer from 1 to {config.max_limit:,}.")
        result["limit"] = limit
    else:
        days = data.get("recent_days", config.default_recent_days)
        if type(days) is not int or not 1 <= days <= config.max_recent_days:
            raise ValueError(
                f"Recent days must be an integer from 1 to {config.max_recent_days:,}."
            )
        end = (
            (now or datetime.now(timezone.utc))
            .astimezone(timezone.utc)
            .replace(second=0, microsecond=0)
        )
        start = end - timedelta(days=days)
        result.update(recent_days=days, date_start=start.isoformat(), date_end=end.isoformat())
        date_query = f"submittedDate:[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]"
    result["query"] = compile_query(
        result["keywords"], result["field"], result["expression"], anchor=date_query
    )
    return result
