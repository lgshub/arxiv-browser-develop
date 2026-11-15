import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "browser_config.yaml"


@dataclass(frozen=True)
class BrowserConfig:
    host: str = "127.0.0.1"
    port: int = 0
    default_mode: str = "count"
    default_limit: int = 1000
    max_limit: int = 5000
    default_recent_days: int = 7
    max_recent_days: int = 3650
    default_category: str = "cs.RO"
    include_abstract: bool = False
    api_page_size: int = 100
    request_interval_seconds: float = 3
    retries: int = 3
    connect_timeout_seconds: float = 10
    read_timeout_seconds: float = 45
    papers_per_page: int = 40
    sort_by_author_count: bool = False
    directory: Path = PROJECT_ROOT / "data" / "searches"


SECTIONS = {
    "server": {"host", "port"},
    "search": {
        "default_mode",
        "default_limit",
        "max_limit",
        "default_recent_days",
        "max_recent_days",
        "default_category",
        "include_abstract",
        "api_page_size",
        "request_interval_seconds",
        "retries",
        "connect_timeout_seconds",
        "read_timeout_seconds",
    },
    "display": {"papers_per_page", "sort_by_author_count"},
    "storage": {"directory"},
}


def load_config(path=DEFAULT_CONFIG_PATH):
    path = Path(path).resolve()
    try:
        with path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {path}: {error}") from error
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("The browser configuration must be a YAML mapping.")
    values = {}
    for section, settings in data.items():
        if section not in SECTIONS or not isinstance(settings, dict):
            raise ValueError(f"Unknown or invalid configuration section: {section}")
        for key, value in settings.items():
            if key not in SECTIONS[section]:
                raise ValueError(f"Unknown configuration setting: {section}.{key}")
            values[key] = value
    config = replace(BrowserConfig(), **values)
    if config.default_mode not in ("count", "days"):
        raise ValueError("default_mode must be count or days.")
    for key in ("host", "default_category"):
        if not isinstance(getattr(config, key), str) or not getattr(config, key).strip():
            raise ValueError(f"{key} must be a nonempty string.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)?", config.default_category):
        raise ValueError("default_category must be an arXiv category code, such as cs.RO.")
    for key, minimum, maximum in (
        ("port", 0, 65535),
        ("default_limit", 1, 300000),
        ("max_limit", 1, 300000),
        ("default_recent_days", 1, 36500),
        ("max_recent_days", 1, 36500),
        ("api_page_size", 1, 2000),
        ("retries", 0, 100),
        ("papers_per_page", 1, 1000),
    ):
        value = getattr(config, key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be an integer from {minimum} to {maximum}.")
    if config.default_limit > config.max_limit:
        raise ValueError("default_limit cannot exceed max_limit.")
    if config.default_recent_days > config.max_recent_days:
        raise ValueError("default_recent_days cannot exceed max_recent_days.")
    for key in ("include_abstract", "sort_by_author_count"):
        if type(getattr(config, key)) is not bool:
            raise ValueError(f"{key} must be true or false.")
    for key in ("request_interval_seconds", "connect_timeout_seconds", "read_timeout_seconds"):
        value = getattr(config, key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive number.")
    directory = config.directory
    if not isinstance(directory, (str, Path)) or not str(directory).strip():
        raise ValueError("storage.directory must be a nonempty path.")
    directory = Path(directory).expanduser()
    if not directory.is_absolute():
        directory = path.parent / directory
    return replace(config, directory=directory.resolve())
