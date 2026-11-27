from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError


def normalize_client_timestamp(value: Any) -> datetime | None:
	"""Convert an ISO-8601 client timestamp to a MariaDB-compatible UTC datetime."""
	if value is None:
		return None
	if isinstance(value, datetime):
		parsed = value
	else:
		text = str(value).strip()
		if not text:
			return None
		if text.endswith(("Z", "z")):
			text = f"{text[:-1]}+00:00"
		try:
			parsed = datetime.fromisoformat(text)
		except ValueError as exception:
			raise OperationsValidationError(
				"client_timestamp must be a valid ISO-8601 date and time."
			) from exception

	if parsed.tzinfo is not None:
		parsed = parsed.astimezone(UTC).replace(tzinfo=None)
	return parsed
