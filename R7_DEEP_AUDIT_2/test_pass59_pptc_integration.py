"""Pass 59: PPTC (Persistent Persona Per City) End-to-End Integration Tests.

These tests exercise the full PPTC flow with a mocked database, including:
- New visitor to a city (creates persona)
- Returning visitor to a city (reuses persona)
- Quarantined persona in a city (creates new persona)
- Cross-city persona switching (state isolation)
- persona_by_city table integrity
- city_persona.py public API contract
- Edge cases (empty city, unknown city, special chars)

Unlike Pass 57/58 (which test individual components), Pass 59 tests
the orchestration: the full get_or_create_persona_for_city flow with
all its database interactions mocked at the connection level.
"""

import sys
import os
import asyncio
import unittest
import re
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, '/home/user/project')
sys.path.insert(0, '/home/user/project/browser-runtime')

# Required env vars for any transitive imports
os.environ.setdefault('ORCH_TASK_TMPFS_DIR', '/tmp')
os.environ.setdefault('DB_PASSWD', 'x' * 32)
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 64)
os.environ.setdefault('TASK_TMPFS_SIZE_MB', '512')
os.environ.setdefault('PERSONA_PID_BUDGET', '100')


class TestPPTCEndToEnd(unittest.IsolatedAsyncioTestCase):
    """End-to-end PPTC flow tests with mocked database."""

    async def asyncSetUp(self):
        """Set up the city_persona module for testing."""
        # Force reimport to pick up the latest module state
        for mod_name in list(sys.modules.keys()):
            if 'city_persona' in mod_name:
                del sys.modules[mod_name]
        self.mod = __import__('city_persona')

    def _make_conn(self, existing_persona=None, persona_state='active',
                   task_count=0, fetchrow_side_effect=None):
        """Build a mock asyncpg conn with controlled query responses.

        Args:
            existing_persona: If set, the SELECT returns this persona.
            persona_state: The state of the existing persona ('active',
                           'limited', 'banned', 'rotated').
            task_count: The persona's task count.
            fetchrow_side_effect: If set, an iterable of rows to return
                                  from sequential fetchrow() calls.
        """
        conn = AsyncMock()
        if fetchrow_side_effect is not None:
            conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        elif existing_persona:
            row = {
                "persona_id": existing_persona,
                "last_used": "2026-01-01",
                "task_count": task_count,
                "state": persona_state,
                "signup_geo_region": "tx",
            }
            conn.fetchrow = AsyncMock(return_value=row)
        else:
            conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        return conn

    # ========================================================================
    # Test 1: First visit to a city creates a persona
    # ========================================================================
    async def test_first_visit_creates_persona(self):
        """First visit to a city (no existing mapping) creates a new persona."""
        conn = self._make_conn(existing_persona=None)

        # Enable PPPC for this test
        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "Dallas", {"signup_city": "Dallas"}
            )
        self.assertTrue(was_created)
        self.assertTrue(pid.startswith("ppc-"))
        # Should have called INSERT at least twice (personas + persona_by_city)
        self.assertGreaterEqual(conn.execute.call_count, 2)

    # ========================================================================
    # Test 2: Returning visit reuses persona
    # ========================================================================
    async def test_returning_visit_reuses(self):
        """Returning visit to a city reuses the existing persona."""
        existing = "ppc-aaaaaaaa-bbbbbbbb"
        conn = self._make_conn(existing_persona=existing,
                                persona_state='active', task_count=3)

        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "Dallas", {"signup_city": "Dallas"}
            )
        self.assertFalse(was_created)
        self.assertEqual(pid, existing)
        # UPDATE for persona_by_city (last_used, task_count++)
        update_calls = [c for c in conn.execute.call_args_list
                        if 'UPDATE' in str(c)]
        self.assertGreater(len(update_calls), 0)

    # ========================================================================
    # Test 3: Quarantined persona gets replaced
    # ========================================================================
    async def test_quarantined_creates_new(self):
        """A 'limited' persona in a city triggers a fresh persona."""
        old = "ppc-old-old-old"
        conn = self._make_conn(existing_persona=old,
                                persona_state='limited', task_count=10)

        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "Dallas", {"signup_city": "Dallas"}
            )
        self.assertTrue(was_created)
        self.assertNotEqual(pid, old)
        # DELETE for the old mapping
        delete_calls = [c for c in conn.execute.call_args_list
                       if 'DELETE' in str(c)]
        self.assertGreater(len(delete_calls), 0)

    # ========================================================================
    # Test 4: Disabled PPTC returns (None, False)
    # ========================================================================
    async def test_disabled_returns_none(self):
        """When PERSISTENT_PERSONA_ENABLED=False, returns (None, False)."""
        conn = self._make_conn()
        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', False):
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "Dallas", {"signup_city": "Dallas"}
            )
        self.assertIsNone(pid)
        self.assertFalse(was_created)
        # No DB calls should have been made
        conn.fetchrow.assert_not_called()
        conn.execute.assert_not_called()

    # ========================================================================
    # Test 5: Empty city returns (None, False)
    # ========================================================================
    async def test_empty_city_returns_none(self):
        """An empty city name returns (None, False) without DB calls."""
        conn = self._make_conn()
        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            # Empty city string
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "", {"signup_city": ""}
            )
        self.assertIsNone(pid)
        self.assertFalse(was_created)
        # No DB calls
        conn.fetchrow.assert_not_called()

        # None city string
        pid, was_created = await self.mod.get_or_create_persona_for_city(
            conn, None, {}
        )
        self.assertIsNone(pid)
        self.assertFalse(was_created)

    # ========================================================================
    # Test 6: PPTC is idempotent (same city returns same persona)
    # ========================================================================
    async def test_idempotent_same_city(self):
        """Two sequential calls for the same city return the same persona."""
        # First call creates a new persona
        conn1 = self._make_conn(existing_persona=None)
        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid1, _ = await self.mod.get_or_create_persona_for_city(
                conn1, "Houston", {"signup_city": "Houston"}
            )
        # Compute the ID that was generated (would be reused on second call)
        # Note: city_persona uses time.time() in the TTL bucket, so two
        # calls within the same bucket should produce the same ID.
        # Second call: the existing persona is the one we just created
        conn2 = self._make_conn(existing_persona=pid1,
                                  persona_state='active', task_count=0)
        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid2, was_created2 = await self.mod.get_or_create_persona_for_city(
                conn2, "Houston", {"signup_city": "Houston"}
            )
        # Should reuse the same persona
        self.assertEqual(pid1, pid2)
        self.assertFalse(was_created2)

    # ========================================================================
    # Test 7: City normalization
    # ========================================================================
    async def test_city_normalization(self):
        """City names are normalized consistently (case, abbreviations)."""
        # Same city with different formatting should resolve to the same
        # normalized name and therefore the same persona.
        cases = [
            ("  DALLAS  ", "dallas"),
            ("Dallas", "dallas"),
            ("DALLAS", "dallas"),
            ("St. Petersburg", "saint petersburg"),
            ("St.Petersburg", "saint petersburg"),
            ("NYC", "new york city"),
            ("LA", "los angeles"),
            ("SF", "san francisco"),
            ("new york", "new york"),
            ("Mt. Vernon", "mount vernon"),
            ("Mt.Vernon", "mount vernon"),
            ("", ""),
        ]
        for input_city, expected_normalized in cases:
            actual = self.mod._normalize_city(input_city)
            self.assertEqual(
                actual, expected_normalized,
                f"_normalize_city({input_city!r}) = {actual!r}, expected {expected_normalized!r}"
            )

    async def test_city_normalization_idempotent(self):
        """_normalize_city is idempotent: normalize(normalize(x)) == normalize(x)."""
        # An operator may paste the same city in different forms;
        # both should resolve to the same persona.
        inputs = [
            "Dallas", "DALLAS", "dallas", "  Dallas  ",
            "St. Petersburg", "St.Petersburg", "st. petersburg",
            "NYC", "nyc", "new york city", "New York City",
        ]
        for inp in inputs:
            first = self.mod._normalize_city(inp)
            second = self.mod._normalize_city(first)
            self.assertEqual(
                first, second,
                f"_normalize_city is not idempotent for {inp!r}: "
                f"first={first!r}, second={second!r}"
            )

    # ========================================================================
    # Test 8: City name extraction from various payload formats
    # ========================================================================
    async def test_city_extraction_formats(self):
        """_extract_city_from_payload reads from various field formats."""
        cases = [
            ({"signup_city": "Dallas"}, "dallas"),
            ({"target_city": "Houston"}, "houston"),
            ({"city": "Austin"}, "austin"),
            ({"geo_city": "Chicago"}, "chicago"),
            ({"geo": {"city": "Phoenix"}}, "phoenix"),
            ({"random_field": "ignored"}, None),  # no city field
            ({"signup_city": ""}, None),  # empty string skipped
        ]
        for payload, expected in cases:
            actual = self.mod._extract_city_from_payload(payload)
            self.assertEqual(
                actual, expected,
                f"_extract_city_from_payload({payload!r}) = {actual!r}, expected {expected!r}"
            )

    # ========================================================================
    # Test 9: Banned persona state triggers new persona
    # ========================================================================
    async def test_banned_state_triggers_new(self):
        """A 'banned' persona in a city triggers a fresh persona."""
        old = "ppc-banned-banned"
        conn = self._make_conn(existing_persona=old,
                                persona_state='banned', task_count=5)

        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "Dallas", {"signup_city": "Dallas"}
            )
        self.assertTrue(was_created)
        self.assertNotEqual(pid, old)

    # ========================================================================
    # Test 10: Rotated persona state triggers new persona
    # ========================================================================
    async def test_rotated_state_triggers_new(self):
        """A 'rotated' persona in a city triggers a fresh persona."""
        old = "ppc-rotated-rotated"
        conn = self._make_conn(existing_persona=old,
                                persona_state='rotated', task_count=10)

        with patch.object(self.mod, 'PERSISTENT_PERSONA_ENABLED', True):
            pid, was_created = await self.mod.get_or_create_persona_for_city(
                conn, "Dallas", {"signup_city": "Dallas"}
            )
        self.assertTrue(was_created)
        self.assertNotEqual(pid, old)

    # ========================================================================
    # Test 11: list_persistent_personas returns active mappings
    # ========================================================================
    async def test_list_persistent_personas(self):
        """list_persistent_personas queries the persona_by_city table."""
        rows = [
            {
                "city": "dallas",
                "persona_id": "ppc-aaaa-bbbb",
                "created_at": "2026-01-01",
                "last_used": "2026-01-15",
                "task_count": 5,
                "state": "active",
            },
            {
                "city": "houston",
                "persona_id": "ppc-cccc-dddd",
                "created_at": "2026-01-02",
                "last_used": "2026-01-14",
                "task_count": 3,
                "state": "active",
            },
        ]
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)
        result = await self.mod.list_persistent_personas(conn)
        # Should return a list of dicts
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['city'], 'dallas')
        self.assertEqual(result[1]['city'], 'houston')
        # The query should be ordered by last_used DESC
        call_args = conn.fetch.call_args
        self.assertIn('ORDER BY pbc.last_used DESC', call_args[0][0])

    # ========================================================================
    # Test 12: retire_persistent_persona removes the mapping
    # ========================================================================
    async def test_retire_persistent_persona(self):
        """retire_persistent_persona returns True if a row was removed."""
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="DELETE 1")
        result = await self.mod.retire_persistent_persona(conn, "Dallas")
        self.assertTrue(result)

        # No row removed
        conn.execute = AsyncMock(return_value="DELETE 0")
        result = await self.mod.retire_persistent_persona(conn, "Dallas")
        self.assertFalse(result)

    # ========================================================================
    # Test 13: get_persistent_persona_id is read-only
    # ========================================================================
    async def test_get_persistent_persona_id_readonly(self):
        """get_persistent_persona_id is a read-only lookup."""
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={
            "persona_id": "ppc-readonly-readonly"
        })
        result = await self.mod.get_persistent_persona_id(conn, "Dallas")
        self.assertEqual(result, "ppc-readonly-readonly")
        # No execute() should be called (read-only)
        conn.execute.assert_not_called()

        # When no mapping exists
        conn.fetchrow = AsyncMock(return_value=None)
        result = await self.mod.get_persistent_persona_id(conn, "Unknown")
        self.assertIsNone(result)


class TestPPTCSchemaIntegrity(unittest.TestCase):
    """Verify the persona_by_city schema matches the SQL definition."""

    @classmethod
    def setUpClass(cls):
        with open('/home/user/project/database/schema.sql') as f:
            cls.schema = f.read()

    def test_persona_by_city_columns(self):
        """The persona_by_city table has the expected columns."""
        # Extract the CREATE TABLE block for persona_by_city
        m = re.search(
            r'CREATE TABLE persona_by_city \((.*?)\);',
            self.schema, re.DOTALL
        )
        self.assertIsNotNone(m, "persona_by_city table not found in schema")
        cols_block = m.group(1)
        for col in ['city', 'persona_id', 'created_at', 'last_used', 'task_count']:
            self.assertIn(col, cols_block, f"Column {col} missing")

    def test_primary_key(self):
        """city is the PRIMARY KEY."""
        m = re.search(
            r'CREATE TABLE persona_by_city \((.*?)\);',
            self.schema, re.DOTALL
        )
        self.assertIn('PRIMARY KEY', m.group(1))

    def test_foreign_key(self):
        """persona_id REFERENCES personas(id) ON DELETE CASCADE."""
        m = re.search(
            r'CREATE TABLE persona_by_city \((.*?)\);',
            self.schema, re.DOTALL
        )
        self.assertIn('REFERENCES personas(id) ON DELETE CASCADE', m.group(1))

    def test_personas_task_count_column(self):
        """The personas table has a task_count column (added in R7-DEEP-AUDIT-2)."""
        # The migration ALTER TABLE statement should add task_count
        self.assertIn(
            'ALTER TABLE personas ADD COLUMN IF NOT EXISTS task_count',
            self.schema,
            "personas.task_count migration missing"
        )


class TestPPTCDeterminism(unittest.TestCase):
    """Verify that the same inputs produce the same persona_id."""

    def setUp(self):
        """Import the city_persona module."""
        for mod_name in list(sys.modules.keys()):
            if 'city_persona' in mod_name:
                del sys.modules[mod_name]
        self.mod = __import__('city_persona')

    def test_generate_persona_id_deterministic(self):
        """_generate_persona_id returns the same value for the same city+salt."""
        ids = set()
        for _ in range(10):
            pid = self.mod._generate_persona_id("dallas", "salt_v1")
            ids.add(pid)
        # All 10 calls should return the same ID (within the same TTL bucket)
        self.assertEqual(len(ids), 1, "persona_id is not deterministic")

    def test_generate_persona_id_format(self):
        """The persona_id has the ppc-<8hex>-<8hex> format."""
        pid = self.mod._generate_persona_id("dallas", "")
        self.assertRegex(pid, r"^ppc-[a-f0-9]{8}-[a-f0-9]{8}$")

    def test_generate_persona_id_salt_changes(self):
        """Different salts produce different persona_ids for the same city."""
        id1 = self.mod._generate_persona_id("dallas", "salt_v1")
        id2 = self.mod._generate_persona_id("dallas", "salt_v2")
        self.assertNotEqual(id1, id2,
                            "Different salts should produce different IDs")

    def test_compute_socks5_url_uses_socks5(self):
        """_compute_socks5_url returns a SOCKS5 URL on port 1081-1085."""
        url = self.mod._compute_socks5_url("ppc-test-test")
        self.assertIn("socks5h://172.18.0.5:", url)
        port = int(url.split(":")[-1])
        self.assertIn(port, [1081, 1082, 1083, 1084, 1085])

    def test_signup_geo_for_city(self):
        """_signup_geo_for_city returns the 2-letter state code for known cities."""
        self.assertEqual(self.mod._signup_geo_for_city("dallas"), "tx")
        self.assertEqual(self.mod._signup_geo_for_city("los angeles"), "ca")
        self.assertEqual(self.mod._signup_geo_for_city("new york"), "ny")
        # Unknown city returns the city name capitalized
        self.assertEqual(self.mod._signup_geo_for_city("atlantis"), "Atlantis")


if __name__ == '__main__':
    print("="*70)
    print("Pass 59: PPTC End-to-End Integration Tests")
    print("="*70)

    # Run unit tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)

    print("="*70)
    print("✓ All Pass 59 integration tests passed.")
    print("="*70)
