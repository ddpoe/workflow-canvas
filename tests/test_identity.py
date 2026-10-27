"""Identity — the pure witnesses.

Every expectation here is a literal digest: written down, or computed by
``hashlib`` over literal bytes inside the test. Nothing is derived by the
function under test, no session is opened, nothing is patched. The content
hash and code fingerprint cases build a real tree under ``tmp_path``; the
input fingerprint cases hand in literal values.
"""

import hashlib
import itertools

import pytest

from axiom_annotations import workflow

from wfc.contracts import render_contract_projection
from wfc.identity import (
    SampleIdentity,
    UpstreamRunIdentity,
    build_code_fingerprint,
    build_input_fingerprint,
    collect_method_scripts,
    digest_input_parts,
    hash_directory,
    hash_path,
    render_run_part,
    render_sample_part,
)


# =============================================================================
# Content hash
# =============================================================================

@workflow(
    purpose="The directory content hash is the MD5 of the sorted relpath:md5 "
            "manifest — a nested tree where the walk order differs from the "
            "sorted order pins the second sort — and the two refusals: a "
            "missing path, and a file handed to the directory rule"
)
def test_hash_directory_manifest_literal_and_refusals(tmp_path):
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"aaa")
    (tree / "zeta.txt").write_bytes(b"zzz")
    (tree / "sub" / "c.txt").write_bytes(b"ccc")

    # DVC's .dir manifest: a JSON list sorted by relpath, sorted keys,
    # default separators. os.walk yields the top-level files (a.txt,
    # zeta.txt) before descending into sub/, so without the sort zeta.txt
    # would precede sub/c.txt.
    manifest = (
        '[{"md5": "' + hashlib.md5(b"aaa").hexdigest() + '", "relpath": "a.txt"}, '
        '{"md5": "' + hashlib.md5(b"ccc").hexdigest() + '", "relpath": "sub/c.txt"}, '
        '{"md5": "' + hashlib.md5(b"zzz").hexdigest() + '", "relpath": "zeta.txt"}]'
    )
    expected = hashlib.md5(manifest.encode("utf-8")).hexdigest() + ".dir"
    assert hash_directory(tree) == expected

    with pytest.raises(FileNotFoundError):
        hash_path(tmp_path / "missing.bin")
    with pytest.raises(NotADirectoryError, match="Not a directory"):
        hash_directory(tree / "a.txt")


# =============================================================================
# Code fingerprint
# =============================================================================

_METHOD_TREE = {
    "main.py": "def main():\n    return 1\n",
    "helper.R": "x <- 1\n",
    "run.sh": "echo run\n",
    "lower.r": "y <- 2\n",
    "data.csv": "a,b\n1,2\n",
    "lib/util.py": "def util():\n    pass\n",
    "lib/notes.txt": "not a script\n",
}
_SELECTED = ["helper.R", "lib/util.py", "lower.r", "main.py", "run.sh"]
#: Every file of the tree, sorted by relative POSIX path: what the code
#: fingerprint digests (``method.yaml`` aside).
_EVERY_FILE = ["data.csv", "helper.R", "lib/notes.txt", "lib/util.py",
               "lower.r", "main.py", "run.sh"]

#: One rendered contract the code fingerprint cases hold fixed. It arrives
#: as a value because Identity imports no ``wfc`` module.
_PROJECTION = render_contract_projection({
    "inputs": {"data": {}},
    "outputs": {"result": {"type": ".csv"}},
    "executor": "python",
    "script": None,
    "gpus": False,
})


def _write_method_tree(root):
    (root / "lib").mkdir(parents=True)
    for rel, content in _METHOD_TREE.items():
        (root / rel).write_text(content, encoding="utf-8")


@workflow(
    purpose="The selection rule returns every recognized script under the "
            "directory, recursively, each exactly once, sorted by relative "
            "POSIX path — a nested tree with .py/.R/.sh/.r, a data file and a "
            "helper in a subdirectory"
)
def test_collect_method_scripts_selection_order_and_dedup(tmp_path):
    method_dir = tmp_path / "method"
    _write_method_tree(method_dir)

    selected = [p.relative_to(method_dir).as_posix() for p in collect_method_scripts(method_dir)]

    # On a case-insensitive filesystem rglob("*.R") and rglob("*.r") both
    # find helper.R and lower.r; the equality below holds only if each
    # appears once, whichever glob found it.
    assert selected == _SELECTED


@workflow(
    purpose="The code fingerprint digests every file of the directory but its "
            "method.yaml, sorted by relative POSIX path: text files as "
            "relpath:text, a binary file as relpath: plus its raw bytes, then "
            "the contract projection — a literal hashlib digest"
)
def test_code_fingerprint_digests_every_file_but_method_yaml(tmp_path):
    method_dir = tmp_path / "method"
    _write_method_tree(method_dir)
    # method.yaml enters through the contract projection only; its text is
    # not a file of the digest.
    (method_dir / "method.yaml").write_text("executor: python\n", encoding="utf-8")
    # A NUL byte makes a file binary: it is hashed as raw bytes, its CRLF kept.
    blob = b"\x00\x01binary\r\n\xff"
    (method_dir / "lib" / "weights.bin").write_bytes(blob)

    hasher = hashlib.sha256()
    for rel in sorted(_EVERY_FILE + ["lib/weights.bin"]):
        if rel == "lib/weights.bin":
            hasher.update(b"lib/weights.bin:" + blob)
        else:
            hasher.update(f"{rel}:{_METHOD_TREE[rel]}".encode("utf-8"))
    hasher.update(f"contract:{_PROJECTION}".encode("utf-8"))

    assert build_code_fingerprint(method_dir, _PROJECTION) == hasher.hexdigest()


@pytest.mark.parametrize(
    "helper,before,after",
    [
        pytest.param("lookup.tsv", b"gene\tid\nA\t1\n", b"gene\tid\nA\t2\n",
                     id="text-helper"),
        pytest.param("model.bin", b"\x00\x01\x02", b"\x00\x01\x03",
                     id="binary-helper"),
    ],
)
@workflow(
    purpose="A helper of any file type is part of the method's code: adding "
            "a non-script text helper or a binary helper moves the code "
            "fingerprint, and so does changing its content"
)
def test_a_helper_of_any_type_moves_the_code_fingerprint(tmp_path, helper,
                                                          before, after):
    method_dir = tmp_path / "method"
    method_dir.mkdir()
    (method_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    scripts_only = build_code_fingerprint(method_dir, _PROJECTION)

    (method_dir / helper).write_bytes(before)
    with_helper = build_code_fingerprint(method_dir, _PROJECTION)
    (method_dir / helper).write_bytes(after)
    changed_helper = build_code_fingerprint(method_dir, _PROJECTION)

    assert len({scripts_only, with_helper, changed_helper}) == 3


@workflow(
    purpose="Line endings do not move the code fingerprint: a copy whose text "
            "files hold CRLF fingerprints as the LF copy, while a binary "
            "file's bytes are hashed as they are"
)
def test_crlf_and_lf_copies_fingerprint_alike(tmp_path):
    files = {
        "main.py": "import csv\nprint('hi')\n",
        "lookup.tsv": "gene\tid\nA\t1\n",
        "sub/run.sh": "echo run\necho done\n",
    }
    blob = b"\x00head\nbody\n"
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    for root, newline in ((lf, b"\n"), (crlf, b"\r\n")):
        (root / "sub").mkdir(parents=True)
        for rel, text in files.items():
            (root / rel).write_bytes(text.encode("utf-8").replace(b"\n", newline))
        (root / "model.bin").write_bytes(blob)

    assert (build_code_fingerprint(crlf, _PROJECTION)
            == build_code_fingerprint(lf, _PROJECTION))

    # A binary file is not text: converting its newlines is a change.
    (crlf / "model.bin").write_bytes(blob.replace(b"\n", b"\r\n"))
    assert (build_code_fingerprint(crlf, _PROJECTION)
            != build_code_fingerprint(lf, _PROJECTION))


# =============================================================================
# Input fingerprint -- the part alphabet and the digest, over literal values
# =============================================================================

_KEY_A = "0123456789abcdef" * 4
_KEY_B = "fedcba9876543210" * 4
_EMPTY_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@workflow(
    purpose="The part alphabet's three literal renderings from literal values "
            "— an upstream run's key, the legacy-run sentinel and a sample's "
            "content hash — and the refusal that keeps it three: a sample "
            "identity with no content hash has no weaker spelling"
)
def test_part_alphabet_literal_renderings():
    # Every form carries the input slot; an upstream's also carries the
    # source slot it was taken from.
    assert render_run_part(UpstreamRunIdentity(
        input_slot="data", source_slot="merged", run_id=3, cache_key=_KEY_A,
    )) == f"key:data:merged:{_KEY_A}"
    assert render_run_part(UpstreamRunIdentity(
        input_slot="data", source_slot="merged", run_id=2, cache_key=None,
    )) == "key:data:merged:legacy-run-2"

    assert render_sample_part(SampleIdentity(
        input_slot="raw", content_hash="a" * 32,
    )) == "hash:raw:" + "a" * 32
    # A sample has exactly one form. There is no weaker spelling to fall
    # back to when the hash is absent, so the alphabet refuses rather than
    # inventing a part the key would then be blind behind.
    with pytest.raises(ValueError, match="no content hash"):
        render_sample_part(SampleIdentity(input_slot="raw", content_hash=""))

    # Each single part digests to the digest of the part text.
    assert build_input_fingerprint([UpstreamRunIdentity(
        input_slot="data", source_slot="merged", run_id=3, cache_key=_KEY_A,
    )]) == _sha(f"key:data:merged:{_KEY_A}")
    assert build_input_fingerprint([UpstreamRunIdentity(
        input_slot="data", source_slot="merged", run_id=2,
    )]) == _sha("key:data:merged:legacy-run-2")
    assert build_input_fingerprint([], [SampleIdentity(
        input_slot="raw", content_hash="a" * 32,
    )]) == _sha("hash:raw:" + "a" * 32)

    # The input slot is part of the identity: the same values wired into a
    # different slot are a different fingerprint.
    assert build_input_fingerprint([UpstreamRunIdentity(
        input_slot="data", source_slot="merged", run_id=3, cache_key=_KEY_A,
    )]) != build_input_fingerprint([UpstreamRunIdentity(
        input_slot="ref", source_slot="merged", run_id=3, cache_key=_KEY_A,
    )])
    assert build_input_fingerprint([UpstreamRunIdentity(
        input_slot="data", source_slot="merged", run_id=3, cache_key=_KEY_A,
    )]) != build_input_fingerprint([UpstreamRunIdentity(
        input_slot="data", source_slot="qc", run_id=3, cache_key=_KEY_A,
    )])


@workflow(
    purpose="A sample's content hash handed in as a value reaches the input "
            "fingerprint, and a same-size bundle membership swap moves it, "
            "proven at the pure function over literal parts"
)
def test_sample_part_reaches_digest_and_membership_swap_moves_it():
    s1 = SampleIdentity(input_slot="raw", content_hash="1" * 32)
    s2 = SampleIdentity(input_slot="raw", content_hash="2" * 32)
    s3 = SampleIdentity(input_slot="raw", content_hash="3" * 32)

    fp_12 = build_input_fingerprint([], [s1, s2])
    fp_13 = build_input_fingerprint([], [s1, s3])

    assert fp_12 == _sha("hash:raw:" + "1" * 32 + ",hash:raw:" + "2" * 32)
    assert fp_13 == _sha("hash:raw:" + "1" * 32 + ",hash:raw:" + "3" * 32)
    assert fp_12 != fp_13, (
        "a same-size membership swap must move the bundle's fingerprint; "
        "the sample's content hash is not reaching the digest"
    )


@workflow(
    purpose="Order invariance against a literal: a mixed set -- two upstream "
            "keys, a legacy-run sentinel, two sample hashes -- handed in every "
            "permutation equals the one literal digest of the sorted comma-join; "
            "the empty part list equals the digest of the empty string"
)
def test_input_fingerprint_permutation_matches_literal_digest():
    runs = [
        UpstreamRunIdentity(input_slot="data", source_slot="out", run_id=1, cache_key=_KEY_A),
        UpstreamRunIdentity(input_slot="data", source_slot="out", run_id=2, cache_key=_KEY_B),
        UpstreamRunIdentity(input_slot="ref", source_slot="out", run_id=7, cache_key=None),
    ]
    samples = [SampleIdentity(input_slot="raw", content_hash="a" * 32),
               SampleIdentity(input_slot="raw", content_hash="b" * 32)]
    parts = [f"key:data:out:{_KEY_A}", f"key:data:out:{_KEY_B}",
             "key:ref:out:legacy-run-7",
             "hash:raw:" + "a" * 32, "hash:raw:" + "b" * 32]
    expected = _sha(",".join(sorted(parts)))

    for run_order in itertools.permutations(runs):
        for sample_order in itertools.permutations(samples):
            assert build_input_fingerprint(list(run_order), list(sample_order)) == expected, (
                "a permutation of the same identities moved the fingerprint "
                "away from the literal digest of the sorted comma-join"
            )

    # The digest over parts is independently callable and equally order-blind.
    for part_order in itertools.permutations(parts):
        assert digest_input_parts(list(part_order)) == expected

    assert digest_input_parts([]) == hashlib.sha256(b"").hexdigest() == _EMPTY_DIGEST
    assert build_input_fingerprint([], []) == _EMPTY_DIGEST
