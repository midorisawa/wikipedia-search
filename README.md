# Wikipedia日本語版 SQLiteアーカイブ

Wikimedia Foundationが公開しているWikipedia日本語版のXMLダンプを取得し、各ページの最新版（現行版）をSQLiteデータベースへ格納するためのローカルツールです。

## 収録範囲

- **標準名前空間の記事**
- **全名前空間の現行ページ**: ノート、利用者、Wikipedia、ファイル説明、テンプレート、カテゴリなど
- **メタデータ**: ページID、名前空間、タイトル、リダイレクト先
- **最新リビジョン情報**: リビジョンID、投稿日時、投稿者、コメント、コンテンツモデル、SHA-1
- **本文**: ダンプに収録されている形式（Wikitextなど）の本文データ

※ 編集履歴、削除済みページ、画像や音声などのメディア本体、全文検索用インデックスは**収録していません**。

## 構築手順

本ツールの実行には、Pythonパッケージマネージャーの `uv` を使用します。

### ダンプのダウンロードと構築

例として、2026年7月1日版のダンプを取得して構築する場合は次のように実行します。

```powershell
uv run python .\jawiki_sqlite.py build --date 20260701 --database .\jawiki.sqlite
```

- ダウンロードが中断された場合でも、`data` ディレクトリ内の `.part` ファイルから再開できます。
- 取得完了後、Wikimedia公式のSHA-1ハッシュ値を用いてファイルの整合性を検証します。
- データベースは変換処理中に一時ファイル（`jawiki.sqlite.partial`）として書き出され、完了時に正式なファイル名へ変更されます。その後、総ページ数の検証および名前空間別の集計表示が行われます。

### 既存のダンプファイルから取り込む場合

すでに手元にダンプファイルがある場合は、`import` コマンドで変換できます。

```powershell
uv run python .\jawiki_sqlite.py import .\data\jawiki-20260701-pages-meta-current.xml.bz2 .\jawiki.sqlite
```

## データの更新

プロジェクトのルートディレクトリで次のコマンドを実行すると、Wikimediaの最新ダンプを確認し、新しい版が公開されていれば自動で `jawiki.sqlite` を更新します（データベースが存在しない場合は新規作成します）。

```powershell
uv run python .\jawiki_sqlite.py update
```

更新時は `data` ディレクトリに最新ダンプを取得し、別の場所で新しいデータベースを一時的に構築します。ページ件数の検証が完了した後に、既存の `jawiki.sqlite` と安全に差し替えます。

> **注意:** 差し替えが完了するまでは新旧両方のDBとダンプファイルが保持されるため、十分なディスク空き容量を確保してください。また、更新実行前にDBファイルを開いている他のアプリケーションを閉じてください。

- 別のDBファイルを指定する場合: `--database`
- ダンプの保存先を指定する場合: `--output-dir`

## 利用方法

### コマンドラインからの検索

**記事「日本」を完全一致で取得する:**

```powershell
uv run python .\jawiki_sqlite.py get .\jawiki.sqlite 日本
```

**別の名前空間のページを取得する:**

`--namespace` オプションで名前空間番号を指定し、プレフィックスを含めたタイトルを渡します（例：「ノート:日本」の名前空間番号は `1`）。

```powershell
uv run python .\jawiki_sqlite.py get .\jawiki.sqlite 'ノート:日本' --namespace 1
```

※名前空間の一覧と番号については、[日本語版Wikipediaの「Help:名前空間」](https://ja.wikipedia.org/wiki/Help:%E5%90%8D%E5%89%8D%E7%A9%BA%E9%96%93)を参照してください。

> **本文検索について:**
>
> SQLの `LIKE` 句などで本文を検索することは可能ですが、全文検索用のインデックスを用意していないため、全ページを対象にすると本文を1件ずつ調べる全件スキャンとなり時間がかかります（名前空間やタイトルであらかじめ候補を絞り込む場合は、既存のインデックスを活用できる場合があります）。

### データベースの検証

ページ件数および名前空間別の集計を確認します。

```powershell
uv run python .\jawiki_sqlite.py verify .\jawiki.sqlite
```

本文データ領域まで含めた物理的な整合性検査を行う場合は `--full` を指定してください（23 GiB規模のDBでは数時間かかる場合があります）。

```powershell
uv run python .\jawiki_sqlite.py verify .\jawiki.sqlite --full
```

### Pythonからの利用例

```python
import sqlite3

connection = sqlite3.connect("jawiki.sqlite")
row = connection.execute(
    "SELECT page_id, revision_id, timestamp, text "
    "FROM pages WHERE namespace = ? AND title = ?",
    (0, "日本"),
).fetchone()
```

## 主なテーブル構造

- **`pages`**: 各ページの最新データを格納
- **`namespaces`**: 名前空間の定義情報を格納
- **`metadata`**: 構築・更新情報を保持（`build` / `update` 実行時は取得元ダンプのURLや最終更新日時、`import` 実行時は元ファイル名や取り込み日時など）

※ タイトルの完全一致検索を高速に行うため、`pages(namespace, title)` に複合インデックスが貼られています。

## ライセンス

本ツール本体とWikimediaのダンプコンテンツは扱いが異なります。

ダンプされたコンテンツを再利用・再配布する場合は、Wikipedia各ページのライセンス表記および[Wikimedia利用規約](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use/ja)をご確認ください。

- ダンプ配布元: <https://dumps.wikimedia.org/jawiki/>
