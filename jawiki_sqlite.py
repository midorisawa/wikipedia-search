from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path


USER_AGENT = "jawiki-sqlite-builder/0.1 (local Wikimedia archive)"
CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_BATCH_SIZE = 5_000


SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE namespaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    canonical TEXT,
    case_rule TEXT
);

CREATE TABLE pages (
    page_id INTEGER PRIMARY KEY,
    namespace INTEGER NOT NULL,
    title TEXT NOT NULL,
    redirect_title TEXT,
    revision_id INTEGER NOT NULL,
    parent_revision_id INTEGER,
    timestamp TEXT NOT NULL,
    contributor_id INTEGER,
    contributor_name TEXT,
    minor INTEGER NOT NULL,
    comment TEXT,
    model TEXT,
    format TEXT,
    sha1 TEXT,
    text_bytes INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""


INDEXES = """
CREATE UNIQUE INDEX pages_namespace_title ON pages(namespace, title);
CREATE UNIQUE INDEX pages_revision_id ON pages(revision_id);
ANALYZE;
"""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def human_bytes(value: int | float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024
    raise AssertionError("到達不能")


def request(url: str, *, start: int | None = None, method: str | None = None):
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if start is not None:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.Request(url, headers=headers, method=method)


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(request(url), timeout=60) as response:
        return response.read().decode("utf-8")


def resolve_dump(date: str) -> dict[str, object]:
    if date != "latest" and re.fullmatch(r"[0-9]{8}", date) is None:
        raise ValueError("ダンプ日付はYYYYMMDDまたはlatestで指定する")
    base_url = f"https://dumps.wikimedia.org/jawiki/{date}/"
    sums_name = f"jawiki-{date}-sha1sums.txt"
    sums = fetch_text(base_url + sums_name)
    for line in sums.splitlines():
        digest, _, name = line.partition("  ")
        match = re.fullmatch(r"jawiki-([0-9]{8})-pages-meta-current\.xml\.bz2", name)
        if match is not None and (date == "latest" or date == match[1]):
            date = match[1]
            dump_name = name
            expected_sha1 = digest
            break
    else:
        raise RuntimeError(f"SHA-1一覧に現行版のダンプがない: {sums_name}")

    base_url = f"https://dumps.wikimedia.org/jawiki/{date}/"
    time.sleep(1)
    head = urllib.request.urlopen(request(base_url + dump_name, method="HEAD"), timeout=60)
    with head:
        size_header = head.headers.get("Content-Length")
        last_modified = head.headers.get("Last-Modified")
    if size_header is None:
        raise RuntimeError("ダンプのContent-Lengthを取得できない")
    return {
        "date": date,
        "filename": dump_name,
        "url": base_url + dump_name,
        "size": int(size_header),
        "sha1": expected_sha1,
        "last_modified": last_modified,
    }


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    processed = 0
    total = path.stat().st_size
    last_report = time.monotonic()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            processed += len(chunk)
            now = time.monotonic()
            if now - last_report >= 30:
                print(f"チェックサム: {processed / total:.1%} ({human_bytes(processed)})", flush=True)
                last_report = now
    return digest.hexdigest()


def download_dump(info: dict[str, object], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / str(info["filename"])
    partial_path = final_path.with_suffix(final_path.suffix + ".part")
    expected_size = int(info["size"])
    expected_sha1 = str(info["sha1"])

    if final_path.exists():
        if final_path.stat().st_size == expected_size and sha1_file(final_path) == expected_sha1:
            print(f"取得済みファイルを再利用: {final_path}")
            return final_path
        raise RuntimeError(f"検証に失敗した既存ファイルがある: {final_path}")

    downloaded = partial_path.stat().st_size if partial_path.exists() else 0
    if downloaded > expected_size:
        raise RuntimeError(f"部分ファイルが配布元より大きい: {partial_path}")
    if downloaded < expected_size or not partial_path.exists():
        print(
            f"ダウンロード開始: {human_bytes(expected_size)}"
            + (f"（{human_bytes(downloaded)}から再開）" if downloaded else ""),
            flush=True,
        )
        time.sleep(1)
        req = request(str(info["url"]), start=downloaded if downloaded else None)
        with urllib.request.urlopen(req, timeout=120) as response:
            if downloaded and response.status != 206:
                raise RuntimeError("配布元がRange要求を受理しなかったため、安全のため中断")
            mode = "ab" if downloaded else "wb"
            started = time.monotonic()
            last_report = started
            start_bytes = downloaded
            with partial_path.open(mode) as destination:
                while chunk := response.read(CHUNK_SIZE):
                    destination.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 30:
                        elapsed = max(now - started, 0.001)
                        speed = (downloaded - start_bytes) / elapsed
                        print(
                            f"ダウンロード: {downloaded / expected_size:.1%} "
                            f"({human_bytes(downloaded)}/{human_bytes(expected_size)}, {human_bytes(speed)}/s)",
                            flush=True,
                        )
                        last_report = now
    if downloaded != expected_size:
        raise RuntimeError(f"サイズ不一致: 期待値{expected_size}、実測値{downloaded}")
    print("SHA-1を検証中...", flush=True)
    actual_sha1 = sha1_file(partial_path)
    if actual_sha1 != expected_sha1:
        raise RuntimeError(f"SHA-1不一致: 期待値{expected_sha1}、実測値{actual_sha1}")
    partial_path.replace(final_path)
    print(f"ダウンロード完了: {final_path}", flush=True)
    return final_path


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def direct_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def child_text(element: ET.Element | None, name: str, default: str | None = None) -> str | None:
    if element is None:
        return default
    child = direct_child(element, name)
    if child is None or child.text is None:
        return default
    return child.text


def integer(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None


def revision_content(revision: ET.Element) -> tuple[str | None, str | None, str]:
    model = child_text(revision, "model")
    content_format = child_text(revision, "format")
    text_node = direct_child(revision, "text")
    if text_node is not None:
        return model, content_format, text_node.text or ""

    slots = direct_child(revision, "slots")
    if slots is not None:
        selected = None
        for slot in slots:
            if local_name(slot.tag) != "slot":
                continue
            if selected is None or slot.attrib.get("role") == "main":
                selected = slot
            if slot.attrib.get("role") == "main":
                break
        if selected is not None:
            model = child_text(selected, "model", model)
            content_format = child_text(selected, "format", content_format)
            text_node = direct_child(selected, "text")
            return model, content_format, text_node.text if text_node is not None and text_node.text else ""
    return model, content_format, ""


def parse_page(page: ET.Element) -> tuple[object, ...]:
    revisions = [child for child in page if local_name(child.tag) == "revision"]
    if len(revisions) != 1:
        raise ValueError("各ページにリビジョンが1件必要。現行版のダンプを指定する")
    revision = revisions[0]
    contributor = direct_child(revision, "contributor")
    contributor_name = child_text(contributor, "username") or child_text(contributor, "ip")
    redirect = direct_child(page, "redirect")
    model, content_format, text = revision_content(revision)
    return (
        integer(child_text(page, "id")),
        integer(child_text(page, "ns")),
        child_text(page, "title", ""),
        redirect.attrib.get("title") if redirect is not None else None,
        integer(child_text(revision, "id")),
        integer(child_text(revision, "parentid")),
        child_text(revision, "timestamp", ""),
        integer(child_text(contributor, "id")),
        contributor_name,
        1 if direct_child(revision, "minor") is not None else 0,
        child_text(revision, "comment"),
        model,
        content_format,
        child_text(revision, "sha1"),
        len(text.encode("utf-8")),
        text,
    )


def read_siteinfo(siteinfo: ET.Element) -> tuple[dict[str, str], list[tuple[int, str, str | None, str | None]]]:
    metadata: dict[str, str] = {}
    for key in ("sitename", "dbname", "base", "generator", "case"):
        value = child_text(siteinfo, key)
        if value is not None:
            metadata[f"siteinfo.{key}"] = value
    namespaces: list[tuple[int, str, str | None, str | None]] = []
    container = direct_child(siteinfo, "namespaces")
    if container is not None:
        for namespace in container:
            if local_name(namespace.tag) != "namespace":
                continue
            namespaces.append(
                (
                    int(namespace.attrib["key"]),
                    namespace.text or "",
                    namespace.attrib.get("canonical"),
                    namespace.attrib.get("case"),
                )
            )
    return metadata, namespaces


def import_dump(
    dump_path: Path,
    database_path: Path,
    *,
    source_info: dict[str, object] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    restart: bool = False,
) -> None:
    partial_path = database_path.with_suffix(database_path.suffix + ".partial")
    source_size = dump_path.stat().st_size
    if partial_path.resolve() == dump_path.resolve():
        raise ValueError("ダンプと未完成DBに同じパスは使えない")
    if database_path.exists():
        raise FileExistsError(f"出力先が既に存在する: {database_path}")
    if partial_path.exists():
        if not restart:
            raise FileExistsError(
                f"前回の未完成DBが存在する: {partial_path}（破棄してやり直す場合は--restart）"
            )
        partial_path.unlink()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(partial_path)
    connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;")
    connection.executescript(SCHEMA)
    metadata = {
        "schema_version": "1",
        "source_file": dump_path.name,
        "source_size": str(source_size),
        "import_started_at": utc_now(),
    }
    if source_info:
        metadata.update(
            {
                "source_url": str(source_info.get("url", "")),
                "source_sha1": str(source_info.get("sha1", "")),
                "source_last_modified": str(source_info.get("last_modified", "")),
            }
        )
    connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())
    connection.commit()

    insert_sql = """
        INSERT INTO pages(
            page_id, namespace, title, redirect_title, revision_id, parent_revision_id,
            timestamp, contributor_id, contributor_name, minor, comment, model, format,
            sha1, text_bytes, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    batch: list[tuple[object, ...]] = []
    count = 0
    text_bytes = 0
    redirect_count = 0
    started = time.monotonic()
    last_report = started

    try:
        with bz2.open(dump_path, "rb") as source:
            root: ET.Element | None = None
            for event, element in ET.iterparse(source, events=("start", "end")):
                if root is None:
                    root = element
                    if local_name(root.tag) != "mediawiki":
                        raise ValueError("MediaWikiのXMLダンプを指定する")
                if event != "end":
                    continue
                name = local_name(element.tag)
                if name == "siteinfo":
                    site_metadata, namespaces = read_siteinfo(element)
                    connection.executemany(
                        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", site_metadata.items()
                    )
                    connection.executemany(
                        "INSERT OR REPLACE INTO namespaces(id, name, canonical, case_rule) VALUES (?, ?, ?, ?)",
                        namespaces,
                    )
                    element.clear()
                elif name == "page":
                    row = parse_page(element)
                    batch.append(row)
                    count += 1
                    text_bytes += int(row[-2])
                    redirect_count += row[3] is not None
                    element.clear()
                    if root is not None:
                        # 処理済みpageへのルート側参照を外して一定メモリで解析する
                        root.clear()
                    if len(batch) >= batch_size:
                        connection.executemany(insert_sql, batch)
                        connection.commit()
                        batch.clear()
                    now = time.monotonic()
                    if now - last_report >= 30:
                        rate = count / max(now - started, 0.001)
                        print(
                            f"変換: {count:,}ページ、本文{human_bytes(text_bytes)}、{rate:,.0f}ページ/秒",
                            flush=True,
                        )
                        last_report = now
        if count == 0:
            raise ValueError("ダンプにページがない")
        if batch:
            connection.executemany(insert_sql, batch)
            connection.commit()
        print("タイトルのインデックスを構築中...", flush=True)
        connection.executescript(INDEXES)
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('import_completed_at', ?)", (utc_now(),)
        )
        connection.execute("INSERT INTO metadata(key, value) VALUES ('page_count', ?)", (str(count),))
        connection.execute("INSERT INTO metadata(key, value) VALUES ('text_bytes', ?)", (str(text_bytes),))
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('redirect_count', ?)", (str(redirect_count),)
        )
        connection.commit()
        connection.close()
        partial_path.replace(database_path)
    except BaseException:
        connection.close()
        raise
    elapsed = time.monotonic() - started
    print(
        f"変換完了: {count:,}ページ、本文{human_bytes(text_bytes)}、"
        f"DB {human_bytes(database_path.stat().st_size)}、{elapsed / 60:.1f}分",
        flush=True,
    )


def database_summary(connection: sqlite3.Connection, *, full: bool = False) -> dict[str, object]:
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    page_count = connection.execute("SELECT count(*) FROM pages").fetchone()[0]
    text_bytes = int(metadata["text_bytes"])
    redirects = integer(metadata.get("redirect_count"))
    if full:
        actual_text_bytes, redirects = connection.execute(
            "SELECT coalesce(sum(text_bytes), 0), count(redirect_title) FROM pages"
        ).fetchone()
        if actual_text_bytes != text_bytes:
            raise RuntimeError("metadataの本文バイト数と実際の値が一致しない")
    namespace_counts = connection.execute(
        """
        SELECT p.namespace, coalesce(n.name, ''), count(*)
        FROM pages AS p LEFT JOIN namespaces AS n ON n.id = p.namespace
        GROUP BY p.namespace ORDER BY p.namespace
        """
    ).fetchall()
    return {
        "page_count": page_count,
        "text_bytes": text_bytes,
        "redirect_count": redirects,
        "namespace_counts": namespace_counts,
    }


def verify_database(database_path: Path, *, full: bool = False) -> dict[str, object]:
    uri = database_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0] if full else "省略"
    summary = database_summary(connection, full=full)
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    connection.close()
    if full and quick_check != "ok":
        raise RuntimeError(f"SQLite quick_check失敗: {quick_check}")
    if int(metadata.get("page_count", "-1")) != summary["page_count"]:
        raise RuntimeError("metadataのページ数と実際のページ数が一致しない")
    return {"quick_check": quick_check, "metadata": metadata, **summary}


def query_title(database_path: Path, title: str, namespace: int) -> None:
    uri = database_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM pages WHERE namespace = ? AND title = ?", (namespace, title)
    ).fetchone()
    connection.close()
    if row is None:
        raise SystemExit(f"該当ページなし: namespace={namespace}, title={title}")
    metadata = {key: row[key] for key in row.keys() if key != "text"}
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("\n--- 本文 ---\n")
    print(row["text"])


def print_summary(summary: dict[str, object]) -> None:
    print(f"quick_check: {summary.get('quick_check', '省略')}")
    print(f"ページ数: {int(summary['page_count']):,}")
    print(f"本文容量: {human_bytes(int(summary['text_bytes']))}")
    redirect_count = summary["redirect_count"]
    print(f"リダイレクト数: {int(redirect_count):,}" if redirect_count is not None else "リダイレクト数: 未集計")
    print("名前空間別:")
    for namespace_id, name, count in summary["namespace_counts"]:  # type: ignore[assignment]
        label = name or "（標準）"
        print(f"  {namespace_id:>4} {label}: {count:,}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wikipedia日本語版をSQLiteへ格納する")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="公式ダンプを取得して検証する")
    download.add_argument("--date", default="latest", help="ダンプ日付（例: 20260701）")
    download.add_argument("--output-dir", type=Path, default=Path("data"))

    importer = subparsers.add_parser("import", help="取得済みダンプをSQLiteへ変換する")
    importer.add_argument("dump", type=Path)
    importer.add_argument("database", type=Path)
    importer.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    importer.add_argument("--restart", action="store_true", help="未完成DBを破棄して再変換する")

    build = subparsers.add_parser("build", help="取得から変換・検証まで実行する")
    build.add_argument("--date", default="latest", help="ダンプ日付（例: 20260701）")
    build.add_argument("--output-dir", type=Path, default=Path("data"))
    build.add_argument("--database", type=Path, default=Path("jawiki.sqlite"))
    build.add_argument("--restart", action="store_true", help="未完成DBを破棄して再変換する")

    query = subparsers.add_parser("get", help="タイトル完全一致でページを表示する")
    query.add_argument("database", type=Path)
    query.add_argument("title")
    query.add_argument("--namespace", type=int, default=0)

    verify = subparsers.add_parser("verify", help="SQLiteの整合性と集計値を確認する")
    verify.add_argument("database", type=Path)
    verify.add_argument(
        "--full", action="store_true", help="本文領域を含む物理整合性と全件集計を検査する（数時間かかる場合あり）"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "download":
        info = resolve_dump(args.date)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        download_dump(info, args.output_dir)
    elif args.command == "import":
        import_dump(args.dump, args.database, batch_size=args.batch_size, restart=args.restart)
    elif args.command == "build":
        info = resolve_dump(args.date)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        dump_path = download_dump(info, args.output_dir)
        import_dump(dump_path, args.database, source_info=info, restart=args.restart)
        print_summary(verify_database(args.database))
    elif args.command == "get":
        query_title(args.database, args.title, args.namespace)
    elif args.command == "verify":
        print_summary(verify_database(args.database, full=args.full))


if __name__ == "__main__":
    main()
