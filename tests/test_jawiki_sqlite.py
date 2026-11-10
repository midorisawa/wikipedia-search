import bz2
import sqlite3
import tempfile
import unittest
from pathlib import Path

import jawiki_sqlite


FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <siteinfo>
    <sitename>Wikipedia</sitename><dbname>jawiki</dbname>
    <base>https://ja.wikipedia.org/wiki/メインページ</base>
    <generator>MediaWiki 1.45</generator><case>first-letter</case>
    <namespaces>
      <namespace key="0" case="first-letter" />
      <namespace key="10" case="first-letter" canonical="Template">テンプレート</namespace>
    </namespaces>
  </siteinfo>
  <page>
    <title>日本</title><ns>0</ns><id>1</id>
    <revision>
      <id>101</id><parentid>100</parentid><timestamp>2026-07-01T00:00:00Z</timestamp>
      <contributor><username>利用者A</username><id>5</id></contributor>
      <comment>更新</comment><model>wikitext</model><format>text/x-wiki</format>
      <text bytes="12">'''日本'''の本文</text><sha1>abc</sha1>
    </revision>
  </page>
  <page>
    <title>転送</title><ns>0</ns><id>2</id><redirect title="日本" />
    <revision>
      <id>102</id><timestamp>2026-07-01T00:01:00Z</timestamp>
      <contributor><ip>127.0.0.1</ip></contributor><minor />
      <model>wikitext</model><format>text/x-wiki</format>
      <text>#REDIRECT [[日本]]</text><sha1>def</sha1>
    </revision>
  </page>
</mediawiki>
"""


class ImportTest(unittest.TestCase):
    def test_import_query_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump_path = root / "sample.xml.bz2"
            database_path = root / "sample.sqlite"
            with bz2.open(dump_path, "wb") as destination:
                destination.write(FIXTURE.encode("utf-8"))

            jawiki_sqlite.import_dump(dump_path, database_path, batch_size=1)
            summary = jawiki_sqlite.verify_database(database_path, full=True)
            self.assertEqual(summary["quick_check"], "ok")
            self.assertEqual(summary["page_count"], 2)
            self.assertEqual(summary["redirect_count"], 1)

            connection = sqlite3.connect(database_path)
            row = connection.execute(
                "SELECT title, contributor_name, text FROM pages WHERE page_id = 1"
            ).fetchone()
            namespaces = connection.execute(
                "SELECT id, name FROM namespaces ORDER BY id"
            ).fetchall()
            connection.close()
            self.assertEqual(row, ("日本", "利用者A", "'''日本'''の本文"))
            self.assertEqual(namespaces, [(0, ""), (10, "テンプレート")])


if __name__ == "__main__":
    unittest.main()
