from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

from neuradix_cassar_ops.operations.exceptions import OperationsValidationError
from neuradix_cassar_ops.operations.timestamps import normalize_client_timestamp


class ClientTimestampTestCase(unittest.TestCase):
	def test_normalizes_flutter_utc_timestamp_for_mariadb(self) -> None:
		self.assertEqual(
			normalize_client_timestamp("2026-09-03T06:55:22.811454Z"),
			datetime(2026, 9, 3, 6, 55, 22, 811454),
		)

	def test_converts_offset_timestamp_to_naive_utc(self) -> None:
		self.assertEqual(
			normalize_client_timestamp("2026-09-03T12:25:22.811454+05:30"),
			datetime(2026, 9, 3, 6, 55, 22, 811454),
		)

	def test_accepts_datetime_and_empty_values(self) -> None:
		aware = datetime(2026, 9, 3, 8, 55, tzinfo=timezone(timedelta(hours=2)))
		self.assertEqual(normalize_client_timestamp(aware), datetime(2026, 9, 3, 6, 55))
		self.assertIsNone(normalize_client_timestamp(""))
		self.assertIsNone(normalize_client_timestamp(None))

	def test_rejects_invalid_timestamp(self) -> None:
		with self.assertRaisesRegex(OperationsValidationError, "valid ISO-8601"):
			normalize_client_timestamp("not-a-timestamp")


if __name__ == "__main__":
	unittest.main()
