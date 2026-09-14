from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from gwz import Client
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import LsRequest, RequestMeta


def native_module():
    return pytest.importorskip("gwz._gwz_core")


def write_minimal_workspace(root: Path) -> None:
    conf = root / "gwz.conf"
    conf.mkdir()
    (conf / "gwz.yml").write_text(
        "\n".join(
            [
                "schema: gwz.workspace/v0",
                "workspace:",
                "  id: ws_native",
                "members:",
                "- id: mem_app",
                "  path: repos/app",
                "  type: git",
                "  source_id: src_app",
                "  active: true",
                "  remotes: []",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_native_module_health() -> None:
    native = native_module()

    assert native.health() == "ok"
    assert native.version()


# `gwz --build-info` ends with `core <gwz_core::VERSION>: <gwz_core::BUILD_PROVENANCE>`, and the
# native module's `version()` and `provenance()` return those same two gwz-core constants, so both
# sides expose the gwz-core version itself and the comparison needs no stand-in for it. The
# provenance reads `revision=<commit|unavailable> dirty=<true|false|unknown> source-sha256=<hex>
# build=cargo` (gwz-core build_support/provenance.rs). A build from a git checkout reports its
# commit; a build of the crates.io package reports `revision=unavailable` and a digest of the
# packaged sources. A gwz-py release builds its extension from the gwz-core git tag, while the
# gwz-cli it tests against links gwz-core from crates.io (gwz-core dev-docs/GwzCratesIoPlan.md D7,
# S3.4), so the whole provenance can only be compared when both sides were built from git.
STRICT_RULE = "strict rule"
VERSION_AND_BUILD_RULE = "version and build kind rule"
CORE_LINE = re.compile(r"^core (?P<version>\S+): (?P<provenance>.*)$", re.MULTILINE)


def provenance_fields(provenance: str) -> dict[str, str]:
    return dict(token.split("=", 1) for token in provenance.split() if "=" in token)


def compare_core_provenance(
    native_version: str, native_provenance: str, build_info: str
) -> tuple[str, str | None]:
    """Compare the extension's gwz-core with the core line of the CLI's `--build-info`.

    Returns the rule that applied and a failure message, or None when the rule holds. When both
    sides report a git revision, the extension's whole core provenance must appear in the CLI's
    core line. When either reports `revision=unavailable`, the gwz-core version and the `build=`
    kind must match instead, which proves the same core release rather than the same core build.
    A side without a `revision=` field never selects the weaker rule.
    """
    extension = f"core {native_version}: {native_provenance}"
    match = CORE_LINE.search(build_info)
    cli = match.group(0) if match else "(no core line in --build-info)"
    native_fields = provenance_fields(native_provenance)
    cli_fields = provenance_fields(match.group("provenance")) if match else {}
    sides = f"\n  extension: {extension}\n  CLI:       {cli}"
    if "unavailable" not in (native_fields.get("revision"), cli_fields.get("revision")):
        if extension in cli:
            return STRICT_RULE, None
        return STRICT_RULE, (
            f"gwz-core differs under the {STRICT_RULE}: both sides report a git revision, so the "
            "extension's whole core provenance must appear in the CLI's core line" + sides
        )
    cli_version = match.group("version") if match else None
    build = native_fields.get("build")
    if native_version == cli_version and build is not None and build == cli_fields.get("build"):
        return VERSION_AND_BUILD_RULE, None
    return VERSION_AND_BUILD_RULE, (
        f"gwz-core differs under the {VERSION_AND_BUILD_RULE}: a side reports "
        "revision=unavailable, so the gwz-core version and the build= kind must match" + sides
    )


def test_native_module_reports_compiled_core_provenance() -> None:
    import os
    import subprocess

    native = native_module()
    provenance = native.provenance()
    assert re.search(r"source-sha256=[0-9a-f]{64}", provenance)
    assert "build=cargo" in provenance
    binary = os.environ.get("GWZ_RUST_BIN")
    if binary:
        build_info = subprocess.check_output([binary, "--build-info"], text=True)
        rule, failure = compare_core_provenance(native.version(), provenance, build_info)
        print(f"gwz-core provenance compared under the {rule}")
        assert failure is None, failure


GIT_CORE = f"revision={'a' * 40} dirty=false source-sha256={'1' * 64} build=cargo"
OTHER_GIT_CORE = f"revision={'b' * 40} dirty=false source-sha256={'1' * 64} build=cargo"
REGISTRY_CORE = f"revision=unavailable dirty=unknown source-sha256={'2' * 64} build=cargo"


def cli_build_info(core_version: str, core_provenance: str) -> str:
    cli = f"revision={'c' * 40} dirty=false source-sha256={'3' * 64} build=cargo"
    return f"gwz 1.0.12\ncli: {cli}\ncore {core_version}: {core_provenance}\n"


def test_core_provenance_of_equal_revisions_passes_the_strict_rule() -> None:
    build_info = cli_build_info("1.0.12", GIT_CORE)

    assert compare_core_provenance("1.0.12", GIT_CORE, build_info) == (STRICT_RULE, None)


def test_core_provenance_of_different_revisions_fails_the_strict_rule() -> None:
    build_info = cli_build_info("1.0.12", OTHER_GIT_CORE)

    rule, failure = compare_core_provenance("1.0.12", GIT_CORE, build_info)

    assert rule == STRICT_RULE
    assert failure is not None
    assert STRICT_RULE in failure
    assert f"extension: core 1.0.12: {GIT_CORE}" in failure
    assert f"CLI:       core 1.0.12: {OTHER_GIT_CORE}" in failure


@pytest.mark.parametrize(
    ("native_provenance", "cli_provenance"),
    [(GIT_CORE, REGISTRY_CORE), (REGISTRY_CORE, GIT_CORE)],
    ids=["cli-unavailable", "extension-unavailable"],
)
def test_core_provenance_without_a_revision_passes_on_same_version_and_build_kind(
    native_provenance: str, cli_provenance: str
) -> None:
    build_info = cli_build_info("1.0.12", cli_provenance)

    assert compare_core_provenance("1.0.12", native_provenance, build_info) == (
        VERSION_AND_BUILD_RULE,
        None,
    )


def test_core_provenance_without_a_revision_fails_on_a_different_version() -> None:
    build_info = cli_build_info("1.0.11", REGISTRY_CORE)

    rule, failure = compare_core_provenance("1.0.12", GIT_CORE, build_info)

    assert rule == VERSION_AND_BUILD_RULE
    assert failure is not None
    assert VERSION_AND_BUILD_RULE in failure
    assert f"extension: core 1.0.12: {GIT_CORE}" in failure
    assert f"CLI:       core 1.0.11: {REGISTRY_CORE}" in failure


def test_core_provenance_without_a_revision_fails_on_a_different_build_kind() -> None:
    bazel_core = REGISTRY_CORE.replace("build=cargo", "build=bazel")
    build_info = cli_build_info("1.0.12", bazel_core)

    rule, failure = compare_core_provenance("1.0.12", GIT_CORE, build_info)

    assert rule == VERSION_AND_BUILD_RULE
    assert failure is not None
    assert VERSION_AND_BUILD_RULE in failure
    assert f"extension: core 1.0.12: {GIT_CORE}" in failure
    assert f"CLI:       core 1.0.12: {bazel_core}" in failure


def test_native_bridge_ls(tmp_path: Path) -> None:
    native = native_module()
    write_minimal_workspace(tmp_path)
    client = Client(root=tmp_path, bridge=NativeCoreBridge(native=native))

    response = asyncio.run(client.ls())

    assert response.members is not None
    assert [member.id for member in response.members] == ["mem_app"]
    assert response.members[0].path == "repos/app"
    assert not response.members[0].materialized


def test_native_bridge_routes_unsupported_methods_explicitly() -> None:
    native = native_module()
    bridge = NativeCoreBridge(native=native)
    request = LsRequest(
        meta=RequestMeta(
            request_id="req_unsupported",
            schema_version="gwz.protocol/v0",
            workspace=None,
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
            transport=None,
            invocation=None,
        ),
        include_unmaterialized=True,
    )

    with pytest.raises(GwzBridgeError, match="unsupported gwz-core method"):
        asyncio.run(bridge.call("unsupported", "LsRequest", "LsResponse", request))
