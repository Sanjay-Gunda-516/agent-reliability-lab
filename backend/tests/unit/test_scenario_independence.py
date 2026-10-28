"""Architecture check: ground truth and grading must not be derived from the agent's rule code."""

import ast
from pathlib import Path

import pytest

import app.evaluation
import app.scenarios

FORBIDDEN = ("app.domain.rules", "app.domain.policies", "app.tools.orders")


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


@pytest.mark.parametrize("package", [app.scenarios, app.evaluation], ids=lambda p: p.__name__)
def test_package_never_imports_business_rules(package: object) -> None:
    package_dir = Path(package.__file__).parent  # type: ignore[attr-defined]
    offenders = {
        f"{path.name}: {module}"
        for path in package_dir.glob("*.py")
        for module in imported_modules(path)
        if module.startswith(FORBIDDEN)
    }

    assert offenders == set()
