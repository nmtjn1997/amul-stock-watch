import tempfile
import unittest
from pathlib import Path

from amul_watch import logs as L


class LogReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "t.log"
        self.path.write_text("".join(f"line {i}\n" for i in range(1, 11)))
        self._real = L.log_files
        L.log_files = lambda: {
            "t": {"id": "t", "path": str(self.path), "label": "t", "exists": True,
                  "size": 0, "tail_cmd": "x"}
        }

    def tearDown(self) -> None:
        L.log_files = self._real
        self.dir.cleanup()

    def append(self, text: str) -> None:
        with self.path.open("a") as fh:
            fh.write(text)

    def test_crlf_offsets_are_exact(self) -> None:
        self.path.write_bytes(b"".join(f"line {i}\r\n".encode() for i in range(1, 11)))
        tail = L.read_log("t", limit=3)
        self.assertEqual(tail["lines"], ["line 8", "line 9", "line 10"])
        older = L.read_log("t", limit=3, before=tail["start"])
        self.assertEqual(older["lines"], ["line 5", "line 6", "line 7"])

    def test_tail_returns_last_n(self) -> None:
        self.assertEqual(L.read_log("t", limit=3)["lines"], ["line 8", "line 9", "line 10"])

    def test_before_window_is_contiguous_with_tail(self) -> None:
        tail = L.read_log("t", limit=3)
        older = L.read_log("t", limit=3, before=tail["start"])
        self.assertEqual(older["lines"], ["line 5", "line 6", "line 7"])
        self.assertEqual(older["end"], tail["start"])

    def test_follow_yields_every_new_line_exactly_once(self) -> None:
        tail = L.read_log("t", limit=3)
        self.append("line 11\nline 12\n")
        first = L.read_log("t", after=tail["end"])
        self.assertEqual(first["lines"], ["line 11", "line 12"])
        self.assertEqual(L.read_log("t", after=first["end"])["lines"], [])

    def test_half_written_line_is_withheld_then_delivered_whole(self) -> None:
        cursor = L.read_log("t", limit=1)["end"]
        self.append("half-writ")
        self.assertEqual(L.read_log("t", after=cursor)["lines"], [])
        self.append("ten\n")
        self.assertEqual(L.read_log("t", after=cursor)["lines"], ["half-written"])

    def test_paging_back_reconstructs_the_file(self) -> None:
        page = L.read_log("t", limit=4)
        seen = list(page["lines"])
        while page["start"] > 0:
            page = L.read_log("t", limit=4, before=page["start"])
            seen = page["lines"] + seen
        self.assertEqual(seen, self.path.read_text().splitlines())

    def test_truncation_is_reported_as_rotated(self) -> None:
        self.path.write_text("fresh\n")
        res = L.read_log("t", after=99999)
        self.assertTrue(res["rotated"])
        self.assertEqual(res["lines"], ["fresh"])


if __name__ == "__main__":
    unittest.main()
