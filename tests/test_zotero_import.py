import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from code import zotero_import as zotero


def paper(**changes):
    return dict(dict(conference="ICML", year=2026, source_record_id="forum1", openreview_id="forum1",
                     title="Time Series Anomaly Detection", authors="张三; Anne van der Meer", abstract="An abstract",
                     openreview_url="https://openreview.net/forum?id=forum1", doi="", bibtex="@inproceedings{key}",
                     matched_snippets=[dict(field="abstract", term="anomaly", text="<script> & evidence")]), **changes)


class MemoryLibrary:
    def __init__(self, items=()):
        self.items = {item["key"]: copy.deepcopy(item) for item in items}
        self.writes = []
        self.attach_pdf = MagicMock(return_value="已导入 PDF")

    def collection(self, parent, name):
        return parent

    def request(self, path):
        if path == "/users/0/items/top":
            items = [item for item in self.items.values() if not item.get("parentItem")]
            return [{"data": copy.deepcopy(item)} for item in items]
        key = path.split("/")[4]
        if path.endswith("/children"):
            return [{"data": copy.deepcopy(item)} for item in self.items.values() if item.get("parentItem") == key]
        return {"data": copy.deepcopy(self.items[key])}

    def write(self, kind, records):
        self.writes.extend(copy.deepcopy(records))
        result = dict(success={}, successful={}, unchanged={}, failed={})
        for i, record in enumerate(records):
            key = record.get("key") or f"TEST{len(self.items):04d}"
            item = dict(record, key=key, version=record.get("version", 0) + 1)
            self.items[key] = copy.deepcopy(item)
            result["success"][str(i)] = key
            result["successful"][str(i)] = {"key": key, "data": copy.deepcopy(item)}
        return result


class ImportTests(unittest.TestCase):
    def test_import_without_doi_preserves_metadata_evidence_and_deduplicates_repeated_selection(self):
        row = paper()
        before = copy.deepcopy(row)
        library = MemoryLibrary()
        first = zotero.import_papers(library, [row, row], collection="COLL1234", tags=["TSAD", "TSAD"])
        again = zotero.import_papers(library, [row], collection="COLL1234", tags=["TSAD"])
        self.assertEqual([item["status"] for item in first], ["created", "existing"])
        self.assertEqual(again[0]["status"], "existing")
        self.assertEqual(len(library.items), 2)  # One paper and one evidence note
        self.assertEqual(len(library.writes), 2)
        item = library.items[first[0]["key"]]
        self.assertEqual(item["abstractNote"], row["abstract"])
        self.assertEqual(item["creators"], [{"creatorType": "author", "name": "张三"},
                                            {"creatorType": "author", "name": "Anne van der Meer"}])
        self.assertEqual(item["tags"], [{"tag": "TSAD"}])
        self.assertEqual(item["collections"], ["COLL1234"])
        note = next(item for item in library.items.values() if item["itemType"] == "note")
        self.assertIn("&lt;script&gt; &amp; evidence", note["note"])
        self.assertNotIn("<script>", note["note"])
        self.assertEqual(row, before)

    def test_existing_doi_preserves_user_edits_and_adds_collection_and_tags(self):
        saved = dict(key="EXISTING", version=3, itemType="conferencePaper", DOI="https://doi.org/10.1234/Example",
                     title="User edited title", abstractNote="User abstract", creators=[{"name": "User author", "creatorType": "author"}],
                     tags=[{"tag": "keep", "type": 1}], collections=["OLD12345"], extra="User notes")
        library = MemoryLibrary([saved])
        row = paper(doi="doi:10.1234/example")
        result = zotero.import_papers(library, [row], collection="NEW12345", tags=["keep", "TSAD"])
        self.assertEqual(result[0]["status"], "existing")
        updated = library.items["EXISTING"]
        for field in ("title", "creators", "abstractNote", "extra"):
            self.assertEqual(updated[field], saved[field])
        self.assertEqual(updated["collections"], ["OLD12345", "NEW12345"])
        self.assertEqual(updated["tags"], [{"tag": "keep", "type": 1}, {"tag": "TSAD"}])

    def test_openreview_url_matches_an_item_imported_outside_this_tool(self):
        library = MemoryLibrary([dict(key="EXISTING", version=1, itemType="conferencePaper",
                                      url="https://openreview.net/forum?id=forum1", title="Original")])
        result = zotero.import_papers(library, [paper()])
        self.assertEqual(result[0]["key"], "EXISTING")
        self.assertEqual(result[0]["status"], "existing")

    def test_conflicting_identifiers_are_reported_without_merging_or_writing(self):
        library = MemoryLibrary([
            dict(key="DOI12345", itemType="conferencePaper", DOI="10.1234/test"),
            dict(key="FORUM123", itemType="conferencePaper", url="https://openreview.net/forum?id=forum1")])
        result = zotero.import_papers(library, [paper(doi="10.1234/test")])
        self.assertEqual(result[0]["status"], "failed")
        self.assertIn("多个", result[0]["error"])
        self.assertFalse(library.writes)

    def test_source_identity_keeps_different_venues_separate_and_rejects_unidentified_paper(self):
        library = MemoryLibrary()
        first = paper(openreview_id="", openreview_url="", source_record_id="record", ccf_type="期刊", volume="3", issue="2")
        second = dict(first, conference="TODS")
        bad = paper(openreview_id="", openreview_url="", source_record_id="")
        results = zotero.import_papers(library, [first, second, bad])
        self.assertEqual([item["status"] for item in results], ["created", "created", "failed"])
        self.assertEqual(library.items[results[0]["key"]]["itemType"], "journalArticle")
        self.assertEqual(library.items[results[0]["key"]]["issue"], "2")

    def test_pdf_failure_does_not_lose_successful_parent(self):
        library = MemoryLibrary()
        library.attach_pdf.side_effect = ValueError("missing.pdf")
        results = zotero.import_papers(library, [paper()], include_pdfs=True)
        self.assertEqual(results[0]["status"], "created")
        self.assertEqual(results[0]["pdf_error"], "missing.pdf")
        self.assertEqual(results[0]["error"], "")

    def test_authorization_denied_stops_batch_and_explicit_retry_completes_missing_note(self):
        library = MemoryLibrary()
        write = library.write
        def reject_note(kind, records):
            if records[0]["itemType"] == "note":
                raise zotero.ZoteroConnectionError("denied")
            return write(kind, records)
        library.write = reject_note
        results = zotero.import_papers(library, [paper(), paper(openreview_id="forum2", source_record_id="forum2")])
        self.assertEqual(results[0]["status"], "created")
        self.assertIn("笔记失败", results[0]["error"])
        self.assertIn("本篇未处理", results[1]["error"])
        self.assertEqual(len(library.items), 1)
        library.write = write
        results = zotero.import_papers(library, [paper()])
        self.assertEqual(results[0]["status"], "existing")
        self.assertEqual(results[0]["error"], "")
        self.assertEqual(len(library.items), 2)


class LocalAPITests(unittest.TestCase):
    @staticmethod
    def response(data, *, server="instance1", content_type="application/json"):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Zotero-Server-ID": server, "Content-Type": content_type}
        response.read.return_value = json.dumps(data).encode() if data is not None else b""
        return response

    def test_local_authorization_and_single_use_key_are_sent_only_to_local_server(self):
        client = zotero.ZoteroClient()
        client.opener = MagicMock()
        client.opener.open.side_effect = [self.response(None), self.response({"key": "secret", "remember": False}),
                                          self.response({"success": {"0": "ITEM1234"}})]
        client.request("/")
        client.write("items", [{"itemType": "conferencePaper", "title": "Paper"}])
        requests = [call.args[0] for call in client.opener.open.call_args_list]
        self.assertEqual(requests[1].full_url, "http://127.0.0.1:23119/api/local/authorize")
        self.assertEqual(requests[1].get_header("Zotero-server-id"), "instance1")
        self.assertEqual(requests[2].get_header("Zotero-api-key"), "secret")
        self.assertEqual(client.api_key, "")

    def test_api_failures_are_explicit_and_never_replay_a_write(self):
        for error, message in ((HTTPError("local", 403, "Denied", {}, io.BytesIO(b'denied')), "403"),
                               (HTTPError("local", 412, "Changed", {}, io.BytesIO(b'changed')), "412"),
                               (URLError("offline"), "启动 Zotero 10")):
            with self.subTest(message=message):
                client = zotero.ZoteroClient()
                client.api_key = "test"
                client.remember = True
                client.opener = MagicMock()
                client.opener.open.side_effect = error
                with self.assertRaisesRegex(zotero.ZoteroConnectionError, message):
                    client.write("items", [{"title": "Paper"}])
                client.opener.open.assert_called_once()

    def test_changed_or_old_server_fails_before_library_access(self):
        for server in (None, "different"):
            client = zotero.ZoteroClient()
            client.server_id = "instance1"
            client.opener = MagicMock()
            client.opener.open.return_value = self.response([], server=server)
            with self.assertRaises(zotero.ZoteroConnectionError):
                client.collections()
            client.opener.open.assert_called_once()

    def test_same_named_collection_reused_and_ambiguous_name_rejected(self):
        client = zotero.ZoteroClient()
        records = [dict(key="ONE12345", name="TSAD", parentCollection=False)]
        with patch.object(client, "collections", return_value=records), patch.object(client, "write") as write:
            self.assertEqual(client.collection("", "TSAD"), "ONE12345")
            write.assert_not_called()
            records.append(dict(key="TWO12345", name="TSAD", parentCollection=False))
            with self.assertRaisesRegex(ValueError, "多个同名"):
                client.collection("", "TSAD")

    def test_pdf_upload_uses_local_bytes_and_required_protocol_then_reuses_attachment(self):
        client = zotero.ZoteroClient()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "论文 附件.pdf"
            content = b"%PDF-1.4\nlocal test file"
            path.write_bytes(content)
            with patch.object(client, "request", side_effect=[[], {"url": client.base + "/local/uploads/token",
                    "uploadKey": "token", "contentType": "application/pdf"}, None, None]) as request, \
                 patch.object(client, "write", return_value={"success": {"0": "PDF12345"}}):
                self.assertEqual(client.attach_pdf("PARENT12", paper(pdf_local_path=str(path))), "已导入 PDF")
            calls = request.call_args_list
            self.assertIn(hashlib.md5(content).hexdigest().encode(), calls[1].kwargs["data"])
            self.assertIn(b"%20", calls[1].kwargs["data"])
            self.assertNotIn(b"+", calls[1].kwargs["data"])
            self.assertEqual(calls[2].kwargs["data"], content)
            self.assertEqual(calls[3].kwargs["data"], b"upload=token")
            self.assertEqual(path.read_bytes(), content)
            saved = {"data": dict(itemType="attachment", contentType="application/pdf", md5="verified")}
            with patch.object(client, "request", return_value=[saved]) as request:
                self.assertEqual(client.attach_pdf("PARENT12", paper()), "已有 PDF")
                request.assert_called_once()

    def test_missing_pdf_and_remote_upload_destination_fail_explicitly(self):
        client = zotero.ZoteroClient()
        with patch.object(client, "request", return_value=[]):
            with self.assertRaisesRegex(ValueError, "不存在"):
                client.attach_pdf("PARENT12", paper(pdf_local_path="/missing/paper.pdf"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(b"%PDF-1.4\n")
            with patch.object(client, "request", side_effect=[[], {"url": "https://external.invalid/upload"}]) as request, \
                 patch.object(client, "write", return_value={"success": {"0": "PDF12345"}}):
                with self.assertRaisesRegex(ValueError, "非本机"):
                    client.attach_pdf("PARENT12", paper(pdf_local_path=str(path)))
                self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
