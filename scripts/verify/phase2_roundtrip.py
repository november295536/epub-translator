#!/usr/bin/env python3
"""Phase 2 check: every chapter file parses as XML and re-serializes unchanged.

For each directory of --projects-dir that contains a project.json, opens the
project's source EPUB (its own copy source.epub, else the stored epub_path)
and reads the package with the Phase 2 reader. Every XHTML item of the OPF
manifest (chapters, navigation documents, items outside the spine) is parsed
with the strict XML parser and serialized again without edits; the chapter
counts as "round trip equal" when both are equal under XML canonicalization
(C14N 2.0, comments kept). The root element is serialized in a document of
its own: serializing the parsed document as a whole lets libxml2 add a
<meta http-equiv="Content-Type"> to documents with an XHTML 1.0 DOCTYPE. How
many chapters survive that whole-document serialization unchanged is printed
too ("whole-document equal"), as information for the export (Phase 3); it is
not a pass condition.

A project FAILs when a chapter is missing, does not parse, or does not round
trip, when the source cannot be read, or when any file in the project
directory changed during the check.

Read-only. Without --anonymize it prints project directory names. With
--anonymize it prints project-N labels (alphabetical position among the
directories of --projects-dir that contain a project.json) and no names or
paths.

Usage:
    python3 scripts/verify/phase2_roundtrip.py [--projects-dir projects] [--anonymize]

Exit code: 0 when every project PASSes, 1 when any FAILs, 2 when the projects
directory cannot be listed.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import sys
import zipfile

# At index 0, ahead of everything (see phase0_builder_alignment.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from lxml import etree  # noqa: E402

from src.epub.extractor import XHTML_MEDIA_TYPE, parse_xml, read_package  # noqa: E402

PROJECT_FILE = 'project.json'
SOURCE_FILE = 'source.epub'


def snapshot(project_dir: str) -> list:
    """Name, size and modification time of every file under the directory."""
    out = []
    for root, dirs, files in os.walk(project_dir):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            st = os.stat(path)
            out.append((os.path.relpath(path, project_dir), st.st_size, st.st_mtime_ns))
    return out


def source_path(project_dir: str) -> str:
    own = os.path.join(project_dir, SOURCE_FILE)
    if os.path.isfile(own):
        return own
    with open(os.path.join(project_dir, PROJECT_FILE), encoding='utf-8') as f:
        return json.load(f)['epub_path']


def canonical(data: bytes) -> str:
    return etree.canonicalize(from_file=io.BytesIO(data), with_comments=True)


def check_epub(path: str) -> tuple[int, int, int]:
    """(XHTML items, round trip equal, whole-document equal)."""
    with open(path, 'rb') as f:
        zf = zipfile.ZipFile(io.BytesIO(f.read()))
    _, manifest, _ = read_package(zf)
    items = equal = whole = 0
    for member, media_type, _ in manifest.values():
        if media_type != XHTML_MEDIA_TYPE:
            continue
        items += 1
        try:
            data = zf.read(member)
            root = parse_xml(data)
        except (KeyError, etree.XMLSyntaxError):
            continue
        source = canonical(data)
        equal += canonical(etree.tostring(copy.deepcopy(root), encoding='utf-8')) == source
        whole += canonical(etree.tostring(root.getroottree(), encoding='utf-8')) == source
    return items, equal, whole


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--projects-dir', default='projects',
                        help='Directory that holds the project folders (default: projects)')
    parser.add_argument('--anonymize', action='store_true',
                        help='Print project-N labels; omit names and paths')
    args = parser.parse_args(argv)

    shown_dir = '<projects-dir>' if args.anonymize else args.projects_dir
    try:
        entries = os.listdir(args.projects_dir)
    except OSError as e:
        print(f'ERROR cannot list projects directory {shown_dir} ({type(e).__name__})')
        return 2

    names = sorted(name for name in entries
                   if os.path.isfile(os.path.join(args.projects_dir, name, PROJECT_FILE)))
    totals = [0, 0, 0]
    failed = 0
    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        project_dir = os.path.join(args.projects_dir, name)
        before = snapshot(project_dir)
        try:
            counts = check_epub(source_path(project_dir))
        except Exception as e:  # an unreadable source is a FAIL, never a crash
            failed += 1
            print(f'FAIL {label}: cannot read the source ({type(e).__name__})')
            continue
        totals = [t + c for t, c in zip(totals, counts)]
        items, equal, whole = counts
        line = (f'{label}: chapters {items}, round trip equal {equal}, '
                f'whole-document equal {whole}')
        problems = []
        if equal != items:
            problems.append(f'{items - equal} chapters missing, unparseable or changed')
        if snapshot(project_dir) != before:
            problems.append('files in the project directory changed')
        if problems:
            failed += 1
            print(f'FAIL {line}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')

    print(f'TOTAL projects {len(names)}  chapters {totals[0]}  round trip equal {totals[1]}  '
          f'whole-document equal {totals[2]}  failed projects {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
