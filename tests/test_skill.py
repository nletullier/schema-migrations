"""End-to-end checks of the schema-migrations scripts, standard library only.

    python3 -m unittest discover -s tests

They build the three example migrations in a temporary project and check the generated files. They do not
need a database: running the SQL is left to the example in the README.
"""
from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "schema-migrations"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / "scripts"))

import prepare_migration  # noqa: E402
import rdm  # noqa: E402

EXAMPLES = [
    ("CreateLibrary", "01-create-tables.json"),
    ("AddPublishers", "02-add-table-and-migrate-data.json"),
    ("DropPublisherName", "03-drop-and-rename.json"),
]


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = prepare_migration.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class SchemaMigrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="schema-migrations-test-"))
        self.cwd = Path.cwd()
        os.chdir(self.tmp)
        self.assertEqual(run("init", "--dir", "migrations")[0], 0)
        self.folders: dict[str, Path] = {}
        for i, (name, example) in enumerate(EXAMPLES):
            self.assertEqual(run("new", name)[0], 0)
            folder = next((self.tmp / "migrations").glob(f"*_{name}"))
            # `new` stamps folders by the second: make the order explicit
            stamped = folder.with_name(f"2026010100000{i}_{name}")
            folder.rename(stamped)
            for f in stamped.iterdir():
                if f.name.startswith(folder.name):
                    f.rename(stamped / f.name.replace(folder.name, stamped.name))
            shutil.copyfile(SKILL / "references" / "examples" / example, stamped / "changes.json")
            code, _, err = run("build", name)
            self.assertEqual(code, 0, err)
            self.folders[name] = stamped

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, name: str, suffix: str) -> str:
        folder = self.folders[name]
        return (folder / (suffix if not suffix.startswith(".") else folder.name + suffix)).read_text(encoding="utf-8")

    def test_every_file_is_generated(self):
        for name, folder in self.folders.items():
            for f in (f"{folder.name}.up.sql", f"{folder.name}.down.sql", "model.xml", "diff.svg",
                      "schema.svg", f"{folder.name}.md"):
                self.assertTrue((folder / f).exists(), f"{name}: {f}")

    def test_create_tables_sql(self):
        up = self.read("CreateLibrary", ".up.sql")
        self.assertIn('CREATE TABLE "author"', up)
        self.assertIn('CONSTRAINT "author_email_uk" UNIQUE ("email")', up)
        self.assertIn('"pages" int  NULL CHECK (pages > 0)', up)
        self.assertIn("ON DELETE  CASCADE", up)
        down = self.read("CreateLibrary", ".down.sql")
        self.assertLess(down.index('DROP CONSTRAINT "book_author"'), down.index('DROP TABLE "book"'))

    def test_data_runs_after_additions(self):
        up = self.read("AddPublishers", ".up.sql")
        self.assertLess(up.index('CREATE TABLE "publisher"'), up.index("INSERT INTO publisher"))
        self.assertLess(up.index('ADD "publisher_id"'), up.index("UPDATE book b SET publisher_id"))
        self.assertIn('ALTER "isbn" TYPE varchar(17)', up)
        self.assertIn("WHERE isbn IS NOT NULL", up)

    def test_drop_and_rename(self):
        up = self.read("DropPublisherName", ".up.sql")
        self.assertIn('RENAME COLUMN "name" TO "full_name"', up)
        self.assertIn('DROP COLUMN "publisher_name"', up)
        self.assertNotIn('DROP COLUMN "name"', up)
        down = self.read("DropPublisherName", ".down.sql")
        # the column comes back before the data that refills it
        self.assertLess(down.index('ADD "publisher_name"'), down.index("UPDATE book b SET publisher_name"))
        self.assertIn('RENAME COLUMN "full_name" TO "name"', down)

    def test_each_migration_starts_from_the_previous_model(self):
        model = rdm.Model.load(self.folders["DropPublisherName"] / "model.xml")
        names = {t.findtext("Name") for t in model.tables()}
        self.assertEqual(names, {"author", "book", "publisher"})

    def test_models_have_unique_ids_and_round_trip(self):
        for folder in self.folders.values():
            path = folder / "model.xml"
            model = rdm.Model.load(path)
            counts = collections.Counter(el.get("Id") for el in model.root.iter() if el.get("Id"))
            self.assertEqual([i for i, n in counts.items() if n > 1], [])
            copy = self.tmp / "copy.xml"
            model.save(copy)
            self.assertEqual(path.read_bytes(), copy.read_bytes())

    def test_diff_svg_colours_changes(self):
        svg = self.read("DropPublisherName", "diff.svg")
        self.assertIn("#cf222e", svg)       # removed
        self.assertIn("#9a6700", svg)       # modified (rename)
        self.assertIn("line-through", svg)
        clean = self.read("DropPublisherName", "schema.svg")
        self.assertNotIn("#cf222e", clean)
        self.assertNotIn("publisher_name", clean)

    def test_whats_new_note(self):
        md = self.read("AddPublishers", ".md")
        self.assertTrue(md.startswith("# Publishers become a table"))
        self.assertIn("![Schema differences](diff.svg)", md)
        self.assertIn("`book_publisher`", md)

    def test_refuses_to_rebuild_an_earlier_migration(self):
        code, _, err = run("build", "CreateLibrary")
        self.assertNotEqual(code, 0)
        self.assertIn("--force", err)

    def test_spec_errors_are_reported(self):
        spec_path = self.folders["DropPublisherName"] / "changes.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["alterTables"][0]["dropColumns"] = ["no_such_column"]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        code, _, err = run("build", "DropPublisherName")
        self.assertEqual(code, 2)
        self.assertIn("no_such_column", err)


if __name__ == "__main__":
    unittest.main()
