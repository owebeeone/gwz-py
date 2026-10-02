"""TR2.5 setting resolution through the candidate native entry."""
from __future__ import annotations

import asyncio
import logging
import os
import warnings
from pathlib import Path

import pytest

from gwz import Client
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from host_helpers import call, error_code, init_request, submit
from test_client_host_transport import load


@pytest.fixture
def candidate():
    path = os.environ.get("GWZ_PY_NATIVE_MODULE")
    if not path:
        pytest.skip("needs the lane's candidate extension")
    return load(path)


@pytest.fixture
def environment(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.delenv("GWZ_TRANSPORT", raising=False)
    return monkeypatch, home


@pytest.mark.parametrize("entry", [call, submit])
def test_invalid_environment_refuses_before_registration(candidate, environment, tmp_path, entry):
    patch, _home = environment
    patch.setenv("GWZ_TRANSPORT", "bogus")
    host = candidate.ClientHost()
    with pytest.raises(RuntimeError) as failed:
        entry(host, init_request(tmp_path, "req_setting_refused"))
    assert error_code(failed.value) == "InvalidRequest"
    assert 'GWZ_TRANSPORT must be gwz or native, not "bogus"' in str(failed.value)
    assert "--transport" not in str(failed.value)
    with pytest.raises(RuntimeError, match="operation op_req_setting_refused not found"):
        candidate.try_operation_result("op_req_setting_refused")
    client = Client(root=tmp_path, bridge=NativeCoreBridge(native=candidate))
    with pytest.raises(GwzBridgeError) as bridged:
        asyncio.run(client.fetch())
    assert bridged.value.code == "InvalidRequest"
    assert "native bridge call failed for fetch:" in str(bridged.value)
    asyncio.run(client.close())
    host.close()


@pytest.mark.parametrize("entry", [call, submit])
def test_warning_as_error_refuses_native_before_registration(candidate, environment, tmp_path, entry):
    patch, _home = environment
    patch.setenv("GWZ_TRANSPORT", "native")
    host = candidate.ClientHost()
    with warnings.catch_warnings():
        warnings.filterwarnings("error", module="gwz")
        with pytest.raises(UserWarning, match="GWZ_TRANSPORT=native"):
            entry(host, init_request(tmp_path, "req_warning_refused"))
    with pytest.raises(RuntimeError, match="operation op_req_warning_refused not found"):
        candidate.try_operation_result("op_req_warning_refused")
    host.close()


def attempt(host, tmp_path, request_id):
    with pytest.raises(RuntimeError) as failed:
        call(host, init_request(tmp_path, request_id))
    return str(failed.value)


def test_existing_host_reads_changed_global_file_and_environment_each_operation(
    candidate, environment, tmp_path
):
    patch, home = environment
    config = home / ".gitconfig"
    config.write_text("[gwz]\ntransport = native\n")
    host = candidate.ClientHost()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        attempt(host, tmp_path, "req_global_native")
        assert len(seen) == 1 and "gwz.transport in" in str(seen[-1].message)
        config.write_text("[gwz]\ntransport = gwz\n")
        attempt(host, tmp_path, "req_global_gwz")
        assert len(seen) == 1
        config.write_text("[gwz]\ntransport = invalid\n")
        refused = attempt(host, tmp_path, "req_bad_global")
        assert "must be gwz or native" in refused and "GWZ_TRANSPORT decides without it" in refused
        patch.setenv("GWZ_TRANSPORT", "native")
        attempt(host, tmp_path, "req_environment_wins")
        assert len(seen) == 2 and "GWZ_TRANSPORT=native" in str(seen[-1].message)
        patch.setenv("GWZ_TRANSPORT", "gwz")
        attempt(host, tmp_path, "req_environment_gwz")
        assert len(seen) == 2
    host.close()


def test_local_operation_ignores_invalid_setting(candidate, environment, tmp_path):
    patch, _home = environment
    patch.setenv("GWZ_TRANSPORT", "bad")
    client = Client(root=tmp_path / "local", bridge=NativeCoreBridge(native=candidate))
    asyncio.run(client.create_workspace())
    asyncio.run(client.close())


def test_ignored_repository_value_logs_once_per_file_per_client_even_with_warnings_errors(
    candidate, environment, tmp_path, caplog, monkeypatch
):
    _patch, _home = environment
    root = tmp_path / "workspace"
    client = Client(root=root, bridge=NativeCoreBridge(native=candidate))
    asyncio.run(client.create_workspace())
    config = root / ".git" / "config"
    config.write_text(config.read_text() + "\n[gwz]\ntransport = invalid\n")
    caplog.set_level(logging.WARNING, logger="gwz")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for _ in range(2):
            asyncio.run(client.fetch())
    records = [r for r in caplog.records if r.name == "gwz"]
    assert len(records) == 1
    assert 'ignoring gwz.transport = "invalid"' in records[0].message
    assert "(root)" in records[0].message and "--unset-all gwz.transport" in records[0].message
    other = Client(root=root, bridge=NativeCoreBridge(native=candidate))
    asyncio.run(other.fetch())
    assert len([r for r in caplog.records if r.name == "gwz"]) == 2
    # A failing application logging handler also cannot refuse the operation.
    def fail(*_args, **_kwargs):
        raise RuntimeError("logging failed")
    monkeypatch.setattr(logging.getLogger("gwz"), "warning", fail)
    third = Client(root=root, bridge=NativeCoreBridge(native=candidate))
    asyncio.run(third.fetch())
    for owner in (client, other, third):
        asyncio.run(owner.close())


def test_native_route_has_no_runtime_or_running_canceller(candidate, environment, tmp_path):
    from host_helpers import DELAY_VARIABLE, started, result, wait_until
    patch, _home = environment
    patch.setenv("GWZ_TRANSPORT", "native")
    patch.setenv("HOME", "")  # A transport runtime would refuse its missing absolute HOME.
    patch.setenv(DELAY_VARIABLE, "800")
    host = candidate.ClientHost()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        operation = submit(host, init_request(tmp_path, "req_native_route"))
    wait_until(lambda: started(candidate, operation), 5, "native operation starting")
    with pytest.raises(RuntimeError) as refused:
        host.cancel_operation(operation)
    assert error_code(refused.value) == "UnsupportedOperation"
    finished = result(candidate, operation)
    assert "HOME" not in str(finished.errors)
    assert "Cancelled" not in str(finished.errors)
    host.close()
