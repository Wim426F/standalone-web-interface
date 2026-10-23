"""Tests for scripts/release_version.py: python -m unittest discover -s scripts"""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import release_version as rv

TAGS = ["v4.1", "v4.11", "v4.9", "v4.23", "v4.2"]


class Key(unittest.TestCase):
    def test_order(self):
        order = ["v4.9", "v4.23", "v4.24-dev.1", "v4.24-dev.2", "v4.24-dev.10", "v4.24", "v4.24.1", "v4.25-dev.1", "v5.0"]
        self.assertEqual(sorted(reversed(order), key=rv.key), order)

    def test_rejects_other_tags(self):
        for t in ["", "4.24", "v4", "v4.24-rc1", "v4.24-dev.0", "v4.24-dev.", "v4.24-3-gabc", "x v4.24", "v4.24-dev.1-dirty"]:
            self.assertIsNone(rv.key(t), t)


class Stable(unittest.TestCase):
    def test_minor_plus_one_numeric_not_lexical(self):
        self.assertEqual(rv.next_stable(TAGS), "v4.24")

    def test_no_tags(self):
        self.assertEqual(rv.next_stable([]), "v0.1")

    def test_dev_tags_never_count(self):
        # previews of v4.24 (and even a stray later one) must not bump stable
        self.assertEqual(rv.next_stable(TAGS + ["v4.24-dev.1", "v4.24-dev.7", "v4.30-dev.1"]), "v4.24")

    def test_head_stable_tag_wins(self):
        self.assertEqual(rv.stable_version(TAGS, ["v4.23"]), "v4.23")

    def test_head_dev_tag_ignored(self):
        # dev fast-forwarded into main: HEAD carries the last preview's tag
        self.assertEqual(rv.stable_version(TAGS + ["v4.24-dev.3"], ["v4.24-dev.3"]), "v4.24")


class Dev(unittest.TestCase):
    def test_first_preview(self):
        self.assertEqual(rv.dev_version(TAGS, []), "v4.24-dev.1")

    def test_counts_up_numerically(self):
        self.assertEqual(rv.dev_version(TAGS + ["v4.24-dev.9", "v4.24-dev.10", "v4.24-dev.2"], []), "v4.24-dev.11")

    def test_restarts_after_stable(self):
        tags = TAGS + ["v4.24-dev.1", "v4.24-dev.2", "v4.24"]
        self.assertEqual(rv.dev_version(tags, []), "v4.25-dev.1")

    def test_rerun_reuses_head_tag(self):
        self.assertEqual(rv.dev_version(TAGS + ["v4.24-dev.4"], ["v4.24-dev.4"]), "v4.24-dev.4")

    def test_head_already_stable_is_nothing_new(self):
        self.assertEqual(rv.dev_version(TAGS, ["v4.23"]), "")


class Previous(unittest.TestCase):
    tags = TAGS + ["v4.24-dev.1", "v4.24-dev.2"]

    def test_stable_skips_previews(self):
        self.assertEqual(rv.previous(self.tags, "v4.24"), "v4.23")

    def test_dev_follows_newest(self):
        self.assertEqual(rv.previous(self.tags, "v4.24-dev.3"), "v4.24-dev.2")
        self.assertEqual(rv.previous(TAGS, "v4.24-dev.1"), "v4.23")

    def test_none(self):
        self.assertEqual(rv.previous([], "v0.1"), "")
        self.assertEqual(rv.previous(TAGS, "garbage"), "")


class Prune(unittest.TestCase):
    def rows(self):
        rows = [{"tagName": f"v4.24-dev.{n}", "isPrerelease": True, "isDraft": False} for n in range(1, 9)]
        rows += [
            {"tagName": "v4.23", "isPrerelease": False, "isDraft": False},
            {"tagName": "v4.22", "isPrerelease": False, "isDraft": False},
            {"tagName": "v4.25-dev.1", "isPrerelease": True, "isDraft": True},     # draft
            {"tagName": "v4.24-dev.99", "isPrerelease": False, "isDraft": False},  # not a pre-release
            {"tagName": "nightly", "isPrerelease": True, "isDraft": False},        # other scheme
        ]
        return rows

    def test_keeps_newest_five_dev_only(self):
        self.assertEqual(rv.prune(self.rows()), ["v4.24-dev.1", "v4.24-dev.2", "v4.24-dev.3"])

    def test_keep_extra(self):
        self.assertEqual(rv.prune(self.rows(), keep=["v4.24-dev.2"]), ["v4.24-dev.1", "v4.24-dev.3"])

    def test_cli(self):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(self.rows()))), redirect_stdout(io.StringIO()) as out:
            rv.main(["prune", "--count", "7"])
        self.assertEqual(out.getvalue().split(), ["v4.24-dev.1"])


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        with redirect_stdout(io.StringIO()) as out:
            rv.main(list(argv))
        return out.getvalue().strip()

    def test_commands(self):
        tags = ",".join(TAGS + ["v4.24-dev.1"])
        self.assertEqual(self.run_cli("stable", "--tags", tags, "--head-tags", ""), "v4.24")
        self.assertEqual(self.run_cli("dev", "--tags", tags, "--head-tags", ""), "v4.24-dev.2")
        self.assertEqual(self.run_cli("previous", "v4.24", "--tags", tags), "v4.23")


if __name__ == "__main__":
    unittest.main()
