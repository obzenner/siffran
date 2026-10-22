"""Passive, injectable host-environment readiness for the Docker sandbox."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import signal
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from core.freshness import canonical_digest

ENVIRONMENT_SCHEMA = "siffran.empirica.sandbox-environment/1"
INTERNAL_IMPORTS = ("adapters.bridge", "application.protocol", "application.run_state")
CAPABILITY_KEYS = {
    "fcntl", "resource", "o_nofollow", "getuid", "setsid", "setpgid", "killpg",
    "sigterm", "sigkill",
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EMPIRICA_ROOT = Path(__file__).resolve().parents[2]
PROBE_POLICY = {"timeout_seconds": 5.0, "output_limit_bytes": 64 * 1024}
EXPECTED_IMPORT_ORIGINS = {
    "adapters.bridge": str((_EMPIRICA_ROOT / "adapters" / "bridge.py").resolve(strict=True)),
    "application.protocol": str((_EMPIRICA_ROOT / "application" / "protocol.py").resolve(strict=True)),
    "application.run_state": str((_EMPIRICA_ROOT / "application" / "run_state.py").resolve(strict=True)),
}


class EnvironmentPreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnvironmentIdentity:
    schema: str
    available: bool
    reasons: tuple[str, ...]
    python: dict[str, Any]
    capabilities: dict[str, bool]
    dependencies: dict[str, Any]
    import_smoke: tuple[dict[str, str], ...]
    optional_tools: dict[str, Any]
    probe_policy: dict[str, Any]

    @property
    def digest(self) -> str:
        return canonical_digest(asdict(self))


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _origin(module: Any) -> str:
    raw = getattr(module, "__file__", None)
    if not isinstance(raw, str) or not raw:
        raise EnvironmentPreflightError("module has no filesystem origin")
    path = Path(raw).resolve(strict=True)
    if not stat.S_ISREG(path.stat().st_mode):
        raise EnvironmentPreflightError("module origin is not a regular file")
    return str(path)


def _system_python() -> dict[str, Any]:
    executable = Path(sys.executable).resolve(strict=True)
    return {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "version_info": list(sys.version_info[:3]),
        "cache_tag": sys.implementation.cache_tag,
        "executable": str(executable),
        "executable_sha256": _file_digest(executable),
        "os_name": os.name,
        "host_os": platform.system(),
        "host_arch": platform.machine(),
    }


def _system_capabilities() -> dict[str, bool]:
    def importable(name: str) -> bool:
        try:
            importlib.import_module(name)
            return True
        except Exception:
            return False
    return {
        "fcntl": importable("fcntl"),
        "resource": importable("resource"),
        "o_nofollow": bool(getattr(os, "O_NOFOLLOW", 0)),
        "getuid": callable(getattr(os, "getuid", None)),
        "setsid": callable(getattr(os, "setsid", None)),
        "setpgid": callable(getattr(os, "setpgid", None)),
        "killpg": callable(getattr(os, "killpg", None)),
        "sigterm": hasattr(signal, "SIGTERM"),
        "sigkill": hasattr(signal, "SIGKILL"),
    }


def _inprocess_import_probe(
    importer: Callable[[str], Any], distribution_version: Callable[[str], str],
) -> dict[str, Any]:
    jsonschema = importer("jsonschema")
    validator_for = getattr(getattr(jsonschema, "validators", None), "validator_for", None)
    draft = getattr(jsonschema, "Draft202012Validator", None)
    return {
        "jsonschema": {
            "version": distribution_version("jsonschema"), "module_origin": _origin(jsonschema),
            "draft202012": isinstance(draft, type), "validator_for": callable(validator_for),
        },
        "imports": [{"module": name, "origin": _origin(importer(name))}
                    for name in INTERNAL_IMPORTS],
    }


def _bounded_import_probe(python_executable: str) -> dict[str, Any]:
    from adapters.sandbox.process import BoundedRunner
    script = (
        "import importlib,importlib.metadata,json,sys;"
        f"sys.path.insert(0,{str(_EMPIRICA_ROOT)!r});"
        "sys.addaudithook(lambda e,a:(_ for _ in ()).throw(PermissionError('network denied')) "
        "if e.startswith('socket.') else None);"
        "j=importlib.import_module('jsonschema');"
        "mods=['adapters.bridge','application.protocol','application.run_state'];"
        "print(json.dumps({'jsonschema':{'version':importlib.metadata.version('jsonschema'),"
        "'module_origin':j.__file__,'draft202012':isinstance(getattr(j,'Draft202012Validator',None),type),"
        "'validator_for':callable(getattr(getattr(j,'validators',None),'validator_for',None))},"
        "'imports':[{'module':n,'origin':importlib.import_module(n).__file__} for n in mods]}))"
    )
    result = BoundedRunner().run(
        (python_executable, "-I", "-c", script),
        timeout_seconds=PROBE_POLICY["timeout_seconds"],
        output_limit_bytes=PROBE_POLICY["output_limit_bytes"],
        env={"LANG": "C", "LC_ALL": "C", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    if result.timed_out:
        raise EnvironmentPreflightError("bounded import probe timed out")
    if result.output_limited:
        raise EnvironmentPreflightError("bounded import probe exceeded its output limit")
    if result.returncode != 0:
        raise EnvironmentPreflightError("bounded import probe child failed")
    try:
        value = json.loads(result.stdout.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentPreflightError("bounded import probe returned malformed JSON") from exc
    if not isinstance(value, Mapping) or set(value) != {"jsonschema", "imports"}:
        raise EnvironmentPreflightError("bounded import probe returned a malformed document")
    return dict(value)


def inspect_environment(
    *, python_facts: Mapping[str, Any] | None = None,
    capability_facts: Mapping[str, bool] | None = None,
    importer: Callable[[str], Any] = importlib.import_module,
    distribution_version: Callable[[str], str] = importlib.metadata.version,
    child_probe: Callable[[str], Mapping[str, Any]] | None = None,
) -> EnvironmentIdentity:
    """Inspect only local files/imports/version facts; never install, write, start, or use network."""
    reasons: list[str] = []
    try:
        python = dict(_system_python() if python_facts is None else python_facts)
    except Exception:
        python = {}
        reasons.append("canonical Python executable identity is unavailable")
    try:
        capabilities = dict(_system_capabilities() if capability_facts is None else capability_facts)
    except Exception:
        capabilities = {}
        reasons.append("required POSIX capability inspection failed")
    expected_python_keys = {
        "implementation", "version", "version_info", "cache_tag", "executable",
        "executable_sha256", "os_name", "host_os", "host_arch",
    }
    if set(python) != expected_python_keys:
        reasons.append("Python identity is incomplete")
    else:
        version_info = python["version_info"]
        try:
            executable = Path(python["executable"])
            canonical = executable.resolve(strict=True)
            executable_ok = (executable == canonical and stat.S_ISREG(canonical.stat().st_mode)
                             and os.access(canonical, os.X_OK))
        except (OSError, TypeError, ValueError):
            executable_ok = False
        if (python["implementation"] != "CPython" or python["os_name"] != "posix"
                or python["host_os"] not in {"Darwin", "Linux"} or not isinstance(version_info, list)
                or len(version_info) != 3 or any(type(item) is not int for item in version_info)
                or tuple(version_info) < (3, 10, 0) or not executable_ok
                or not _DIGEST.fullmatch(str(python["executable_sha256"]))
                or not all(isinstance(python[key], str) and python[key]
                           for key in ("version", "cache_tag", "host_arch"))):
            reasons.append("Python is not a canonical supported POSIX CPython >=3.10 runtime")
    if (set(capabilities) != CAPABILITY_KEYS
            or any(type(capabilities[key]) is not bool for key in capabilities)
            or not all(capabilities.values())):
        reasons.append("required POSIX sandbox capabilities are unavailable")

    dependencies: dict[str, Any] = {
        "jsonschema": {"version": None, "module_origin": None, "draft202012": False,
                       "validator_for": False},
    }
    imports: list[dict[str, str]] = []
    try:
        if child_probe is not None:
            probe = dict(child_probe(str(python.get("executable", ""))))
        elif importer is importlib.import_module and distribution_version is importlib.metadata.version:
            probe = _bounded_import_probe(str(python.get("executable", "")))
        else:
            probe = _inprocess_import_probe(importer, distribution_version)
        if set(probe) != {"jsonschema", "imports"}:
            raise EnvironmentPreflightError("import probe document is not closed")
        dependencies["jsonschema"] = dict(probe["jsonschema"])
        imports = [dict(row) for row in probe["imports"]]
        version = dependencies["jsonschema"]["version"]
        if (int(version.split(".", 1)[0]) < 4
                or dependencies["jsonschema"].get("draft202012") is not True
                or dependencies["jsonschema"].get("validator_for") is not True):
            raise EnvironmentPreflightError("jsonschema lacks the Draft 2020-12 validator API")
    except Exception as exc:
        detail = str(exc).replace("\n", " ")[:160] or type(exc).__name__
        reasons.append(f"bounded dependency/import preflight failed: {detail}")

    identity = EnvironmentIdentity(
        ENVIRONMENT_SCHEMA, not reasons, tuple(reasons), python, capabilities,
        dependencies, tuple(imports),
        {"git": {"required_for_sandbox": False, "checked": False,
                 "reason": "Git identity is owned by Empirica snapshot capture, not Docker isolation"}},
        dict(PROBE_POLICY))
    validate_environment_identity(identity, require_available=False)
    return identity


def validate_environment_identity(
    value: EnvironmentIdentity | Mapping[str, Any], *, require_available: bool = True,
) -> dict[str, Any]:
    source = asdict(value) if isinstance(value, EnvironmentIdentity) else dict(value)
    expected = {"schema", "available", "reasons", "python", "capabilities",
                "dependencies", "import_smoke", "optional_tools", "probe_policy"}
    if set(source) != expected or source.get("schema") != ENVIRONMENT_SCHEMA:
        raise EnvironmentPreflightError("environment identity schema is malformed")
    if type(source["available"]) is not bool or not isinstance(source["reasons"], (list, tuple)):
        raise EnvironmentPreflightError("environment availability is malformed")
    if require_available and (source["available"] is not True or source["reasons"] not in ([], ())):
        raise EnvironmentPreflightError("environment is not qualified")
    if not require_available and source["available"] is False and source["reasons"]:
        return asdict(value) if isinstance(value, EnvironmentIdentity) else __import__("json").loads(
            __import__("json").dumps(source))
    python = source["python"]
    capabilities = source["capabilities"]
    dependencies = source["dependencies"]
    imports = source["import_smoke"]
    optional_tools = source["optional_tools"]
    if source["probe_policy"] != PROBE_POLICY:
        raise EnvironmentPreflightError("environment probe policy is malformed")
    python_keys = {
        "implementation", "version", "version_info", "cache_tag", "executable",
        "executable_sha256", "os_name", "host_os", "host_arch",
    }
    if not isinstance(python, Mapping) or set(python) != python_keys:
        raise EnvironmentPreflightError("environment Python identity is malformed")
    try:
        executable = Path(python["executable"])
        canonical = executable.resolve(strict=True)
        version_info = python["version_info"]
        python_valid = (
            python["implementation"] == "CPython" and python["os_name"] == "posix"
            and python["host_os"] in {"Darwin", "Linux"} and isinstance(version_info, list)
            and len(version_info) == 3 and all(type(item) is int for item in version_info)
            and tuple(version_info) >= (3, 10, 0) and executable == canonical
            and python["version"] == ".".join(str(item) for item in version_info)
            and python["cache_tag"] == f"cpython-{version_info[0]}{version_info[1]}"
            and stat.S_ISREG(canonical.stat().st_mode) and os.access(canonical, os.X_OK)
            and _DIGEST.fullmatch(str(python["executable_sha256"])) is not None
            and _file_digest(canonical) == python["executable_sha256"]
            and all(isinstance(python[key], str) and python[key]
                    for key in ("version", "cache_tag", "host_arch")))
    except (OSError, TypeError, ValueError):
        python_valid = False
    if not python_valid:
        raise EnvironmentPreflightError("environment Python runtime is outside the supported profile")
    if (not isinstance(capabilities, Mapping) or set(capabilities) != CAPABILITY_KEYS
            or any(capabilities[key] is not True for key in capabilities)):
        raise EnvironmentPreflightError("environment POSIX capabilities are incomplete")
    dependency = dependencies.get("jsonschema") if isinstance(dependencies, Mapping) else None
    try:
        dependency_origin = Path(dependency["module_origin"])
        dependency_version = dependency["version"]
        dependency_valid = (
            set(dependencies) == {"jsonschema"}
            and set(dependency) == {"version", "module_origin", "draft202012", "validator_for"}
            and int(dependency_version.split(".", 1)[0]) >= 4
            and dependency_origin.is_absolute()
            and dependency_origin.resolve(strict=True) == dependency_origin
            and stat.S_ISREG(dependency_origin.stat().st_mode)
            and dependency["draft202012"] is True and dependency["validator_for"] is True)
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        dependency_valid = False
    if not dependency_valid:
        raise EnvironmentPreflightError("environment jsonschema identity is incompatible")
    if (not isinstance(imports, (list, tuple))
            or [row.get("module") for row in imports if isinstance(row, Mapping)] != list(INTERNAL_IMPORTS)
            or len(imports) != len(INTERNAL_IMPORTS)):
        raise EnvironmentPreflightError("environment shipped import identity is incomplete")
    for row in imports:
        if not isinstance(row, Mapping) or set(row) != {"module", "origin"}:
            raise EnvironmentPreflightError("environment shipped import identity is malformed")
        try:
            origin = Path(row["origin"])
            if (row["origin"] != EXPECTED_IMPORT_ORIGINS[row["module"]]
                    or not origin.is_absolute() or origin.resolve(strict=True) != origin
                    or not stat.S_ISREG(origin.stat().st_mode)):
                raise EnvironmentPreflightError("environment shipped import origin is unsafe")
        except (OSError, TypeError, ValueError) as exc:
            raise EnvironmentPreflightError("environment shipped import origin is unsafe") from exc
    if optional_tools != {
            "git": {"required_for_sandbox": False, "checked": False,
                    "reason": "Git identity is owned by Empirica snapshot capture, not Docker isolation"}}:
        raise EnvironmentPreflightError("environment optional-tool boundary is malformed")
    return asdict(value) if isinstance(value, EnvironmentIdentity) else __import__("json").loads(
        __import__("json").dumps(source))
