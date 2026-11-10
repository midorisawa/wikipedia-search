# Wikipedia日本語版SQLiteアーカイブ

Wikimedia Foundationが公開するWikipedia日本語版のXMLダンプを取得し、各ページの現行版をSQLiteへ格納するためのローカルツール。

## 収録範囲

- 標準名前空間の記事
- ノート、利用者、Wikipedia、ファイル説明、テンプレート、カテゴリなど、全名前空間の現行ページ
- ページID、名前空間、タイトル、リダイレクト先
- 最新リビジョンのID、日時、投稿者、コメント、モデル、SHA-1
- Wikitextなど、ダンプに収録された形式の本文

編集履歴、削除済みページ、画像や音声などのバイナリ本体、全文検索用のインデックスは収録しない。

## 構築

Python環境には`uv`を使用する。2026年7月1日版を取得して構築する例:

```powershell
uv run python .\jawiki_sqlite.py build --date 20260701 --database .\jawiki.sqlite
```

ダウンロードは`data`ディレクトリ内の`.part`ファイルから再開できる。取得後に公式SHA-1値を照合する。`build`は変換後に総ページ数を検証し、名前空間別の件数を表示する。変換途中のデータベースは`jawiki.sqlite.partial`となり、完成時に正式名へ変更される。

既存のダンプから変換する場合:

```powershell
uv run python .\jawiki_sqlite.py import .\data\jawiki-20260701-pages-meta-current.xml.bz2 .\jawiki.sqlite
```

## 利用

記事「日本」をタイトル完全一致で取得する:

```powershell
uv run python .\jawiki_sqlite.py get .\jawiki.sqlite 日本
```

別の名前空間のページを取得するには、`--namespace`で番号を指定し、名前空間の接頭辞を含むタイトルを渡す。たとえば「ノート:日本」の名前空間番号は`1`。

```powershell
uv run python .\jawiki_sqlite.py get .\jawiki.sqlite 'ノート:日本' --namespace 1
```

名前空間の一覧と番号は[日本語版Wikipediaの「Help:名前空間」](https://ja.wikipedia.org/wiki/Help:%E5%90%8D%E5%89%8D%E7%A9%BA%E9%96%93)を参照。

本文をSQLの`LIKE`などで検索することはできる。本文検索用のインデックスがないため、全ページを対象にすると本文を1件ずつ調べる必要があり、時間がかかる。

ページ件数と名前空間別の集計を確認する:

```powershell
uv run python .\jawiki_sqlite.py verify .\jawiki.sqlite
```

本文領域まで含む物理整合性検査を行う場合は`--full`を付ける。23GiB規模のDBでは数時間かかる場合がある。

```powershell
uv run python .\jawiki_sqlite.py verify .\jawiki.sqlite --full
```

Pythonから直接利用する例:

```python
import sqlite3

connection = sqlite3.connect("jawiki.sqlite")
row = connection.execute(
    "SELECT page_id, revision_id, timestamp, text "
    "FROM pages WHERE namespace = ? AND title = ?",
    (0, "日本"),
).fetchone()
```

## 主なテーブル

`pages`がページ本体、`namespaces`が名前空間名、`metadata`が取得元と構築情報を保持する。タイトルの完全一致検索には`pages(namespace, title)`のインデックスを使う。

## ライセンス

ツール部分とWikimediaのダンプ内容は別物。ダンプ内容を再利用・再配布する場合は、Wikipedia各ページのライセンス表示と[Wikimedia利用規約](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/ja)を確認する。

配布元: <https://dumps.wikimedia.org/jawiki/>
