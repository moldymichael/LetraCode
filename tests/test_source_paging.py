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
    ('', 0, '', None), ('abc', 3, '', None),
    ('abc', 2, 'c', None), ('abc', 0, 'a', 1),
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
    {'max_chars': 1.5}, {'offset': 5}, {'start_line': 2},
    {'start_line': 0}, {'start_line': True}, {'start_line': '1'},
    {'max_lines': 0}, {'max_lines': 401}, {'max_lines': True},
])
def test_invalid_pagination_returns_an_executable_recovery_without_coverage(tmp_path, arguments):
    path = tmp_path / 'source.txt'
    path.write_text('keep')
    result = json.loads(reader(tmp_path).execute('read_file', {'path': str(path), **arguments}))
    assert 'error' in result
    assert result['code'] == 'invalid_pagination' and result['recoverable'] is True
    assert 'coverage' not in result and 'text' not in result
    retry = result['retry_read_file']
    assert retry == {'path': str(path), 'offset': 0, 'max_chars': 4000}
    assert json.loads(reader(tmp_path).execute('read_file', retry))['text'] == 'keep'
    assert path.read_text() == 'keep'


@pytest.mark.parametrize('arguments', [
    {'offset': 7, 'start_line': 1, 'max_lines': 180},
    {'offset': 7, 'start_line': 300, 'max_lines': None},
    {'start_line': 2},
])
def test_mixed_paging_preserves_exact_character_or_line_cursor(tmp_path, arguments):
    path = tmp_path / 'source.txt'
    path.write_bytes('\ufeffcafé\r\nβeta\r\nlast'.encode())
    page = read(reader(tmp_path), path, max_chars=3, **arguments)
    assert page['offset'] == 7 and page['text'] == 'βet'
    assert page['coverage']['ranges'] == [[7, 10]]
    assert page['next_read_file'] == {'path': str(path), 'offset': 10, 'max_chars': 3}
    tail = json.loads(reader(tmp_path).execute('read_file', page['next_read_file']))
    assert tail['text'] == 'a\r\n'


def test_character_size_with_line_limit_starts_at_beginning(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('first\nsecond')
    page = read(reader(tmp_path), path, max_chars=3, max_lines=1)
    assert page['offset'] == 0 and page['text'] == 'fir'


@pytest.mark.parametrize('arguments', [
    {'offset': 7, 'max_chars': 0, 'start_line': 1},
    {'start_line': 2, 'max_chars': 0},
    {'start_line': 2, 'max_lines': 0},
])
def test_invalid_page_size_recovery_preserves_valid_source_cursor(tmp_path, arguments):
    path = tmp_path / 'source.txt'
    path.write_bytes('\ufeffcafé\r\nβeta\r\nlast'.encode())
    tool = reader(tmp_path)
    result = json.loads(tool.execute('read_file', {'path': str(path), **arguments}))
    assert result['code'] == 'invalid_pagination'
    assert result['retry_read_file'] == {'path': str(path), 'offset': 7, 'max_chars': 4000}
    assert 'coverage' not in result
    assert json.loads(tool.execute('read_file', result['retry_read_file']))['text'] == 'βeta\r\nlast'


def test_unknown_character_cursor_never_uses_conflicting_line_start_for_recovery(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('first\nsecond')
    result = json.loads(reader(tmp_path).execute('read_file', {
        'path': str(path), 'offset': None, 'start_line': 2}))
    assert result['retry_read_file']['offset'] == 0


@pytest.mark.parametrize('contents,arguments', [
    ('', {'offset': 5}), ('', {'start_line': 2}),
    ('abc', {'offset': 9}), ('abc', {'start_line': 2, 'max_chars': 1}),
])
def test_cursor_beyond_source_cannot_masquerade_as_successful_eof(tmp_path, contents, arguments):
    path = tmp_path / 'source.txt'
    path.write_text(contents)
    result = json.loads(reader(tmp_path).execute('read_file', {'path': str(path), **arguments}))
    assert result['code'] == 'invalid_pagination'
    assert result['retry_read_file']['offset'] == 0
    assert 'next_offset' not in result and 'coverage' not in result


def test_pagination_recovery_does_not_replace_missing_source_or_permission_errors(tmp_path):
    path = tmp_path / 'missing.txt'
    result = json.loads(reader(tmp_path).execute('read_file', {'path': str(path), 'offset': -1}))
    assert 'error' in result and 'retry_read_file' not in result
    assert 'missing' in result['error'].lower()
    denied = json.loads(reader(tmp_path, computer_enabled=False).execute(
        'read_file', {'path': str(path), 'offset': -1}))
    assert 'denied' in denied and 'retry_read_file' not in denied


def test_canonical_continuation_reads_every_character_after_a_cut_line(tmp_path):
    contents = '\ufefffirst\r\n' + 'café' * 6000 + 'DEEP_MARKER\r\nlast\r\n'
    path = tmp_path / 'source.txt'
    path.write_bytes(contents.encode())
    tool = reader(tmp_path)
    page = read(tool, path, start_line=1, max_lines=2)
    covered_end = page['coverage']['ranges'][0][1]
    assert covered_end == 15995
    assert page['text'] == '1: \ufefffirst\n2: ' + ('café' * 4000)[:15987]
    pieces = [contents[:covered_end]]
    for _ in range(20):
        arguments = page['next_read_file']
        if arguments is None:
            break
        assert arguments['offset'] == covered_end
        assert set(arguments) == {'path', 'offset', 'max_chars'}
        page = json.loads(tool.execute('read_file', arguments))
        assert 'error' not in page
        assert page['sha256'] == hashlib.sha256(contents.encode()).hexdigest()
        assert page['coverage']['ranges'][0][0] == covered_end
        covered_end = page['coverage']['ranges'][0][1]
        pieces.append(page['text'])
    else:
        pytest.fail('Canonical source continuation failed to finish.')
    assert ''.join(pieces) == contents
    assert covered_end == len(contents)


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


def test_character_reads_obey_read_switch_and_are_not_limited_to_sources(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('private')
    assert 'denied' in json.loads(reader(tmp_path, computer_enabled=False).execute(
        'read_file', {'path': str(path), 'offset': 0}))
    tool = ToolExecutor([], tmp_path / 'appdata', lambda _: False, threading.Event())
    assert json.loads(tool.execute('read_file', {'path': str(path), 'offset': 0}))['text'] == 'private'


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
    files = ProjectFiles([str(tmp_path)])
    hits = files.search('OUTSIDE_SEARCH_BUDGET')
    assert hits == []
    assert files.report['scanned_files'] == 6
    assert files.report['search_limit_reached'] is True
    assert not any('OUTSIDE_SEARCH_BUDGET' in hit['text'] for hit in hits)
    assert files.report['searched_chars'] == 12 * 1024 * 1024


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
