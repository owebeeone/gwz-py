from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
from enum import Enum
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Mapping

from taut.ir.model import EnumRef, ListOf, MapOf, MsgRef, Scalar, TypeRef
from taut.ir.load import schema_from_json
from taut.wire import codec as wire_codec

from gwz.errors import GwzProtocolError
from gwz.protocol import generated


DEFAULT_SERVICE = "GwzCore"
EVENTS_SUBSCRIBE_METHOD = "events.subscribe"
OPERATION_RESULT_METHOD = "operation.result"
IR_PACKAGE = "gwz.protocol.generated"
IR_RESOURCE = "gwz.ir.json"
SCHEMA_VERSION_STATUS = "unresolved"
SCHEMA_VERSION: str | None = None


@cache
def _ir_bytes() -> bytes:
    return resources.files(IR_PACKAGE).joinpath(IR_RESOURCE).read_bytes()


@cache
def schema():
    with resources.files(IR_PACKAGE).joinpath(IR_RESOURCE).open(
        "r",
        encoding="utf-8",
    ) as handle:
        return schema_from_json(json.load(handle))


@cache
def generated_classes() -> Mapping[str, type[Any]]:
    declared_names = set(schema().messages) | set(schema().enums)
    classes = {
        name: value
        for name in declared_names
        if inspect.isclass(value := getattr(generated, name, None))
    }
    return MappingProxyType(classes)


def generated_class(name: str) -> type[Any]:
    try:
        return generated_classes()[name]
    except KeyError as exc:
        raise GwzProtocolError(f"unsupported generated class: {name}") from exc


def message_class(message_name: str) -> type[Any]:
    if message_name not in schema().messages:
        raise GwzProtocolError(f"unsupported message: {message_name}")
    cls = generated_class(message_name)
    if not dataclasses.is_dataclass(cls):
        raise GwzProtocolError(f"generated message is not a dataclass: {message_name}")
    return cls


def enum_class(enum_name: str) -> type[Enum]:
    if enum_name not in schema().enums:
        raise GwzProtocolError(f"unsupported enum: {enum_name}")
    cls = generated_class(enum_name)
    if not issubclass(cls, Enum):
        raise GwzProtocolError(f"generated enum is not an Enum: {enum_name}")
    return cls


def message_def(message_name: str):
    try:
        return schema().messages[message_name]
    except KeyError as exc:
        raise GwzProtocolError(f"unsupported message: {message_name}") from exc


def service_def(service_name: str = DEFAULT_SERVICE):
    try:
        return schema().services[service_name]
    except KeyError as exc:
        raise GwzProtocolError(f"unsupported service: {service_name}") from exc


def service_method(method_name: str, service_name: str = DEFAULT_SERVICE):
    for method in service_def(service_name).methods:
        if method.name == method_name:
            return method
    raise GwzProtocolError(f"unsupported service method: {service_name}.{method_name}")


def request_message_names(service_name: str = DEFAULT_SERVICE) -> dict[str, str]:
    names: dict[str, str] = {}
    for method in service_def(service_name).methods:
        if method.role != "in":
            continue
        for param_name, type_ref in method.params:
            if param_name == "request" and isinstance(type_ref, MsgRef):
                names[method.name] = type_ref.name
                break
    return names


def response_message_names(service_name: str = DEFAULT_SERVICE) -> dict[str, str]:
    return {
        method.name: method.output.name
        for method in service_def(service_name).methods
        if method.role == "in" and isinstance(method.output, MsgRef)
    }


def event_message_names(service_name: str = DEFAULT_SERVICE) -> dict[str, tuple[str, ...]]:
    names: dict[str, tuple[str, ...]] = {}
    for method in service_def(service_name).methods:
        if method.role != "out" or not method.streams():
            continue
        event_names = tuple(type_ref.name for _slot, type_ref in method.events if isinstance(type_ref, MsgRef))
        if event_names:
            names[method.name] = event_names
    return names


def result_message_names(service_name: str = DEFAULT_SERVICE) -> dict[str, str]:
    return {
        method.name: method.output.name
        for method in service_def(service_name).methods
        if method.role == "out" and not method.streams() and isinstance(method.output, MsgRef)
    }


def request_message_name(method_name: str, service_name: str = DEFAULT_SERVICE) -> str:
    try:
        return request_message_names(service_name)[method_name]
    except KeyError as exc:
        raise GwzProtocolError(f"method has no request message: {service_name}.{method_name}") from exc


def response_message_name(method_name: str, service_name: str = DEFAULT_SERVICE) -> str:
    try:
        return response_message_names(service_name)[method_name]
    except KeyError as exc:
        raise GwzProtocolError(f"method has no response message: {service_name}.{method_name}") from exc


def event_message_name(
    method_name: str = EVENTS_SUBSCRIBE_METHOD,
    service_name: str = DEFAULT_SERVICE,
) -> str:
    try:
        names = event_message_names(service_name)[method_name]
    except KeyError as exc:
        raise GwzProtocolError(f"method has no event message: {service_name}.{method_name}") from exc
    if len(names) != 1:
        raise GwzProtocolError(f"method has {len(names)} event messages: {service_name}.{method_name}")
    return names[0]


def result_message_name(
    method_name: str = OPERATION_RESULT_METHOD,
    service_name: str = DEFAULT_SERVICE,
) -> str:
    try:
        return result_message_names(service_name)[method_name]
    except KeyError as exc:
        raise GwzProtocolError(f"method has no result message: {service_name}.{method_name}") from exc


def schema_version_metadata() -> dict[str, str | None]:
    return {
        "status": SCHEMA_VERSION_STATUS,
        "value": SCHEMA_VERSION,
        "reason": "packaged gwz.ir.json does not contain a canonical schema_version literal",
    }


def ir_fingerprint(algorithm: str = "sha256") -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise GwzProtocolError(f"unsupported IR fingerprint algorithm: {algorithm}") from exc
    digest.update(_ir_bytes())
    return f"{algorithm}:{digest.hexdigest()}"


def protocol_metadata() -> dict[str, Any]:
    return {
        "schema_version": schema_version_metadata(),
        "ir_fingerprint": ir_fingerprint(),
    }


def to_wire(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: to_wire(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, list):
        return [to_wire(item) for item in value]
    if isinstance(value, tuple):
        return [to_wire(item) for item in value]
    if isinstance(value, dict):
        return {to_wire(key): to_wire(item) for key, item in value.items()}
    return value


def from_wire(message_name: str, payload: Mapping[str, Any]) -> Any:
    return _from_wire_message(message_name, payload)


def encode_message(message_name: str, dataclass_value: Any) -> bytes:
    cls = message_class(message_name)
    if not isinstance(dataclass_value, cls):
        raise GwzProtocolError(
            f"expected {message_name} dataclass, got {type(dataclass_value).__name__}"
        )
    return wire_codec.encode(schema(), message_name, to_wire(dataclass_value))


def decode_message(message_name: str, data: bytes) -> Any:
    message_class(message_name)
    try:
        payload = wire_codec.decode(schema(), message_name, data)
        return from_wire(message_name, payload)
    except GwzProtocolError:
        raise
    except Exception as exc:
        raise GwzProtocolError(f"failed to decode {message_name}") from exc


def _from_wire_message(message_name: str, payload: Any) -> Any:
    cls = message_class(message_name)
    if isinstance(payload, cls):
        return payload
    if not isinstance(payload, Mapping):
        raise GwzProtocolError(f"message payload must be a mapping: {message_name}")

    kwargs = {}
    for field in message_def(message_name).fields:
        raw_value = payload.get(field.name)
        kwargs[field.name] = None if raw_value is None else _from_wire_value(field.type, raw_value)
    return cls(**kwargs)


def _from_wire_value(type_ref: TypeRef, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(type_ref, Scalar):
        return float(value) if type_ref.kind == "float" else value
    if isinstance(type_ref, EnumRef):
        return _from_wire_enum(type_ref.name, value)
    if isinstance(type_ref, MsgRef):
        return _from_wire_message(type_ref.name, value)
    if isinstance(type_ref, ListOf):
        if not isinstance(value, (list, tuple)):
            raise GwzProtocolError(f"list field decoded as {type(value).__name__}")
        return [_from_wire_value(type_ref.elem, item) for item in value]
    if isinstance(type_ref, MapOf):
        if not isinstance(value, Mapping):
            raise GwzProtocolError(f"map field decoded as {type(value).__name__}")
        return {
            _from_wire_value(type_ref.key, key): _from_wire_value(type_ref.value, item)
            for key, item in value.items()
        }
    raise GwzProtocolError(f"unsupported IR type reference: {type_ref!r}")


def _from_wire_enum(enum_name: str, value: Any) -> Enum:
    cls = enum_class(enum_name)
    if isinstance(value, cls):
        return value
    if isinstance(value, str):
        try:
            return cls[value]
        except KeyError as exc:
            raise GwzProtocolError(f"unsupported {enum_name} enum value: {value}") from exc
    if isinstance(value, int):
        members = schema().enums[enum_name].members
        reverse_members = {wire_value: name for name, wire_value in members.items()}
        try:
            return cls[reverse_members[value]]
        except KeyError as exc:
            raise GwzProtocolError(f"unsupported {enum_name} enum wire value: {value}") from exc
    raise GwzProtocolError(f"unsupported {enum_name} enum payload: {value!r}")
