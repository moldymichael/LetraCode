"""Bounded DOCX body-text extraction using only Python's standard library."""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


class _NoDocumentType(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        # Reject before any entity expansion, including in UTF-16 XML. Never
        # load document relationships, external entities, or embedded objects.
        raise ValueError('DOCX document type declarations (DOCTYPE) are not supported.')


def read_docx(path: Path, text_limit: int, document_limit: int) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError('DOCX expanded contents exceed the extraction limit.')
            info = archive.getinfo('word/document.xml')
            if info.file_size > document_limit:
                raise ValueError('DOCX body XML exceeds the 20 MiB extraction limit.')
            with archive.open(info) as body_file:
                raw = body_file.read(document_limit + 1)
            if len(raw) > document_limit:
                raise ValueError('DOCX body XML exceeds the 20 MiB extraction limit.')
        document = ET.fromstring(raw, parser=ET.XMLParser(target=_NoDocumentType()))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('Cannot read this DOCX document; it is damaged or has no valid document body.') from error

    namespaces = (
        'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'http://purl.oclc.org/ooxml/wordprocessingml/main',
    )
    namespace = next((f'{{{ns}}}' for ns in namespaces
                      if document.tag == f'{{{ns}}}document'), None)
    if namespace is None:
        raise ValueError('This DOCX does not contain a supported Word document.')
    body = document.find(namespace + 'body')
    if body is None:
        raise ValueError('This DOCX has no document body.')

    def inline(element):
        if element.tag in {namespace + 'del', namespace + 'moveFrom',
                           namespace + 'pPr', namespace + 'rPr'}:
            return
        if element.tag == namespace + 't':
            yield element.text or ''
        elif element.tag == namespace + 'tab':
            yield '\t'
        elif element.tag in {namespace + 'br', namespace + 'cr'}:
            yield '\n'
        else:
            for child in element:
                yield from inline(child)

    def blocks(element):
        for child in element:
            if child.tag == namespace + 'p':
                yield ''.join(inline(child))
            elif child.tag == namespace + 'tr':
                yield ' | '.join('\n'.join(blocks(cell))
                                 for cell in child if cell.tag == namespace + 'tc')
            elif child.tag not in {namespace + 'del', namespace + 'moveFrom'}:
                # Walk tables and content controls in document order. Paragraph
                # text is consumed above so it cannot be counted twice.
                yield from blocks(child)

    parts = []
    remaining = text_limit
    for part in blocks(body):
        if remaining <= 0:
            break
        # A paragraph separator is extracted text too, including when it is
        # exactly the final character allowed by the caller's coverage probe.
        parts.append((('\n' if parts else '') + part)[:remaining])
        remaining -= len(parts[-1])
    return ''.join(parts)
