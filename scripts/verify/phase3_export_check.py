#!/usr/bin/env python3
"""Phase 3 check: exports of copies of the existing projects keep the source intact.

Copies every project of --projects-dir (its project.json, source.epub and
glossary.json) into a new temporary directory outside the repository and
outside --projects-dir, migrates the copies that are schema 1 (the code of
the migrate command), exports each copy in all three formats into that
directory, and checks every export against the project's source EPUB:

- `mimetype` is the first member and stored; no member name occurs twice;
- every member outside the allowed set (chapters exported with a
  translation, the OPF, the NCX, navigation documents, the added bilingual
  stylesheet) is present with identical bytes, and nothing else is added;
- every XHTML member is well-formed XML;
- the <link> and <style> elements of every chapter head equal the source's
  (a bilingual export may add one <link> to its stylesheet, last);
- no id occurs twice in a chapter unless it already did in the source;
- every internal link with a fragment (any `href`, NCX `src`) resolves to an
  element with that id (or `name`), unless the same link already fails in
  the source;
- every chapter using a bilingual class links a stylesheet that exists in
  the archive and in the manifest and defines the bilingual classes;
- SVG attribute names keep their case (every attribute name on an SVG
  element of an output chapter occurs on an SVG element of the source
  chapter);
- epubcheck, when it is on PATH, reports no error the source does not have,
  counted per message ID (ERROR and FATAL) as reported locations:
  chinese-only: the export's count is not above the source's;
  bilingual: every ID also occurs in the source, and the export's count is
  at most twice the source's. The bilingual rule is looser on purpose: the
  translated copy of a block repeats the source element's attributes and
  markup, so each source error inside a translated block can appear once
  more; an error ID the source does not have is still a failure.
  Only counts and message IDs are printed (messages can quote the book).

It prints per project and in total: translated segments exported as
translation and kept in the original language, with counts per reason
(the export report), and the TOC labels changed, out of all entries and
out of the entries that point into a chapter with at least one segment
exported as translation. Everything in the
temporary directory is removed at the end. The projects directory is only
read: a project FAILs if any file in it changed during the run.

Without --anonymize it prints project directory names. With --anonymize it
prints project-N labels (alphabetical position among the directories of
--projects-dir that contain a project.json) and no names or paths.

Usage:
    python3 scripts/verify/phase3_export_check.py [--projects-dir projects] [--anonymize]

Exit code: 0 when every project PASSes, 1 when any FAILs, 2 when the projects
directory cannot be listed or the temporary directory would be inside the
repository or the projects directory (checked before it is created).
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from urllib.parse import unquote, urlsplit

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
# At index 0, ahead of everything (see phase2_roundtrip.py)
sys.path.insert(0, ROOT)

from lxml import etree  # noqa: E402

from src.core.migrate import migrate_project  # noqa: E402
from src.core.project import ProjectManager  # noqa: E402
from src.epub.blocks import localname, serialize  # noqa: E402
from src.epub.exporter import EPUB_TYPE, FORMATS, NCX_MEDIA_TYPE, export_project  # noqa: E402
from src.epub.extractor import (  # noqa: E402
    XHTML_MEDIA_TYPE, package_path, parse_xml, read_package,
)

PROJECT_FILE = 'project.json'
COPIED = (PROJECT_FILE, 'source.epub', 'glossary.json')
SVG_NS = 'http://www.w3.org/2000/svg'
BILINGUAL_CLASSES = ('bilingual-container', 'bilingual-vertical')


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


class Archive:
    """An EPUB opened for checking: members, package, parsed XHTML."""

    def __init__(self, path: str):
        self.zip = zipfile.ZipFile(path)
        self.names = set(self.zip.namelist())
        self.opf_path = package_path(self.zip)
        _, self.manifest, _ = read_package(self.zip)
        self.xhtml = sorted({p for p, mt, _ in self.manifest.values()
                             if mt == XHTML_MEDIA_TYPE and p in self.names})
        self._trees: dict = {}

    def tree(self, name: str):
        """The parsed member, or None when it is missing or not well-formed."""
        if name not in self._trees:
            try:
                self._trees[name] = parse_xml(self.zip.read(name))
            except (KeyError, etree.XMLSyntaxError):
                self._trees[name] = None
        return self._trees[name]

    def anchors(self, name: str) -> set:
        root = self.tree(name)
        if root is None:
            return set()
        return {v for e in root.iter(etree.Element)
                for v in (e.get('id'), e.get('name') if localname(e) == 'a' else None) if v}


def _resolve(base: str, href: str):
    """(member, fragment) of an internal link with a fragment, else None."""
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.fragment:
        return None
    target = base
    if parts.path:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parts.path)))
    return target, unquote(parts.fragment)


def broken_links(archive: Archive, ncx: list) -> set:
    """(member, href) of every internal fragment link that does not resolve."""
    out = set()
    for name in archive.xhtml + ncx:
        root = archive.tree(name)
        if root is None:
            continue
        for e in root.iter(etree.Element):
            href = e.get('src') if localname(e) == 'content' else e.get('href')
            resolved = _resolve(name, href) if href else None
            if resolved and (resolved[0] not in archive.names
                             or resolved[1] not in archive.anchors(resolved[0])):
                out.add((name, href))
    return out


def head_items(root) -> list:
    head = next((c for c in root if localname(c) == 'head'), None)
    return [] if head is None else [c for c in head if localname(c) in ('link', 'style')]


def id_counts(root) -> Counter:
    return Counter(e.get('id') for e in root.iter(etree.Element) if e.get('id'))


def new_duplicates(root, src_root) -> set:
    """Ids that occur more than once and more often than in the source."""
    src = id_counts(src_root)
    return {i for i, n in id_counts(root).items() if n > 1 and n > src[i]}


def svg_attributes(root) -> set:
    return {a for e in root.iter(etree.Element)
            if etree.QName(e).namespace == SVG_NS for a in e.attrib}


def uses_bilingual(root) -> bool:
    return any(c in (e.get('class') or '').split()
               for e in root.iter(etree.Element) for c in BILINGUAL_CLASSES)


def _entries(archive: Archive):
    """(member, is the table of contents, label element, href) of every
    labelled entry of the NCX and navigation documents: NCX navMap
    navPoints and links of the nav whose epub:type holds `toc` are the
    table of contents; page lists, nav lists, landmarks, docTitle,
    docAuthor and other links are not."""
    for path, media_type, is_nav in archive.manifest.values():
        root = archive.tree(path) if (is_nav or media_type == NCX_MEDIA_TYPE) else None
        if root is None:
            continue
        if is_nav:
            toc_links = {a for nav in root.iter(etree.Element)
                         if localname(nav) == 'nav' and 'toc' in (nav.get(EPUB_TYPE) or '').split()
                         for a in nav.iter(etree.Element) if localname(a) == 'a'}
            for e in root.iter(etree.Element):
                if localname(e) == 'a':
                    yield path, e in toc_links, e, e.get('href')
            continue
        for e in root.iter(etree.Element):
            if localname(e) != 'text':
                continue
            point = e.getparent().getparent() if e.getparent() is not None else None
            in_map = (point is not None and localname(point) == 'navPoint'
                      and any(localname(a) == 'navMap' for a in point.iterancestors()))
            content = None if point is None else next(
                (c for c in point if localname(c) == 'content'), None)
            yield path, in_map, e, None if content is None else content.get('src')


def _label(e) -> str:
    return ' '.join(''.join(e.itertext()).split())


def toc_labels(archive: Archive) -> list:
    """(member, position, label, target member or None) of every table of
    contents entry, numbered within its file."""
    out, positions = [], Counter()
    for path, toc, e, href in _entries(archive):
        if not toc:
            continue
        resolved = _resolve(path, href + '#x') if href else None
        out.append((path, positions[path], _label(e), resolved[0] if resolved else None))
        positions[path] += 1
    return out


def other_labels(archive: Archive) -> list:
    """(member, label) of every labelled entry outside the table of contents."""
    return [(path, _label(e)) for path, toc, e, _ in _entries(archive) if not toc]


def epubcheck_errors(path: str):
    """Counter of epubcheck's ERROR and FATAL messages for the file, by
    message ID (one per reported location); None when epubcheck is not on
    PATH. Messages themselves are never printed: they can quote the book."""
    tool = shutil.which('epubcheck')
    if tool is None:
        return None
    fd, report = tempfile.mkstemp(suffix='.json')  # never next to the checked file
    os.close(fd)
    try:
        subprocess.run([tool, path, '--json', report], capture_output=True, text=True)
        with open(report, encoding='utf-8') as f:
            messages = json.load(f)['messages']
    finally:
        os.remove(report)
    counts = Counter()
    for m in messages:
        if m.get('severity') in ('ERROR', 'FATAL'):
            counts[m['ID']] += max(1, len(m.get('locations') or []))
    return counts


def new_errors(source: Counter, export: Counter, factor: int) -> Counter:
    """Errors of the export beyond `factor` times the source's, by
    message ID (an ID the source does not have counts in full)."""
    return Counter({i: n - factor * source[i] for i, n in export.items()
                    if n > factor * source[i]})


def check_export(source: Archive, output_path: str, report, fmt: str, source_errors,
                 epubcheck: dict) -> list[str]:
    """Problems of one export (empty when it passes)."""
    out = Archive(output_path)
    problems = []
    infos = out.zip.infolist()
    if infos[0].filename != 'mimetype' or infos[0].compress_type != zipfile.ZIP_STORED:
        problems.append('mimetype not first and stored')
    if len(infos) != len(out.names):
        problems.append('a member is written twice')

    by_id = {i: p for i, (p, _, _) in source.manifest.items()}
    ncx = [p for p, mt, _ in source.manifest.values() if mt == NCX_MEDIA_TYPE and p in source.names]
    navs = [p for p, _, nav in source.manifest.values() if nav]
    translated = {by_id.get(c.chapter_id) for c in report.chapters if c.translated}
    css = {p for p, mt, _ in out.manifest.values() if mt == 'text/css'} - source.names
    if len(css) > (0 if fmt == 'chinese-only' else 1):
        problems.append(f'{len(css)} stylesheets added')
    allowed = translated | {source.opf_path} | set(ncx) | set(navs) | css
    if out.names - source.names - css:
        problems.append(f'{len(out.names - source.names - css)} members added')
    missing = source.names - out.names
    if missing:
        problems.append(f'{len(missing)} source members missing')
    changed = sum(1 for name in source.names - allowed - missing
                  if out.zip.read(name) != source.zip.read(name))
    if changed:
        problems.append(f'{changed} members outside the allowed set differ')
    problems += member_problems(source, out, allowed)
    if other_labels(out) != other_labels(source):
        problems.append('labels outside the table of contents changed')

    counts = Counter()
    for name in out.xhtml:
        root, src_root = out.tree(name), source.tree(name)
        if root is None:
            counts['not well-formed'] += 1
        elif src_root is not None:
            counts.update(chapter_problems(out, name, root, src_root, css))
    problems += [f'{n} chapters: {what}' for what, n in sorted(counts.items())]
    broken = broken_links(out, ncx) - broken_links(source, ncx)
    if broken:
        problems.append(f'{len(broken)} fragment links do not resolve')
    if source_errors is not None:
        errors = epubcheck_errors(output_path)
        epubcheck[fmt] = sum(errors.values())
        new = new_errors(source_errors, errors, 1 if fmt == 'chinese-only' else 2)
        if new:
            problems.append('epubcheck new errors '
                            + ', '.join(f'{i} {n}' for i, n in sorted(new.items())))
    return problems


def member_problems(source: Archive, out: Archive, allowed: set) -> list[str]:
    """Order of the members, and the stored form of each untouched one:
    raw name bytes, compression, date and attributes."""
    problems = []
    order = list(dict.fromkeys(['mimetype'] + source.zip.namelist()))
    if [n for n in out.zip.namelist() if n in source.names] != [n for n in order
                                                                 if n in out.names]:
        problems.append('member order differs')
    last = {i.filename: i for i in source.zip.infolist()}
    fields = ('orig_filename', 'compress_type', 'date_time', 'external_attr', 'internal_attr',
              'create_system', 'extra', 'comment')
    differ = 0
    for info in out.zip.infolist():
        src = last.get(info.filename)
        if src is None or info.filename in allowed:
            continue
        stored = [getattr(info, f) for f in fields] + [info.flag_bits & 0x800]
        expect = [getattr(src, f) for f in fields] + [src.flag_bits & 0x800]
        if info.filename == 'mimetype':  # stored, no extra field (OCF)
            expect[1], expect[6] = zipfile.ZIP_STORED, b''
        differ += stored != expect
    if differ:
        problems.append(f'{differ} untouched members stored differently')
    return problems


def chapter_problems(out: Archive, name: str, root, src_root, css: set) -> list[str]:
    """Problems of one output chapter compared with its source."""
    problems = []
    items = [serialize(e) for e in head_items(root)]
    src_items = [serialize(e) for e in head_items(src_root)]
    if css and len(items) == len(src_items) + 1:
        added = head_items(root)[-1]
        if _resolve(name, (added.get('href') or '') + '#x') == (next(iter(css)), 'x'):
            items.pop()
    if items != src_items:
        problems.append('head links or styles differ')
    if new_duplicates(root, src_root):
        problems.append('duplicate id')
    if not svg_attributes(root) <= svg_attributes(src_root):
        problems.append('SVG attribute case changed')
    if uses_bilingual(root) and not _links_bilingual_css(out, name, root):
        problems.append('bilingual chapter without its stylesheet')
    return problems


def _links_bilingual_css(archive: Archive, name: str, root) -> bool:
    manifest = {p for p, _, _ in archive.manifest.values()}
    for link in head_items(root):
        if localname(link) != 'link' or 'stylesheet' not in (link.get('rel') or '').split():
            continue
        resolved = _resolve(name, (link.get('href') or '') + '#x')
        if resolved and resolved[0] in archive.names and resolved[0] in manifest:
            if all(f'.{c}' in archive.zip.read(resolved[0]).decode('utf-8', 'replace')
                   for c in BILINGUAL_CLASSES):
                return True
    return False


def check_copy(copy_dir: str, out_dir: str) -> tuple[dict, list[str]]:
    problems = []
    pm = ProjectManager(copy_dir)
    if pm.load().schema != 2:
        migration = migrate_project(copy_dir)
        if migration.unmatched:
            return {}, [f'{len(migration.unmatched)} chapters unmatched by the migration']
        pm.load()
    source = Archive(pm.data.epub_path)
    src_labels = toc_labels(source)
    source_errors = epubcheck_errors(pm.data.epub_path)
    result = {'exports': len(FORMATS), 'entries': len(src_labels), 'epubcheck': {},
              'source_errors': source_errors}
    for fmt in FORMATS:  # chinese-only first: its counts are the project's
        output = os.path.join(out_dir, f'{fmt}.epub')
        report = export_project(copy_dir, output, fmt)
        problems += [f'{fmt}: {p}' for p in check_export(source, output, report, fmt,
                                                          source_errors, result['epubcheck'])]
        counts = (report.translated, report.reasons())
        if fmt == FORMATS[0]:
            result['translated'], result['kept'] = counts
            out_labels = toc_labels(Archive(output))
            if [e[:2] for e in out_labels] != [e[:2] for e in src_labels]:
                problems.append(f'{fmt}: table of contents entries differ')
            result['labels'] = sum(a[2] != b[2] for a, b in zip(src_labels, out_labels))
            by_id = {i: p for i, (p, _, _) in source.manifest.items()}
            exported = {by_id.get(c.chapter_id) for c in report.chapters if c.translated}
            result['into'] = sum(entry[3] in exported for entry in src_labels)
        elif counts != (result['translated'], result['kept']):
            problems.append(f'{fmt}: segment counts differ from chinese-only')
    result['epubcheck_failed'] = sum('epubcheck new errors' in p for p in problems)
    return result, problems


def epubcheck_text(r: dict) -> str:
    if r['source_errors'] is None:
        return ''
    ids = ' '.join(f'{i}:{n}' for i, n in sorted(r['source_errors'].items()))
    exports = '/'.join(str(r['epubcheck'][fmt]) for fmt in FORMATS)
    return (f", epubcheck errors source {sum(r['source_errors'].values())}"
            + (f' ({ids})' if ids else '') + f', exports {exports}')


def kept_text(kept: Counter) -> str:
    inner = ', '.join(f'{r} {n}' for r, n in sorted(kept.items()))
    return f"kept original {sum(kept.values())}" + (f' ({inner})' if inner else '')


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

    parent = os.path.realpath(tempfile.gettempdir())
    for place, what in ((ROOT, 'the repository'),
                        (os.path.realpath(args.projects_dir), 'the projects directory')):
        if os.path.commonpath([parent, place]) == place:
            print(f'ERROR the temporary directory would be inside {what}')
            return 2
    work = tempfile.mkdtemp(prefix='phase3-export-', dir=parent)
    try:
        return run(args, names, work)
    finally:
        shutil.rmtree(work)


def run(args, names: list[str], work: str) -> int:
    if shutil.which('epubcheck') is None:
        print('epubcheck not available (not on PATH): not run')
    else:
        print(f'epubcheck found; errors compared by message ID ({", ".join(FORMATS)})')
    epubcheck = shutil.which('epubcheck') is not None
    totals = {'exports': 0, 'translated': 0, 'kept': Counter(), 'labels': 0, 'entries': 0,
              'into': 0,
              'epubcheck_failed': 0}
    failed = 0
    for index, name in enumerate(names, 1):
        label = f'project-{index}' if args.anonymize else name
        project_dir = os.path.join(args.projects_dir, name)
        # numbered scratch directories: no project name reaches a path
        copy_dir = os.path.join(work, f'{index}', 'project')
        out_dir = os.path.join(work, f'{index}', 'exports')
        before = None
        try:
            before = snapshot(project_dir)
            os.makedirs(copy_dir)
            os.makedirs(out_dir)
            for file in COPIED:
                if os.path.isfile(os.path.join(project_dir, file)):
                    shutil.copy2(os.path.join(project_dir, file), copy_dir)
            r, problems = check_copy(copy_dir, out_dir)
        except Exception as e:  # any failure is a FAIL, never a crash
            r, problems = {}, [f'export check raised {type(e).__name__}']
        finally:
            if before is not None and snapshot(project_dir) != before:
                problems.append('files in the project directory changed')
        if not r:
            failed += 1
            print(f'FAIL {label}: ' + '; '.join(problems))
            continue
        for key in ('exports', 'translated', 'labels', 'entries', 'into', 'epubcheck_failed'):
            totals[key] += r[key]
        totals['kept'] += r['kept']
        line = (f"{label}: exports {r['exports']}, translated segments "
                f"{r['translated'] + sum(r['kept'].values())}: as translation {r['translated']}, "
                f"{kept_text(r['kept'])}, toc labels changed {r['labels']}/{r['entries']} "
                f"({r['into']} into translated chapters)"
                + epubcheck_text(r))
        if problems:
            failed += 1
            print(f'FAIL {line}; ' + '; '.join(problems))
        else:
            print(f'PASS {line}')

    kept = totals['kept']
    print(f"TOTAL projects {len(names)}  exports {totals['exports']}  translated segments "
          f"{totals['translated'] + sum(kept.values())}  as translation {totals['translated']}  "
          f"{kept_text(kept)}  toc labels changed {totals['labels']}/{totals['entries']} "
          f"({totals['into']} into translated chapters)  "
          + (f"exports with new epubcheck errors {totals['epubcheck_failed']}  " if epubcheck
             else 'epubcheck not run  ')
          + f"failed projects {failed}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
