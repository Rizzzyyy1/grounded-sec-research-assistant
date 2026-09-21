"""Static checks on the deployment files (Docker itself is not required to run these)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "docker" / "Dockerfile").read_text()
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def stage(name: str) -> str:
    match = re.search(rf"FROM \S+ AS {name}\n(.*?)(?=\nFROM |\Z)", DOCKERFILE, re.S)
    assert match, f"stage {name} not found"
    return match.group(1)


def test_images_run_as_a_non_root_user_and_have_health_checks() -> None:
    for name in ("api", "ui"):
        body = stage(name)
        assert "USER finsight" in body, f"{name} would run as root"
        assert "HEALTHCHECK" in body and "EXPOSE" in body
    assert "useradd" in DOCKERFILE and "--uid 10001" in DOCKERFILE


def test_api_image_installs_only_what_it_needs() -> None:
    assert '".[data,ml,llm,api]"' in stage("api")
    assert "streamlit" not in stage("api") and '".[ui]"' in stage("ui")
    assert (
        "ml" not in stage("ui").split("pip install")[1].split("\n")[0]
    )  # the UI never loads models


def test_compose_wires_ui_to_api_to_qdrant_with_loopback_only_ports() -> None:
    services = COMPOSE["services"]
    assert set(services) == {"qdrant", "api", "ui"}
    assert services["api"]["environment"]["FINSIGHT_QDRANT_URL"] == "http://qdrant:6333"
    assert services["ui"]["environment"]["FINSIGHT_API_URL"] == "http://api:8000"
    assert services["api"]["depends_on"] == ["qdrant"] and services["ui"]["depends_on"] == ["api"]
    for name, svc in services.items():
        for port in svc["ports"]:
            assert port.startswith("127.0.0.1:"), f"{name} exposes {port} beyond loopback"
    assert "qdrant_data" in COMPOSE["volumes"]


def test_compose_targets_exist_in_the_dockerfile_and_secrets_are_not_baked_in() -> None:
    for name in ("api", "ui"):
        assert COMPOSE["services"][name]["build"]["target"] == name
        assert re.search(rf"FROM \S+ AS {name}\b", DOCKERFILE)
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in dockerignore and "data/" in dockerignore
    assert (
        "ANTHROPIC_API_KEY" not in DOCKERFILE
        and "ANTHROPIC_API_KEY"
        not in (ROOT / "docker-compose.yml").read_text().split("environment")[1]
    )
    assert "api_key" not in DOCKERFILE.lower()


def test_qdrant_url_setting_exists_and_defaults_to_embedded() -> None:
    from finsight.config.settings import Settings  # noqa: PLC0415

    assert Settings().qdrant_url is None
    assert Settings(qdrant_url="http://qdrant:6333").qdrant_url == "http://qdrant:6333"
