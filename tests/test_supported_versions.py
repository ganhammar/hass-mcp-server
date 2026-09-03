"""The declared minimum Home Assistant version has to be one CI actually builds.

A floor in hacs.json is a promise to users on that release. Nothing enforces it at
install time beyond HACS refusing older versions, so if the matrix stops covering
it the promise is untested and breaks on someone's instance rather than here.
"""

import json
import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _matrix_ha_versions() -> list[str]:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/main.yml").read_text())
    legs = workflow["jobs"]["integration-test"]["strategy"]["matrix"]["include"]
    return [leg["ha-version"] for leg in legs]


def test_declared_minimum_is_the_oldest_version_tested():
    """hacs.json's floor and the oldest integration leg must be the same release.

    Raise both together, or add a matrix leg for the older one. The current floor
    is 2025.6.0 because recorder.get_statistics, which tools/statistics.py reads
    through, landed in that release and raises ServiceNotFound before it.
    """
    declared = json.loads((REPO_ROOT / "hacs.json").read_text())["homeassistant"]
    oldest = min(_matrix_ha_versions(), key=_version)

    assert _version(declared) == _version(oldest), (
        f"hacs.json declares {declared} as the minimum but the oldest tested "
        f"version is {oldest}, so nothing verifies the version we claim to support"
    )
