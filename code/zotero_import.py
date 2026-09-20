"""Import selected papers into a running Zotero 10 personal library."""
from __future__ import annotations

import hashlib
import html
import json
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from . import paper_enrichment as enrichment


class ZoteroConnectionError(RuntimeError):
    """Stop the batch on connection, authorization or concurrent-change errors."""


class ZoteroClient:
    """Local API credentials live only in this application's memory."""

    def __init__(self, port: int = 23119):
        self.base = f"http://127.0.0.1:{port}/api"
        self.server_id = ""
        self.api_key = ""
        self.remember = False
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, *, method="GET", data=None, headers=None, timeout=30):
        request_headers = {"Zotero-API-Version": "3", "Zotero-Allowed-Request": "1",
                           "User-Agent": "search4paper/1.0"}
        if self.server_id:
            request_headers["Zotero-Server-ID"] = self.server_id
        writing = method != "GET" and path != "/local/authorize" and not path.startswith("/local/uploads/")
        if writing:
            if not self.api_key:
                authorization = self.request("/local/authorize", method="POST",
                                             data={"appName": "search4paper"}, timeout=120)
                self.api_key = authorization["key"]
                self.remember = authorization["remember"]
            request_headers["Zotero-API-Key"] = self.api_key
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        try:
            with self.opener.open(Request(self.base + path, data=data, headers=request_headers,
                                          method=method), timeout=timeout) as response:
                server_id = response.headers.get("Zotero-Server-ID")
                if not server_id or (self.server_id and server_id != self.server_id):
                    raise ZoteroConnectionError("需要 Zotero 10，且导入期间不能切换 Zotero 资料库。")
                self.server_id = server_id
                body = response.read()
                return json.loads(body) if body and "json" in response.headers.get("Content-Type", "") else None
        except HTTPError as exc:
            if exc.code == 401:
                self.api_key = ""
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            hints = {401: "授权已失效，请再次点击导入并在 Zotero 中授权。",
                     403: "请在 Zotero 设置 → 高级中启用本地通信，并允许本次写入。",
                     412: "资料库或条目已变化，请重新启动本工具并选择目标文献夹。",
                     429: "授权请求过于频繁，请稍后重试；批量导入可选择“始终允许”。"}
            raise ZoteroConnectionError(f"Zotero HTTP {exc.code}：{hints.get(exc.code, detail)}") from exc
        except (URLError, OSError) as exc:
            raise ZoteroConnectionError(f"无法连接本机 Zotero，请启动 Zotero 10 并启用本地通信：{exc}") from exc
        finally:
            if writing and not self.remember:
                self.api_key = ""

    def collections(self):
        self.request("/")
        return [item["data"] for item in self.request("/users/0/collections")]

    def write(self, kind, records):
        return self.request(f"/users/0/{kind}", method="POST", data=records)

    def collection(self, parent, name):
        """Reuse an explicitly named collection under the chosen parent."""
        if not name:
            if parent:
                self.request(f"/users/0/collections/{parent}")
            return parent
        matches = [item for item in self.collections()
                   if item["name"] == name and (item.get("parentCollection") or "") == parent]
        if len(matches) > 1:
            raise ValueError("同一位置有多个同名文献夹，请直接选择目标文献夹。")
        if matches:
            return matches[0]["key"]
        response = self.write("collections", [{"name": name, "parentCollection": parent or False}])
        return _written_key(response, 0)

    def attach_pdf(self, item_key, row):
        children = self.request(f"/users/0/items/{item_key}/children")
        attachments = [item["data"] for item in children if item["data"]["itemType"] == "attachment"]
        if any(item.get("contentType") == "application/pdf" and item.get("md5") for item in attachments):
            return "已有 PDF"
        local = str(row.get("pdf_local_path") or "").strip()
        with tempfile.TemporaryDirectory(prefix="search4paper-zotero-") as directory:
            if local:
                path = Path(local).expanduser()
                if not path.is_file():
                    raise ValueError(f"本地 PDF 不存在：{path}")
            else:
                url = enrichment.resolve_pdf_url(row)
                if not url:
                    raise ValueError("没有本地 PDF 或可直接下载的 PDF 链接")
                path = Path(directory) / "paper.pdf"
                downloaded = enrichment._download_one_pdf(url, path)
                if downloaded["status"] != "downloaded":
                    raise RuntimeError(downloaded["error"])
            content = path.read_bytes()
            if not content.startswith(b"%PDF-"):
                raise ValueError("附件不是 PDF 文件")
            pending = [item for item in attachments if item.get("title") == "PDF (search4paper)"
                       and item.get("linkMode") == "imported_file" and not item.get("md5")]
            if len(pending) > 1:
                raise ValueError("有多个未完成的 PDF 附件，请在 Zotero 中检查后重试")
            if pending:
                attachment_key = pending[0]["key"]
            else:
                response = self.write("items", [{"itemType": "attachment", "parentItem": item_key,
                    "linkMode": "imported_file", "title": "PDF (search4paper)", "contentType": "application/pdf",
                    "filename": path.name, "tags": [], "relations": {}}])
                attachment_key = _written_key(response, 0)
            endpoint = f"/users/0/items/{attachment_key}/file"
            # MD5 and file timestamps are required by Zotero's upload protocol.
            upload = self.request(endpoint, method="POST", data=urlencode({
                "md5": hashlib.md5(content).hexdigest(), "filename": path.name,
                "filesize": len(content), "mtime": int(path.stat().st_mtime * 1000),
            }, quote_via=quote).encode(), headers={"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"})
            if not upload.get("exists"):
                if not upload["url"].startswith(self.base + "/local/uploads/"):
                    raise ValueError("Zotero 返回了非本机文件上传地址")
                self.request(upload["url"][len(self.base):], method="POST", data=content,
                             headers={"Content-Type": upload["contentType"]}, timeout=120)
                self.request(endpoint, method="POST", data=urlencode({"upload": upload["uploadKey"]}).encode(),
                             headers={"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"})
        return "已导入 PDF"


def _written_key(response, index):
    index = str(index)
    if index in response.get("failed", {}):
        raise RuntimeError(response["failed"][index]["message"])
    key = response.get("success", {}).get(index) or response.get("unchanged", {}).get(index)
    if not key:
        raise RuntimeError("Zotero 未返回条目的写入结果，请检查文献库后重试")
    return key


def _openreview_id(url):
    parts = urlsplit(str(url or ""))
    if parts.hostname in {"openreview.net", "www.openreview.net"}:
        return parse_qs(parts.query).get("id", [""])[0]
    return ""


def _identities(data):
    identities = set()
    doi = enrichment._normalize_doi(data.get("DOI") or data.get("doi"))
    if doi:
        identities.add(("doi", doi))
    review = data.get("openreview_id") or _openreview_id(data.get("url") or data.get("openreview_url") or data.get("source_url"))
    if review:
        identities.add(("openreview", review))
    for line in str(data.get("extra") or "").splitlines():
        for label, kind in (("OpenReview ID: ", "openreview"), ("Search4Paper ID: ", "source"), ("DOI: ", "doi")):
            if line.startswith(label):
                value = line[len(label):].strip()
                identities.add((kind, enrichment._normalize_doi(value) if kind == "doi" else value))
    if data.get("source_record_id"):
        identities.add(("source", json.dumps([data.get("conference", ""), str(data.get("year", "")),
                                             data["source_record_id"]], ensure_ascii=False)))
    return identities


def paper_item(row, collection, tags):
    title = str(row.get("title") or "").strip()
    identities = _identities(row)
    if not title or not identities:
        raise ValueError("论文缺少标题或 DOI/OpenReview/来源记录标识，无法可靠导入及去重")
    journal = row.get("ccf_type") == "期刊"
    item = {"itemType": "journalArticle" if journal else "conferencePaper", "title": title,
            "abstractNote": str(row.get("abstract") or ""), "date": str(row.get("year") or ""),
            "url": str(row.get("openreview_url") or row.get("source_url") or row.get("paper_url") or ""),
            "DOI": enrichment._normalize_doi(row.get("doi")),
            # Snapshot author names are not reliably split into family/given names.
            "creators": [{"creatorType": "author", "name": name.strip()}
                         for name in str(row.get("authors") or "").split(";") if name.strip()],
            "tags": [{"tag": tag} for tag in dict.fromkeys(tags)],
            "collections": [collection] if collection else [], "relations": {},
            "extra": "\n".join(("OpenReview ID: " if kind == "openreview" else "Search4Paper ID: ") + value
                                 for kind, value in sorted(identities) if kind != "doi")}
    venue = str(row.get("booktitle") or row.get("conference_display_name") or row.get("conference") or "")
    item["publicationTitle" if journal else "proceedingsTitle"] = venue
    for field in ("volume", "pages") + (("issue",) if journal else ()):
        if row.get(field):
            item[field] = str(row[field])
    return item


def evidence_note(row, key):
    lines = ["Imported by search4paper", f"Source: {row.get('source_url') or row.get('openreview_url') or ''}"]
    for hit in row.get("matched_snippets", []):
        lines.append(f"[{hit.get('field', '')}] {hit.get('term', '')}: {hit.get('text', '')}")
    if row.get("bibtex"):
        lines.extend(["Source BibTeX:", row["bibtex"]])
    return {"itemType": "note", "parentItem": key,
            "note": "<p>" + "</p><p>".join(html.escape(str(line)) for line in lines) + "</p>",
            "tags": [], "relations": {}}


def import_papers(client, rows, *, collection="", new_collection="", tags=(), include_pdfs=False, progress=None):
    """Preserve existing metadata; report per-paper metadata and attachment outcomes."""
    rows = list(rows)
    if not rows:
        raise ValueError("请先选择论文")
    def notify(message):
        if progress:
            progress(message)
    notify("正在读取 Zotero 文献库；出现授权窗口时请切换到 Zotero。")
    collection = client.collection(collection, new_collection.strip())
    existing = [item["data"] for item in client.request("/users/0/items/top")
                if item["data"]["itemType"] not in {"note", "attachment"}]
    results, index = [], {}
    for item in existing:
        for identity in _identities(item):
            index.setdefault(identity, {})[item["key"]] = item
    # Write each parent before its children, so a rejected parent cannot create orphan notes.
    stopped = ""
    for position, row in enumerate(rows, 1):
        result = dict(title=str(row.get("title") or ""), status="failed", key="", error="", pdf_status="", pdf_error="")
        results.append(result)
        if stopped:
            result["error"] = "批次已停止，本篇未处理：" + stopped
            continue
        notify(f"正在导入 {position}/{len(rows)}：{result['title']}")
        try:
            item = paper_item(row, collection, tags)
            identities = _identities(row)
            matches = {key: value for identity in identities for key, value in index.get(identity, {}).items()}
            if len(matches) > 1:
                raise ValueError("Zotero 中存在多个相同 DOI/来源标识的条目，请先确认重复条目")
            if matches:
                saved = dict(next(iter(matches.values())))
                collections = list(saved.get("collections", []))
                if collection and collection not in collections:
                    collections.append(collection)
                merged_tags = list(saved.get("tags", []))
                known_tags = {entry["tag"] for entry in merged_tags}
                merged_tags.extend(entry for entry in item["tags"] if entry["tag"] not in known_tags)
                if collections != saved.get("collections", []) or merged_tags != saved.get("tags", []):
                    saved.update(collections=collections, tags=merged_tags)
                    _written_key(client.write("items", [saved]), 0)
                    saved = client.request(f"/users/0/items/{saved['key']}")["data"]
                result.update(status="existing", key=saved["key"])
            else:
                response = client.write("items", [item])
                key = _written_key(response, 0)
                saved = response["successful"]["0"]["data"]
                result.update(status="created", key=key)
            for identity in identities | _identities(saved):
                index.setdefault(identity, {})[saved["key"]] = saved
            try:
                children = ([] if result["status"] == "created" else
                            client.request(f"/users/0/items/{result['key']}/children"))
                if not any(child["data"]["itemType"] == "note" and
                           "<p>Imported by search4paper</p>" in child["data"].get("note", "") for child in children):
                    _written_key(client.write("items", [evidence_note(row, result["key"])]), 0)
            except (RuntimeError, ValueError, OSError) as exc:
                result["error"] = f"条目已导入，来源笔记失败：{exc}"
                if isinstance(exc, ZoteroConnectionError):
                    stopped = str(exc)
            if include_pdfs and not stopped:
                try:
                    result["pdf_status"] = client.attach_pdf(result["key"], row)
                except (RuntimeError, ValueError, OSError) as exc:
                    result["pdf_error"] = str(exc)
                    if isinstance(exc, ZoteroConnectionError):
                        stopped = str(exc)
        except (RuntimeError, ValueError, OSError) as exc:
            result["error"] = str(exc)
            if isinstance(exc, ZoteroConnectionError):
                stopped = str(exc)
    return results
