"""SearchEditModal: Tab-cycle + submit/cancel semantics.

Modal logic only — Textual mounting is mocked out so the test stays inside
plain unittest (no asyncio harness needed). The integration coverage that the
modal renders correctly inside SearchReviewApp is implicit in the real run;
this file pins down the state-machine behaviour.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from titleforge.prompt_ui import SEARCH_TYPE_CYCLE
from titleforge.search_review_app import SearchEditModal


class TestSearchEditModal(unittest.TestCase):
    def _make(self, initial: str = "movie") -> SearchEditModal:
        modal = SearchEditModal(
            message="Edit search for [foo.mkv]:",
            default="foo",
            initial_type=initial,  # type: ignore[arg-type]
        )
        # Replace the dismiss method with a mock so we can assert the result
        # tuple without needing a mounted screen stack.
        modal.dismiss = MagicMock()  # type: ignore[method-assign]
        return modal

    def test_initial_state_matches_initial_type(self) -> None:
        for kind in SEARCH_TYPE_CYCLE:
            m = self._make(kind)
            self.assertEqual(SEARCH_TYPE_CYCLE[m._state], kind)

    def test_cycle_type_advances_state_index(self) -> None:
        m = self._make("movie")
        # Patch query_one to a stub Label whose .update() is no-op so the action
        # can run without a mounted DOM.
        stub_label = MagicMock()
        with patch.object(m, "query_one", return_value=stub_label):
            m.action_cycle_type()
            self.assertEqual(SEARCH_TYPE_CYCLE[m._state], "tv")
            m.action_cycle_type()
            self.assertEqual(SEARCH_TYPE_CYCLE[m._state], "both")
            m.action_cycle_type()
            self.assertEqual(SEARCH_TYPE_CYCLE[m._state], "movie")
        # The label widget was updated once per cycle.
        self.assertEqual(stub_label.update.call_count, 3)

    def test_submit_with_query_returns_tuple(self) -> None:
        m = self._make("tv")
        m._submit("Sons of Anarchy")
        m.dismiss.assert_called_once_with(("tv", "Sons of Anarchy"))

    def test_submit_strips_whitespace(self) -> None:
        m = self._make("movie")
        m._submit("   Spider-Man   ")
        m.dismiss.assert_called_once_with(("movie", "Spider-Man"))

    def test_submit_empty_dismisses_with_none(self) -> None:
        m = self._make("both")
        m._submit("   ")
        m.dismiss.assert_called_once_with(None)

    def test_action_cancel_dismisses_with_none(self) -> None:
        m = self._make("movie")
        m.action_cancel()
        m.dismiss.assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()
