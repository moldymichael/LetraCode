"""Public source paging/search regressions; all source and app data are disposable."""
import hashlib
import json
import threading
import zipfile

import pytest

from letracode.context import MAX_FILE, ProjectFiles
from letracode.tools import ToolExecutor


def reader(root, **kwargs):
    return ToolExecutor([str(root)], root / 'appdata', lambda _: False,
                        threading.Event(), web_enabled=False, **kwargs)


def read(tool, path, **kwargs):
    result = json.loads(tool.execute('read_file', {'path': str(path), **kwargs}))
    assert 'error' not in result and 'denied' not in result, result
    return result


@pytest.mark.parametrize('prefix', ['ordinary prose ' * 1600,
                                    'first\r\nsecond\r\n' + 'ordinary prose ' * 1600],
                         ids=['single-line', 'multiline'])
def test_search_reaches_long_paragraph_and_attributes_exact_characters(tmp_path, prefix):
    marker = 'UNIQUE_CONTINUITY_MARKER'
    contents = '\ufeff' + prefix + marker + '\r\nlast'
    path = tmp_path / 'chapter.txt'
    path.write_bytes(contents.encode('utf-8'))
    hits = ProjectFiles([str(tmp_path)]).search(marker)
    matching = [hit for hit in hits if marker in hit['text']]
    assert matching, 'Evidence past a long line prefix must be searchable.'
    hit = matching[0]
    assert hit['path'] == str(path)
    assert contents[hit['offset']:hit['offset'] + len(hit['text'])] == hit['text']
    assert hit['line'] == contents[:hit['offset']].count('\n') + 1
    assert len(hit['text']) <= 5000


def test_character_pages_preserve_unicode_bom_crlf_and_whole_snapshot_hash(tmp_path):
    contents = '\ufeff' + 'ordinary café 🐍 prose ' * 1300 + 'LATE_MARKER\r\n'
    path = tmp_path / 'chapter.txt'
    raw = contents.encode('utf-8')
    path.write_bytes(raw)
    tool = reader(tmp_path)
    offset, pages = 0, []
    for _ in range(30):
        page = read(tool, path, offset=offset, max_chars=4000)
        assert page['offset'] == offset
        assert page['total_chars'] == len(contents)
        assert len(page['text']) <= 4000
        assert page['sha256'] == hashlib.sha256(raw).hexdigest()
        assert page['editable'] is True
        pages.append(page['text'])
        if page['next_offset'] is None:
            break
        assert page['next_offset'] == offset + len(page['text']) > offset
        offset = page['next_offset']
    else:
        pytest.fail('Finite source paging failed to terminate.')
    assert ''.join(pages) == contents
    assert path.read_bytes() == raw


@pytest.mark.parametrize('contents,offset,want,next_offset', [
    ('', 0, '', None), ('', 5, '', None), ('abc', 3, '', None),
    ('abc', 9, '', None), ('abc', 2, 'c', None), ('abc', 0, 'a', 1),
])
def test_character_page_boundaries(tmp_path, contents, offset, want, next_offset):
    path = tmp_path / 'source.txt'
    path.write_text(contents)
    page = read(reader(tmp_path), path, offset=offset, max_chars=1)
    assert page['text'] == want
    assert page['next_offset'] == next_offset
    assert page['total_chars'] == len(contents)


@pytest.mark.parametrize('arguments', [
    {'offset': -1}, {'offset': True}, {'offset': '0'}, {'offset': None},
    {'max_chars': True}, {'max_chars': 0}, {'max_chars': 16001},
    {'max_chars': 1.5}, {'offset': 0, 'start_line': 1},
    {'max_chars': 100, 'max_lines': 3},
])
def test_character_mode_rejects_invalid_and_mixed_arguments(tmp_path, arguments):
    path = tmp_path / 'source.txt'
    path.write_text('keep')
    result = json.loads(reader(tmp_path).execute('read_file', {'path': str(path), **arguments}))
    assert 'error' in result
    assert path.read_text() == 'keep'


def test_source_changes_between_pages_are_visible(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('before source')
    tool = reader(tmp_path)
    first = read(tool, path, offset=0, max_chars=3)
    path.write_text('after source')
    second = read(tool, path, offset=3, max_chars=3)
    assert first['sha256'] != second['sha256']
    assert second['sha256'] == hashlib.sha256(b'after source').hexdigest()
    assert second['text'] == 'er '


def test_line_mode_returns_intraline_character_cursor_without_losing_tail(tmp_path):
    contents = 'first\r\n' + 'café' * 6000 + 'DEEP_MARKER\r\nlast\r\n'
    path = tmp_path / 'source.txt'
    path.write_bytes(contents.encode())
    tool = reader(tmp_path)
    page = read(tool, path)
    assert page['start_line'] == 1 and page['total_lines'] == 3
    assert page['text'].startswith('1: first\n2: café')
    assert len(page['text']) <= 16000
    assert page['truncated'] is True and page['output_truncated'] is True
    assert page['source_truncated'] is False
    offset = page['next_offset']
    # The first page has 15,988 raw characters of line 2 after line labels.
    assert offset == 15995
    tail = read(tool, path, offset=offset, max_chars=16000)
    assert tail['text'] == contents[offset:]
    assert 'DEEP_MARKER' in tail['text'] and tail['next_offset'] is None


def docx(path, text):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')


def test_docx_paging_is_readonly_with_explicit_extraction_coverage_and_version(tmp_path):
    path = tmp_path / 'source.docx'
    docx(path, 'a' * (MAX_FILE + 10))
    tool = reader(tmp_path)
    page = read(tool, path, offset=MAX_FILE - 3, max_chars=100)
    assert page['text'] == 'aaa' and page['next_offset'] is None
    assert page['total_chars'] == MAX_FILE
    assert page['editable'] is False and page['sha256'] is None
    assert page['source_truncated'] is True
    assert page['extraction']['coverage'] == 'DOCX body paragraphs and tables; excludes headers, footers, comments, images and deleted text.'
    assert page['extraction']['version']
    assert page['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    first_hash = page['source_sha256']
    docx(path, 'changed')
    changed = read(tool, path, offset=0)
    assert changed['source_sha256'] != first_hash
    assert changed['source_truncated'] is False


def test_character_reads_keep_computer_disabled_and_outside_approval_boundary(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('private')
    assert 'denied' in json.loads(reader(tmp_path, computer_enabled=False).execute(
        'read_file', {'path': str(path), 'offset': 0}))
    tool = ToolExecutor([], tmp_path / 'appdata', lambda _: False, threading.Event())
    assert 'denied' in json.loads(tool.execute('read_file', {'path': str(path), 'offset': 0}))


def test_search_overlap_keeps_boundary_marker_and_initial_file_diversity(tmp_path):
    marker = 'BOUNDARY_CONTINUITY_MARKER'
    first = tmp_path / 'a.txt'
    first.write_text('x' * 4990 + marker + 'x' * 20000 + marker)
    second = tmp_path / 'b.txt'
    second.write_text('another ' + marker)
    hits = ProjectFiles([str(tmp_path)]).search(marker, limit=2)
    assert {hit['path'] for hit in hits} == {str(first), str(second)}
    assert all(marker in hit['text'] for hit in hits)


def test_search_bounds_total_supported_character_work(tmp_path):
    # Six full sources exhaust the existing 12 MiB character-work budget.
    # A seventh file must not be scanned merely because no earlier file matches.
    for i in range(6):
        (tmp_path / f'{i}.txt').write_bytes(b'x' * MAX_FILE)
    (tmp_path / '7.txt').write_text('OUTSIDE_SEARCH_BUDGET')
    hits = ProjectFiles([str(tmp_path)]).search('OUTSIDE_SEARCH_BUDGET')
    assert len(hits) == 6
    assert not any('OUTSIDE_SEARCH_BUDGET' in hit['text'] for hit in hits)
    assert sum(hit['searched_chars'] for hit in hits) == 12 * 1024 * 1024


def pdf(path, text):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 10 780 Td (' + text.encode('ascii') + b') Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as output:
        writer.write(output)


def test_pdf_long_page_text_is_reachable_and_readonly(tmp_path):
    path = tmp_path / 'chapter.pdf'
    text = 'ordinary ' * 4000 + 'DEEP_PDF_MARKER'
    pdf(path, text)
    tool = reader(tmp_path)
    first = read(tool, path, offset=0, max_chars=16000)
    assert first['editable'] is False and first['sha256'] is None
    assert first['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first['source_truncated'] is False
    assert first['extraction']['total_pages'] == 1
    assert 'no OCR' in first['extraction']['coverage']
    assert first['extraction']['version']
    pieces, offset = [first['text']], first['next_offset']
    while offset is not None:
        page = read(tool, path, offset=offset, max_chars=16000)
        assert page['source_sha256'] == first['source_sha256']
        pieces.append(page['text'])
        offset = page['next_offset']
    assert ''.join(pieces) == '[Page 1]\n' + text
    assert any('DEEP_PDF_MARKER' in hit['text'] for hit in
               ProjectFiles([str(tmp_path)]).search('DEEP_PDF_MARKER'))


def test_pdf_extraction_limit_is_separate_from_terminal_page(tmp_path, monkeypatch):
    import letracode.context as context
    path = tmp_path / 'chapter.pdf'
    pdf(path, 'a' * 100)
    monkeypatch.setattr(context, 'MAX_FILE', 50)
    page = read(reader(tmp_path), path, offset=48, max_chars=10)
    assert page['text'] == 'aa' and page['total_chars'] == 50
    assert page['next_offset'] is None and page['output_truncated'] is False
    assert page['source_truncated'] is True and page['truncated'] is True


def test_binary_document_reads_refuse_hardlinks(tmp_path):
    import os
    path = tmp_path / 'source.docx'
    docx(path, 'Private text')
    os.link(path, tmp_path / 'another.docx')
    result = json.loads(reader(tmp_path).execute('read_file', {'path': str(path), 'offset': 0}))
    assert 'error' in result and 'linked' in result['error'].lower()


def test_docx_paragraph_separator_at_limit_is_reported_as_source_truncation(tmp_path, monkeypatch):
    import letracode.context as context
    path = tmp_path / 'source.docx'
    # The supported extraction is "abc\n"; its last character is outside limit 3.
    docx(path, 'abc</w:t></w:r></w:p><w:p><w:r><w:t>')
    monkeypatch.setattr(context, 'MAX_FILE', 3)
    page = read(reader(tmp_path), path, offset=0)
    assert page['text'] == 'abc'
    assert page['source_truncated'] is True
