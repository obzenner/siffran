#!/usr/bin/env python3
"""Generate hand-pinned Docker 27/28 semantic boundary fixtures.

The literals are based on the G11 probe corpus and Docker's projected inspect forms. This script
must not import Empirica production code: fixtures are an independent admission oracle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "plugins/empirica/tests/fixtures/docker/generic-linux-v27.json"


def fixture() -> dict:
    capabilities = [
        {
            "name": "linux-rootful-amd64-default-seccomp",
            "host_os": "Linux", "host_architecture": "x86_64",
            "server_os": "linux", "server_architecture": "x86_64",
            "platform_name": "Docker Engine - Community", "operating_system": "Ubuntu 24.04",
            "security_options": ["name=seccomp,profile=default", "name=cgroupns"],
        },
        {
            "name": "linux-rootless-amd64-builtin-seccomp",
            "host_os": "Linux", "host_architecture": "amd64",
            "server_os": "linux", "server_architecture": "amd64",
            "platform_name": "Docker Engine - Community", "operating_system": "Debian GNU/Linux",
            "security_options": ["name=rootless", "name=cgroupns",
                                 "name=seccomp,profile=builtin"],
        },
        {
            "name": "desktop-macos-arm64-containerd",
            "host_os": "Darwin", "host_architecture": "arm64",
            "server_os": "linux", "server_architecture": "aarch64",
            "platform_name": "Docker Desktop", "operating_system": "Docker Desktop",
            "security_options": ["name=cgroupns", "name=seccomp,profile=builtin"],
        },
        {
            "name": "desktop-macos-amd64-classic",
            "host_os": "Darwin", "host_architecture": "x86_64",
            "server_os": "linux", "server_architecture": "amd64",
            "platform_name": "Docker Desktop", "operating_system": "Docker Desktop",
            "security_options": ["name=seccomp,profile=default", "name=cgroupns"],
        },
    ]
    for capability in capabilities:
        capability.update(memory_limit=True, swap_limit=True, cpu_cfs_quota=True,
                          pids_limit=True, ncpu=8, mem_total=17179869184)
    terminal = [
        {"name": "natural-pass", "State": {"Status": "exited", "Running": False,
         "ExitCode": 0, "OOMKilled": False, "Error": ""}},
        {"name": "natural-fail-125", "State": {"Status": "exited", "Running": False,
         "ExitCode": 125, "OOMKilled": False, "Error": ""}},
        {"name": "exit-137-unavailable", "State": {"Status": "exited", "Running": False,
         "ExitCode": 137, "OOMKilled": False, "Error": ""}},
        {"name": "failed-start-created", "State": {"Status": "created", "Running": False,
         "ExitCode": 127, "OOMKilled": False,
         "Error": "failed to create task: OCI runtime create failed"}},
        {"name": "timeout-running", "State": {"Status": "running", "Running": True,
         "ExitCode": 0, "OOMKilled": False, "Error": ""}},
    ]
    static_container = {
        "Id": "a" * 64, "Image": "sha256:" + "0" * 64,
        "Config": {
            "Image": "sha256:" + "1" * 64, "Entrypoint": ["python"],
            "Cmd": ["/inputs/a.py", "--exact"],
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/work", "MODE=test",
                    "TMPDIR=/tmp"],
            "WorkingDir": "/work", "User": "65534:65534", "Labels": {"image.label": "kept"},
            "ExposedPorts": None, "Volumes": None,
        },
        "HostConfig": {
            "NetworkMode": "none", "IpcMode": "private", "PidMode": "", "UTSMode": "",
            "CgroupnsMode": "private", "UsernsMode": "", "Privileged": False,
            "PortBindings": {}, "PublishAllPorts": False, "Devices": [],
            "DeviceRequests": None, "DeviceCgroupRules": None, "VolumesFrom": None,
            "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges:true"],
            "CapAdd": None, "CapDrop": ["ALL"], "Memory": 2147483648,
            "MemorySwap": 2147483648, "NanoCpus": 2000000000, "PidsLimit": 256,
            "Ulimits": [
                {"Name": "nofile", "Soft": 1024, "Hard": 1024},
                {"Name": "fsize", "Soft": 67108864, "Hard": 67108864},
                {"Name": "core", "Soft": 0, "Hard": 0},
            ],
            "ShmSize": 67108864,
            "Tmpfs": {
                "/work": "rw,nodev,nosuid,exec,size=536870912,mode=1777",
                "/tmp": "rw,nodev,nosuid,noexec,size=67108864,mode=1777",
            },
        },
        "Mounts": [{"Type": "bind", "Source": "/stage/root", "Destination": "/inputs",
                    "RW": False, "Propagation": "rprivate", "Mode": ""}],
        "State": {"Status": "created", "Running": False, "ExitCode": 0,
                  "OOMKilled": False, "Error": ""},
        "Networks": {"none": {"NetworkID": "", "EndpointID": ""}},
    }
    return {
        "schema": "siffran.empirica.test-fixture.docker-generic-linux/2",
        "source": ("G12 pinned from the G11 Docker probe corpus plus verbatim docker/cli and "
                   "moby 20.10-29 not-found/cidfile behavior; independent literals, not planner output"),
        "provenance": {
            "static_inspect": [
                "G11 local Docker probe h1-container-inspect.json; bundle manifest sha256:7ac2c9d9a37c2afa71967a9f65124c613bdb9b7748ee65a1bc066e4405acd575",
                "G11 local Docker probe h2-static-inspect.json; bundle manifest sha256:7ac2c9d9a37c2afa71967a9f65124c613bdb9b7748ee65a1bc066e4405acd575"
            ],
            "absence_forms": [
                "moby/libnetwork/error.go ErrNoSuchNetwork: network %s not found (Docker 24-29 daemon response)",
                "moby/client/network_inspect.go empty-ID fallback: No such network: %s",
                "docker/cli errdefs formatting: Error response from daemon: <message>",
                "G12 independent implementation review B5 recorded Docker 24-29 output"
            ],
            "cidfile_contract": [
                "moby/container/idfile/idfile.go New: O_CREATE|O_EXCL|O_WRONLY mode 0666",
                "moby/container/idfile/idfile.go preexisting-file refusal",
                "child umask 077 derives owner-only 0600"
            ],
            "generator_independence": "This generator imports no Empirica planner, admission, or runtime module."
        },
        "static_container_inspect": static_container,
        "docker_cli_create_contract": {
            "forbidden_default_namespace_flags": ["--pid", "--uts"],
            "cidfile_preexisting_error":
                "container ID file found, make sure the other container isn't running or delete /private/run/container.cid",
            "cidfile_create_mode_under_umask_077": "0600",
        },
        "absence_stderr": {
            "container_no_such": "Error: No such container: " + "a" * 64,
            "container_not_found": "Error response from daemon: container " + "a" * 64 + " not found",
            "network_no_such": "Error: No such network: " + "b" * 64,
            "network_not_found": "Error response from daemon: network " + "b" * 64 + " not found",
        },
        "capabilities": capabilities,
        "image_optional_forms": [
            {"name": "missing", "Id": "sha256:" + "1" * 64,
             "Os": "linux", "Architecture": "amd64"},
            {"name": "null", "Id": "sha256:" + "1" * 64,
             "Os": "linux", "Architecture": "aarch64", "Volumes": None},
            {"name": "empty", "Id": "sha256:" + "1" * 64,
             "Os": "linux", "Architecture": "arm64", "Volumes": {}},
            {"name": "declared", "Id": "sha256:" + "1" * 64,
             "Os": "linux", "Architecture": "amd64", "Volumes": {"/data": {}}},
        ],
        "terminal_forms": terminal,
        "network_endpoint_forms": [
            {"name": "none-attached", "NetworkID": "", "EndpointID": ""},
            {"name": "failed-start-none", "NetworkID": "0" * 64, "EndpointID": ""},
            {"name": "attached-private-bridge", "NetworkID": "2" * 64,
             "EndpointID": "3" * 64},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(fixture(), indent=2, sort_keys=True) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            raise SystemExit(f"generated Docker fixture differs: {TARGET}")
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
