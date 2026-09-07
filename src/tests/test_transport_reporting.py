from types import SimpleNamespace
import json
import pytest

from gwz.cli_render import render_response
from gwz.protocol.generated import (
    AggregateStatus, TransportObservation, TransportOperation,
    TransportCredentialMethod, TransportSelectionSource,
)


def test_transport_output_does_not_turn_an_offer_into_authentication() -> None:
    row = TransportObservation(
        repository_path="repos/app", remote="origin", operation=TransportOperation.push,
        credential_method=TransportCredentialMethod.file,
        selection_source=TransportSelectionSource.invocation_default,
        credential_offered=True, authenticated=None, public_key_fingerprint=None,
    )
    response = SimpleNamespace(response=SimpleNamespace(meta=SimpleNamespace(
        transport=[row], aggregate_status=AggregateStatus.ok,
    )))
    human = render_response(response)
    assert "credential=file source=invocation_default offered=true authenticated=unknown" in human
    row.authenticated = True
    assert "authenticated=yes" in render_response(response)


def test_transport_rows_are_serializable_without_secret_material() -> None:
    from gwz.cli_render_parts.machine import json_default
    row = TransportObservation(
        repository_path="repos/app", remote="origin", operation=TransportOperation.fetch,
        credential_method=TransportCredentialMethod.agent,
        selection_source=TransportSelectionSource.ambient,
        credential_offered=True, authenticated=True, public_key_fingerprint=None,
    )
    value = json.loads(json.dumps(row, default=json_default))
    assert value["credential_method"] == "agent"
    assert value["public_key_fingerprint"] is None
    assert "private_key_path" not in value


@pytest.mark.parametrize("streamed", [False, True])
def test_failed_operations_keep_transport_evidence_in_error_output(streamed: bool) -> None:
    from gwz.cli_render_parts.errors import render_error
    from gwz.client_helpers import raise_for_response
    from gwz.errors import GwzOperationError
    from gwz.protocol.generated import (
        ActionKind, OperationResult, PushResponse, ResponseEnvelope, ResponseMeta,
    )
    rows = [TransportObservation(
        repository_path="repos/app", remote="origin", operation=TransportOperation.push,
        credential_method=TransportCredentialMethod.file,
        selection_source=TransportSelectionSource.invocation_default,
        credential_offered=True, authenticated=None, public_key_fingerprint=None,
    )]
    if streamed:
        response = OperationResult(
            operation_id="op_failure", request_id="req_failure", action=ActionKind.push,
            aggregate_status=AggregateStatus.failed, started_at_ms=1, finished_at_ms=2,
            members=[], errors=[], attribution=None, transport=rows,
        )
    else:
        response = PushResponse(response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_failure", schema_version="gwz.protocol/v0",
                action=ActionKind.push, aggregate_status=AggregateStatus.failed,
                operation_id="op_failure", message=None, attribution=None, transport=rows,
            ), members=[], errors=[],
        ))
    with pytest.raises(GwzOperationError) as caught:
        raise_for_response(response)
    assert "offered=true authenticated=unknown" in render_error(caught.value)
    machine = json.loads(render_error(caught.value, json_mode=True))
    assert machine["meta"]["operation_id"] == "op_failure"
    assert machine["meta"]["transport"][0]["authenticated"] is None
