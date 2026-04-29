from importlib import import_module

import playlist_porter


def test_package_exposes_version_string() -> None:
    assert isinstance(playlist_porter.__version__, str)
    assert playlist_porter.__version__


def test_planned_runtime_dependencies_import() -> None:
    modules = [
        "loguru",
        "opencc",
        "pydantic",
        "qqmusic_api",
        "rapidfuzz",
        "requests",
        "requests_ratelimiter",
        "rich",
        "spotipy",
        "sqlalchemy",
        "tenacity",
    ]

    for module in modules:
        assert import_module(module)


def test_planned_dev_dependencies_import() -> None:
    modules = [
        "pytest",
        "pytest_cov",
        "ruff",
        "vcr",
    ]

    for module in modules:
        assert import_module(module)
