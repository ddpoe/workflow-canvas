"""The preview route answers tables, text and JSON within their caps, and says what it cut.

Tier 2 entered at the route over a real cache (catalog cases 3 and 4): each
test runs a method whose folder output holds the files it previews and
archives the run. The image is a ``.tif``: an on-access scanner can write an
extended attribute on a fresh ``.png`` between collect and the archive pass,
which then refuses the output as changed. Tier 1
tests enter the readers in ``wfc.canvas.previews`` directly for what a route
cannot observe: the bytes a reader consumed, a cut on a character boundary.
"""

from __future__ import annotations

import io
import json

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import completed_run
from tests.harness.scenario import Behavior
from wfc import layout
from wfc.canvas.previews import (JSON_PARSE_BYTES, TABLE_READ_BYTES, TABLE_ROWS, TEXT_BYTES,
                                 read_table, read_text)
from wfc.canvas.wfc_provider import WfcProvider
from wfc.storage import archive_outputs

MB = 1024 * 1024
#: 1000 short comma rows under a header: more rows than a preview shows.
MANY_ROWS = b"id,name,score\n" + b"".join(b"%d,cell%d,%d.5\n" % (i, i, i) for i in range(1000))
#: Ten tab rows under a header.
TAB_ROWS = b"id\tname\n" + b"".join(b"%d\tcell%d\n" % (i, i) for i in range(10))
#: Semicolon rows whose fields hold commas.
SEMI_ROWS = b"gene;note\n" + b"".join(b"g%d;a,b\n" % i for i in range(5))
#: Sixty rows of ~50 KB each: 100 rows would be ~3 MB, past the read cap.
LONG_ROWS = b"".join(b"%d," % i + b"x" * 50_000 + b"\n" for i in range(60))
#: ~2.5 MB with no line break, comma-separated.
ONE_LINE_COMMAS = b"ab," * (int(2.5 * MB) // 3)
#: ~2.5 MB with no line break and no delimiter: one field past csv's limit.
ONE_LINE_ONE_FIELD = b"x" * int(2.5 * MB)


@pytest.fixture
def preview_run(tmp_project, monkeypatch):
    """Run a method whose folder output ``out`` holds the given files, and archive it.

    The run is driven through production's phases (``completed_run``), its
    output archived by ``archive_outputs``, and a provider bound over the
    project.

    Returns:
        A function taking ``{file name: bytes}`` and returning the run id.
    """
    def make(members: dict[str, bytes]) -> str:
        run = completed_run(
            tmp_project, monkeypatch=monkeypatch, method="dump", module="tables",
            sample="s1", outputs={"out": "directory"}, output_files={"out": "out"},
            behavior=Behavior(outputs={"out": members}),
        )
        archive_outputs(tmp_project, run_id=run.run_id)
        bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
        return str(run.run_id)

    return make


def _preview(client, run_id, name, **params):
    resp = client.get(f"/api/wfc/run/{run_id}/preview/out/{name}", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    "name, content, delimiter, columns, n_rows, truncated",
    [
        pytest.param("many.csv", MANY_ROWS, "comma", ["id", "name", "score"], TABLE_ROWS, True,
                     id="comma-capped-at-100-rows"),
        pytest.param("tabs.tsv", TAB_ROWS, "tab", ["id", "name"], 10, False, id="tab-whole"),
    ],
)
@workflow(purpose="A table preview guesses the delimiter and the header row and answers "
                  "at most 100 rows, saying when there were more")
def test_table_preview_guesses_the_delimiter_and_shows_at_most_100_rows(
    canvas_client, preview_run, name, content, delimiter, columns, n_rows, truncated
):
    run_id = preview_run({name: content})

    body = _preview(canvas_client, run_id, name)

    table = body["table"]
    assert body["kind"] == "table"
    assert (table["delimiter"], table["delimiter_guessed"]) == (delimiter, True)
    assert (table["header"], table["header_guessed"]) == (True, True)
    assert table["columns"] == columns
    assert len(table["rows"]) == n_rows
    assert table["rows"][0] == content.split(b"\n")[1].decode().split(
        "\t" if delimiter == "tab" else ",")
    assert body["truncated"] is truncated
    assert body["total_bytes"] == len(content)


@workflow(purpose="A table preview obeys a chosen delimiter and header row over its guesses")
def test_table_preview_obeys_the_delimiter_and_header_overrides(canvas_client, preview_run):
    run_id = preview_run({"semi.csv": SEMI_ROWS})

    guessed = _preview(canvas_client, run_id, "semi.csv")["table"]
    as_comma = _preview(canvas_client, run_id, "semi.csv", delimiter="comma")["table"]
    no_header = _preview(canvas_client, run_id, "semi.csv", header="false")["table"]

    assert (guessed["delimiter"], guessed["columns"]) == ("semicolon", ["gene", "note"])
    assert guessed["rows"][0] == ["g0", "a,b"]
    assert (as_comma["delimiter"], as_comma["delimiter_guessed"]) == ("comma", False)
    assert as_comma["columns"] == ["gene;note"]
    assert as_comma["rows"][0] == ["g0;a", "b"]
    assert (no_header["header"], no_header["header_guessed"]) == (False, False)
    assert no_header["columns"] is None
    assert no_header["rows"][:2] == [["gene", "note"], ["g0", "a,b"]]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(LONG_ROWS, id="long-rows"),
        pytest.param(ONE_LINE_COMMAS, id="no-line-break"),
        pytest.param(ONE_LINE_ONE_FIELD, id="no-line-break-one-field"),
    ],
)
@workflow(purpose="A table preview reads at most ~2 MB of the file, even from a file with "
                  "no line break or one field past csv's limit, and says it was cut")
def test_table_preview_reads_at_most_2mb(canvas_client, preview_run, content):
    run_id = preview_run({"big.csv": content})

    body = _preview(canvas_client, run_id, "big.csv")

    assert body["kind"] == "table"
    assert body["total_bytes"] == len(content)
    assert 0 < body["shown_bytes"] <= TABLE_READ_BYTES
    assert body["truncated"] is True
    assert body["reason"]
    assert len(body["table"]["rows"]) < TABLE_ROWS


TEXT_BIG = b"line of a log\n" * (int(1.2 * MB) // 14)
JSON_BIG = json.dumps([{"k": i, "v": "x" * 40} for i in range(90_000)]).encode()


@pytest.mark.parametrize(
    "name, content, kind, truncated, reason",
    [
        pytest.param("big.log", TEXT_BIG, "text", True, "1 MB", id="text-over-1mb"),
        pytest.param("big.json", JSON_BIG, "text", True, "5 MB", id="json-over-5mb"),
        pytest.param("bad.json", b'{"a": [1, 2,', "text", False, "not valid JSON",
                     id="invalid-json"),
        pytest.param("ok.json", b'{"a": [1, 2]}', "json", False, None, id="json"),
    ],
)
@workflow(purpose="Text over 1 MB and JSON over 5 MB come back cut and say so; JSON too "
                  "large or not valid comes back as text with its reason")
def test_text_and_json_previews_announce_every_cut(
    canvas_client, preview_run, name, content, kind, truncated, reason
):
    assert name != "big.json" or len(content) > JSON_PARSE_BYTES
    run_id = preview_run({name: content})

    body = _preview(canvas_client, run_id, name)

    assert body["kind"] == kind
    assert body["truncated"] is truncated
    assert body["total_bytes"] == len(content)
    assert body["shown_bytes"] == (TEXT_BYTES if truncated else len(content))
    assert body["text"] == content[:body["shown_bytes"]].decode()
    if reason is None:
        assert body["reason"] is None
    else:
        assert reason in body["reason"]


@workflow(purpose="A file no preview answers for (an image, a download-only file) is "
                  "refused with a reason")
def test_preview_refuses_a_kind_it_does_not_answer_for(canvas_client, preview_run):
    run_id = preview_run({"plot.tif": b"II*\x00", "cells.parquet": b"PAR1"})

    for name in ("plot.tif", "cells.parquet"):
        resp = canvas_client.get(f"/api/wfc/run/{run_id}/preview/out/{name}")
        assert resp.status_code == 415, resp.text
        assert name in resp.json()["detail"]


@pytest.mark.parametrize(
    "section, named",
    [
        pytest.param("[previews]\nmemory_mb = 0\n", "[previews] memory_mb", id="previews"),
        pytest.param('[integrity]\ninputs = "sometimes"\n', "[integrity] inputs",
                     id="integrity"),
    ],
)
@workflow(purpose="An invalid project config makes every artifact route (the run's listing, "
                  "the file, preview, image and image-info routes, and the export pair) "
                  "answer 409 with read_config's message naming the setting, not a 500")
def test_the_artifact_routes_refuse_an_invalid_config_with_its_fix(
        canvas_client, preview_run, tmp_project, section, named):
    run_id = preview_run({"rows.csv": b"a,b\n1,2\n"})
    marker = layout.marker_path(tmp_project)
    marker.write_text(marker.read_text(encoding="utf-8") + "\n" + section, encoding="utf-8")

    for route in ("artifacts", "artifact/out/rows.csv", "preview/out/rows.csv",
                  "image/out/rows.csv", "image-info/out/rows.csv"):
        resp = canvas_client.get(f"/api/wfc/run/{run_id}/{route}")
        assert resp.status_code == 409, (route, resp.text)
        assert named in resp.json()["detail"], route
    for route in ("export-artifacts", "preview-artifacts"):
        resp = canvas_client.post(f"/api/wfc/{route}", json={"run_ids": [run_id]})
        assert resp.status_code == 409, (route, resp.text)
        assert named in resp.json()["detail"], route


class _CountingStream(io.BytesIO):
    """A binary stream that counts the bytes read from it."""

    consumed = 0

    def read(self, size=-1):
        data = super().read(size)
        self.consumed += len(data)
        return data


@pytest.mark.parametrize("content", [LONG_ROWS, ONE_LINE_COMMAS, ONE_LINE_ONE_FIELD, MANY_ROWS * 4],
                         ids=["long-rows", "no-line-break", "one-field", "small"])
def test_table_reader_consumes_at_most_the_read_cap(content):
    stream = _CountingStream(content)

    preview = read_table(stream, len(content), name="t.csv")

    assert stream.consumed <= TABLE_READ_BYTES
    assert preview.shown_bytes == stream.consumed


def test_table_reader_strips_a_bom_and_replaces_bytes_that_are_not_utf8():
    content = b"\xef\xbb\xbfid,name\n1,caf\xe9\n2,ok\n"

    preview = read_table(io.BytesIO(content), len(content), name="t.csv")

    assert preview.table.columns == ["id", "name"]
    assert preview.table.rows == [["1", "caf�"], ["2", "ok"]]


def test_a_table_sniffer_failure_falls_back_and_says_so():
    content = b"one_column\nanother\n"

    for name, char in (("t.csv", "comma"), ("t.tsv", "tab")):
        preview = read_table(io.BytesIO(content), len(content), name=name)
        assert (preview.table.delimiter, preview.table.delimiter_guessed) == (char, False)
        assert "could not be guessed" in preview.reason


def test_text_is_cut_on_a_character_boundary():
    # A three-byte character straddles the 1 MB cap.
    content = b"a" * (TEXT_BYTES - 1) + "€".encode() + b"tail"

    preview = read_text(io.BytesIO(content), len(content))

    assert preview.text == "a" * (TEXT_BYTES - 1)
    assert preview.shown_bytes == TEXT_BYTES - 1
    assert preview.truncated is True
