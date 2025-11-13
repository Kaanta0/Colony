"""Utilities for validating model payloads before instantiation."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any, ClassVar, Mapping, MutableMapping, Sequence


class ModelValidationError(ValueError):
    """Raised when a payload does not satisfy a model's requirements."""

    def __init__(self, model: type[Any], errors: Sequence[str]) -> None:
        self.model = model
        self.errors = list(errors)
        message = ", ".join(self.errors) if self.errors else "invalid payload"
        super().__init__(f"{model.__name__} validation failed: {message}")


@dataclass(frozen=True)
class FieldSpec:
    expected: Any
    description: str
    required: bool = True
    allow_none: bool = False


@dataclass(frozen=True)
class SequenceSpec:
    item: Any
    allow_empty: bool = True


@dataclass(frozen=True)
class MappingSpec:
    key: Any
    value: Any
    allow_empty: bool = True


def is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _describe_expected(expected: Any) -> str:
    if isinstance(expected, FieldSpec):
        return expected.description
    if isinstance(expected, SequenceSpec):
        return f"sequence of {_describe_expected(expected.item)}"
    if isinstance(expected, MappingSpec):
        key_desc = _describe_expected(expected.key)
        value_desc = _describe_expected(expected.value)
        return f"mapping of {key_desc} to {value_desc}"
    if callable(expected) and not isinstance(expected, type):
        return "valid value"
    if isinstance(expected, tuple):
        return " or ".join(_describe_expected(part) for part in expected)
    if isinstance(expected, type):
        return expected.__name__
    return str(expected)


def _matches_type(value: Any, expected: Any) -> bool:
    if expected is Any:
        return True
    if isinstance(expected, FieldSpec):
        return _matches_type(value, expected.expected)
    if isinstance(expected, SequenceSpec):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return False
        if not expected.allow_empty and not value:
            return False
        return all(_matches_type(item, expected.item) for item in value)
    if isinstance(expected, MappingSpec):
        if not isinstance(value, Mapping):
            return False
        if not expected.allow_empty and not value:
            return False
        return all(
            _matches_type(key, expected.key) and _matches_type(item, expected.value)
            for key, item in value.items()
        )
    if isinstance(expected, tuple):
        return any(_matches_type(value, part) for part in expected)
    if isinstance(expected, type):
        if expected is str:
            return isinstance(value, str)
        if expected is int:
            return isinstance(value, int) and not isinstance(value, bool)
        if expected is float:
            return isinstance(value, Real) and not isinstance(value, bool)
        if expected is bool:
            return isinstance(value, bool)
        return isinstance(value, expected)
    if callable(expected):
        try:
            return bool(expected(value))
        except Exception:
            return False
    return True


class ModelValidator:
    """Base class for model payload validators."""

    model: ClassVar[type[Any]]
    fields: ClassVar[Mapping[str, FieldSpec]]

    @classmethod
    def validate(cls, data: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            raise ModelValidationError(
                cls.model,
                [
                    "Payload must be a mapping of field names to values",
                ],
            )

        errors: list[str] = []
        normalized: dict[str, Any] = {}

        for name, spec in cls.fields.items():
            present = name in data
            if not present:
                if spec.required:
                    errors.append(f"Missing required field '{name}' ({spec.description})")
                continue

            value = data[name]
            if value is None and not spec.allow_none:
                errors.append(f"Field '{name}' cannot be null")
                continue

            if value is None and spec.allow_none:
                normalized[name] = value
                continue

            if not _matches_type(value, spec.expected):
                expected_desc = spec.description or _describe_expected(spec.expected)
                normalized_type = type(value).__name__
                errors.append(
                    f"Field '{name}' expected {expected_desc}, received {normalized_type}"
                )
                continue

            normalized[name] = value

        if errors:
            raise ModelValidationError(cls.model, errors)

        for key, value in data.items():
            if key not in cls.fields:
                normalized[key] = value

        return normalized


def validate_dataclass_payload(cls: type[Any], data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate payload for a dataclass if a validator is registered."""

    validator: type[ModelValidator] | None = getattr(cls, "validator", None)
    if validator is None:
        if isinstance(data, MutableMapping):
            return dict(data)
        return {key: data[key] for key in data}
    return validator.validate(data)

