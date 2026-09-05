from __future__ import annotations

import builtins
from pathlib import Path
import zipfile

import pytest

from letracode.context import MAX_FILE, read_text


NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_docx(path: Path, body: str, namespace=NAMESPACE):
    xml = f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return path


@pytest.mark.parametrize("namespace", [NAMESPACE, "http://purl.oclc.org/ooxml/wordprocessingml/main"])
def test_docx_reads_paragraphs_and_tables_without_python_docx(tmp_path, monkeypatch, namespace):
    original_import = builtins.__import__

    def without_docx(name, *args, **kwargs):
        if name == "docx" or name.startswith("docx."):
            raise ImportError("python-docx is unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_docx)
    path = write_docx(tmp_path / "project.docx", '''
      <w:p><w:r><w:t>Before &amp; </w:t></w:r><w:r><w:t>after</w:t></w:r></w:p>
      <w:tbl><w:tr>
        <w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
      </w:tr><w:tr>
        <w:tc><w:p><w:r><w:t>LetraCode</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Local</w:t></w:r></w:p>
              <w:p><w:r><w:t>Private</w:t></w:r></w:p></w:tc>
      </w:tr></w:tbl>
      <w:p><w:hyperlink><w:r><w:t>Reference</w:t><w:tab/><w:t>two</w:t>
        <w:br/><w:t>Finál</w:t></w:r></w:hyperlink></w:p>
    ''', namespace=namespace)
    assert read_text(path) == "Before & after\nName | Value\nLetraCode | Local\nPrivate\nReference\ttwo\nFinál"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_docx_rejects_document_type_and_entity_expansion(tmp_path, encoding):
    xml = (f'<?xml version="1.0" encoding="{encoding}"?>'
           '<!DOCTYPE document [<!ENTITY payload "do not expand this">]>'
           f'<w:document xmlns:w="{NAMESPACE}"><w:body>'
           '<w:p><w:r><w:t>&payload;</w:t></w:r></w:p></w:body></w:document>')
    path = tmp_path / "entities.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml.encode(encoding))
    with pytest.raises(ValueError, match="DOCTYPE|document type"):
        read_text(path)


def test_docx_rejects_expanded_zip_limit(tmp_path):
    path = write_docx(tmp_path / "oversized.docx", "")
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/media/large.bin", b"x" * (50 * 1024 * 1024))
    with pytest.raises(ValueError, match="expanded contents"):
        read_text(path)


def test_docx_bounds_extracted_text(tmp_path):
    path = write_docx(tmp_path / "long.docx", f'<w:p><w:r><w:t>{"a" * (MAX_FILE + 50)}</w:t></w:r></w:p>')
    assert read_text(path) == "a" * MAX_FILE


def test_docx_formatting_tab_stops_do_not_become_text(tmp_path):
    path = write_docx(tmp_path / "tab-stops.docx", '''
      <w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>
        <w:r><w:t>Plain text</w:t><w:tab/><w:t>after an actual tab</w:t></w:r>
      </w:p>
    ''')
    assert read_text(path) == "Plain text\tafter an actual tab"


def test_docx_rejects_large_body_xml(tmp_path):
    path = write_docx(tmp_path / "large-body.docx", " " * (20 * 1024 * 1024))
    with pytest.raises(ValueError, match="body XML exceeds"):
        read_text(path)


@pytest.mark.parametrize("contents", [b"not a zip", b"PK\x03\x04"])
def test_docx_invalid_file_has_readable_error(tmp_path, contents):
    path = tmp_path / "broken.docx"
    path.write_bytes(contents)
    with pytest.raises(ValueError, match="DOCX"):
        read_text(path)
