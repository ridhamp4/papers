#!/usr/bin/env python3
"""Local paper editor and GitHub Pages generator. Python 3.10+; no packages."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from datetime import date
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEX = ROOT / "papers" / "tex"
PDF = ROOT / "papers" / "pdf"
DOCS = ROOT / "docs"
DATA = ROOT / "catalog.json"
MAX_UPLOAD = 100 * 1024 * 1024
MAX_UNPACKED = 250 * 1024 * 1024


def initial_data():
    return {"settings": {"name": "Ridham Patel", "subtitle": "Research papers", "bio": "Research in machine learning, numerical methods, and dynamical systems.", "contact": "", "site_url": "", "github_repo": "", "auto_publish": True}, "papers": []}


def read_data():
    if not DATA.exists():
        return initial_data()
    return json.loads(DATA.read_text(encoding="utf-8"))


def save_data(data):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ROOT, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        temp = f.name
    os.replace(temp, DATA)
    build(data)


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72].rstrip("-") or "paper"


def tex_group(source, command):
    matches = list(re.finditer(r"\\" + command + r"(?:\[[^]]*\])?\s*\{", source))
    for match in reversed(matches):
        if source[source.rfind("\n", 0, match.start()) + 1:match.start()].lstrip().startswith("%"):
            continue
        depth, start = 1, match.end()
        for index in range(start, len(source)):
            if source[index] == "{" and source[index - 1] != "\\":
                depth += 1
            elif source[index] == "}" and source[index - 1] != "\\":
                depth -= 1
                if depth == 0:
                    return source[start:index]
    return ""


def clean_tex(value):
    value = re.sub(r"(?m)^\s*%.*$", "", value)
    value = re.sub(r"\\(?:inst|keywords)\s*\{[^{}]*\}", "", value)
    value = re.sub(r"\\(?:cite|citep|citet|citealt|ref|eqref)\*?(?:\[[^]]*\])*\{[^{}]*\}", "", value)
    value = re.sub(r"\\(?:mathrm|mathbb|textsc|textbf|emph|textit|hat)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\(?:Name|Email|texttt)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", value)
    value = re.sub(r"\\(?:Rd|IKNO)\b", lambda m: {"Rd": "dynamic regret", "IKNO": "IKNO"}[m.group()[1:]], value)
    symbols = {"sum": "∑", "Omega": "Ω", "Theta": "Θ", "omega": "ω", "le": "≤", "ge": "≥", "in": "∈", "to": "→", "infty": "∞", "times": "×"}
    value = re.sub(r"\\(?:sum|Omega|Theta|omega|le|ge|infty|in|to|times)\b", lambda m: symbols[m.group()[1:]], value)
    value = value.replace(r"\|", "‖")
    value = value.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("--", "–")
    value = re.sub(r"\\(?:,|;|!|\s|\\)", " ", value)
    value = re.sub(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?", "", value)
    value = value.replace("{", "").replace("}", "").replace("$", "")
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+([,.!?;:])", r"\1", value)


def institute_lines(value):
    if isinstance(value, list):
        return [str(line).strip() for line in value]
    return [line.strip() for line in str(value or "").splitlines()]


def author_details(source):
    """Read author names and aligned institutes from common TeX title blocks."""
    source = re.sub(r"(?m)(?<!\\)%[^\n]*", "", source)
    author_source = tex_group(source, "author")
    blocks = [block.strip() for block in re.split(r"\\(?:And|and)\b", author_source) if block.strip()]
    institute_source = tex_group(source, "institute")
    institute_blocks = re.split(r"\\(?:And|and)\b", institute_source) if institute_source else []
    organization = re.compile(r"\b(?:University|Institute|College|School|Laboratory|Centre|Center|Academy|Hospital|IIT)\b", re.I)

    def find_institute(text):
        text = re.sub(r"\\(?:email|Email|texttt)\s*\{[^{}]*\}", "", text)
        text = text.replace(r"\addr", "\\\\")
        for line in re.split(r"\\\\|\n", text):
            cleaned = clean_tex(line)
            for segment in cleaned.split(","):
                if organization.search(segment):
                    return segment.strip()
        return ""

    names, institutes = [], []
    for block in blocks:
        first_line = block.split("\\\\", 1)[0].splitlines()[0]
        first_line = re.sub(r"\\(?:Email|email|texttt|thanks|orcidID)\s*\{[^{}]*\}", "", first_line)
        names.append(clean_tex(first_line))
        institute = find_institute(block)
        if not institute and institute_blocks:
            marker = re.search(r"\\inst\s*\{(\d+)\}", block)
            index = int(marker.group(1)) - 1 if marker else 0
            if 0 <= index < len(institute_blocks):
                institute = find_institute(institute_blocks[index])
        institutes.append(institute)
    return names, institutes


def infer_metadata(archive):
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) > 500 or sum(info.file_size for info in z.infolist()) > MAX_UNPACKED:
            raise ValueError("Archive is too large.")
        tex_files = [n for n in names if n.lower().endswith(".tex") and not n.startswith("__MACOSX/") and not any(part.startswith("._") for part in Path(n).parts)]
        preferred = [n for n in tex_files if Path(n).name.lower() in ("main.tex", "paper.tex")]
        main = min(preferred, key=lambda n: (0 if Path(n).name.lower() == "main.tex" else 1, len(Path(n).parts), n), default=None)
        if not main:
            main = min(tex_files, key=lambda n: (len(Path(n).parts), n), default=None)
        if not main:
            raise ValueError("The ZIP needs a main .tex file.")
        if z.getinfo(main).file_size > 5 * 1024 * 1024:
            raise ValueError("The main TeX file is too large.")
        source = z.read(main).decode("utf-8", "replace")
        title = clean_tex(tex_group(source, "title")) or archive.stem.replace("_", " ").title()
        authors, institutes = author_details(source)
        uncommented = re.sub(r"(?m)^\s*%.*$", "", source)
        abstract_match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", uncommented, re.S)
        if not abstract_match:
            abstract_match = re.search(r"\\abstract\b(.*?)\\endabstract\b", uncommented, re.S)
        abstract = clean_tex(abstract_match.group(1)) if abstract_match else ""
        return {"title": title, "authors": ", ".join(authors) or "Author", "institutes": institutes, "abstract": abstract, "main_tex": main}


def safe_extract(archive, destination):
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > 500 or sum(info.file_size for info in infos) > MAX_UNPACKED:
            raise ValueError("Archive is too large to compile safely.")
        for info in infos:
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts or info.filename.startswith("__MACOSX/"):
                if info.filename.startswith("__MACOSX/"):
                    continue
                raise ValueError("Archive contains an unsafe path.")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive contains a symbolic link.")
            target = destination / path
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def remove_identical_bib_entries(source):
    """Drop repeated, byte-for-byte identical entries in a build copy."""
    starts = re.finditer(r"(?m)^@[A-Za-z]+\s*\{\s*([^,\s]+)\s*,", source)
    seen, duplicates = {}, []
    for match in starts:
        opening = source.find("{", match.start(), match.end())
        depth, index = 0, opening
        while index < len(source):
            char = source[index]
            if char == "\\":
                index += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if depth != 0:
            continue
        end = index + 1
        entry = source[match.start():end].strip()
        key = match.group(1).lower()
        if key in seen and seen[key] == entry:
            duplicates.append((match.start(), end))
        else:
            seen[key] = entry
    for start, end in reversed(duplicates):
        source = source[:start] + source[end:]
    return source, len(duplicates)


def prepare_latex_build(folder, main_tex):
    """Repair common TeX markup errors only in the temporary build folder."""
    changes = []
    main = folder / main_tex
    source = main.read_text(encoding="utf-8", errors="replace")
    original = source
    packages = r"\\(?:usepackage|RequirePackage)(?:\[[^]]*\])?\{[^}]*\b(?:amssymb|amsfonts)\b"
    if r"\mathbb" in source and not re.search(packages, source) and r"\begin{document}" in source:
        source = source.replace(r"\begin{document}", "\\usepackage{amssymb}\n" + r"\begin{document}", 1)
        changes.append("loaded amssymb")
    source, math_fixes = re.subn(r"\$\\\(([^\n]*?)\\\)\$", r"$\1$", source)
    if math_fixes:
        changes.append("corrected nested math delimiters")
    if source != original:
        main.write_text(source, encoding="utf-8")

    for bib in folder.rglob("*.bib"):
        if bib.stat().st_size > 10 * 1024 * 1024:
            continue
        try:
            original = bib.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        normalized, duplicates = remove_identical_bib_entries(original)
        if duplicates:
            changes.append("removed repeated bibliography entries")
        lines = normalized.splitlines(keepends=True)
        normalized = "".join(line if line.lstrip().startswith("%") or re.match(r"\s*(?:url|doi|eprint)\s*=", line, re.I) else re.sub(r"(?<!\\)&", r"\\&", line) for line in lines)
        if normalized != original:
            bib.write_text(normalized, encoding="utf-8")
            if not duplicates and r"\&" in normalized:
                changes.append("escaped bibliography ampersands")
    return changes


def compile_pdf(archive, main_tex, output):
    with tempfile.TemporaryDirectory(prefix="paperhost-") as work:
        folder = Path(work)
        safe_extract(archive, folder)
        source_dir = folder / Path(main_tex).parent
        source_file = Path(main_tex).name
        embedded = source_dir / Path(source_file).with_suffix(".pdf")
        if not shutil.which("latexmk"):
            if embedded.exists() and embedded.stat().st_size > 1000:
                shutil.copy2(embedded, output)
                return "PDF copied from source archive; LaTeX compiler unavailable"
            return "LaTeX compiler unavailable; upload a PDF separately"
        try:
            run = subprocess.run(["latexmk", "-g", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", source_file], cwd=source_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=120)
            changes = prepare_latex_build(folder, main_tex) if run.returncode else []
            if changes:
                run = subprocess.run(["latexmk", "-g", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", source_file], cwd=source_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=120)
        except subprocess.TimeoutExpired:
            return "PDF compilation timed out; upload a PDF separately"
        built = source_dir / Path(source_file).with_suffix(".pdf")
        if run.returncode == 0 and built.exists():
            shutil.copy2(built, output)
            return "PDF compiled successfully" + (" using build-only TeX fixes; source ZIP unchanged" if changes else "")
        if embedded.exists() and embedded.stat().st_size > 1000:
            shutil.copy2(embedded, output)
            return "PDF copied from source archive; recompilation failed"
        latex_error = re.search(r"(?m)^(?:\./)?[^\n]*\.tex:\d+:\s*[^\n]+", run.stdout)
        detail = latex_error.group(0).removeprefix("./") if latex_error else run.stdout[-300:].strip()
        return "PDF compilation failed; upload a PDF separately. " + detail


def valid_url(value):
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError("Links must use a full http:// or https:// URL.")
    return value.strip()


def add_paper(data, fields, zip_file=None, pdf_file=None):
    if not zip_file and not pdf_file:
        raise ValueError("Choose a TeX ZIP or PDF.")
    guessed = {}
    if zip_file:
        if not zipfile.is_zipfile(zip_file):
            raise ValueError("The TeX source must be a valid ZIP archive.")
        guessed = infer_metadata(zip_file)
    fallback_name = Path(pdf_file).stem if pdf_file else Path(zip_file).stem
    title = fields.get("title", "").strip() or guessed.get("title", "") or fallback_name.replace("_", " ").title()
    if not title:
        raise ValueError("Enter a paper title.")
    base = slugify(title)
    taken = {p["slug"] for p in data["papers"]}
    slug, count = base, 2
    while slug in taken:
        slug, count = f"{base}-{count}", count + 1
    TEX.mkdir(parents=True, exist_ok=True)
    PDF.mkdir(parents=True, exist_ok=True)
    source_name = f"{slug}.zip" if zip_file else ""
    pdf_name = f"{slug}.pdf" if pdf_file or zip_file else ""
    institutes = institute_lines(fields.get("institutes", "")) if fields.get("institutes", "").strip() else guessed.get("institutes", [])
    paper = {"slug": slug, "title": title, "authors": fields.get("authors", "").strip() or guessed.get("authors", ""), "institutes": institutes, "abstract": fields.get("abstract", "").strip() or guessed.get("abstract", ""), "year": fields.get("year", "").strip() or str(date.today().year), "venue": fields.get("venue", "").strip(), "date": fields.get("date", "").strip() or date.today().isoformat(), "arxiv": valid_url(fields.get("arxiv", "")), "openreview": valid_url(fields.get("openreview", "")), "proceedings": valid_url(fields.get("proceedings", "")), "source": source_name, "pdf": ""}
    if zip_file:
        if Path(zip_file).resolve() != (TEX / source_name).resolve():
            shutil.copy2(zip_file, TEX / source_name)
    if pdf_file:
        if Path(pdf_file).read_bytes()[:5] != b"%PDF-":
            raise ValueError("The uploaded PDF is invalid.")
        shutil.copy2(pdf_file, PDF / pdf_name)
        paper["pdf"] = pdf_name
        message = "PDF uploaded"
    elif zip_file:
        message = compile_pdf(TEX / source_name, guessed["main_tex"], PDF / pdf_name)
        if (PDF / pdf_name).exists():
            paper["pdf"] = pdf_name
    data["papers"].insert(0, paper)
    save_data(data)
    return paper, message


def escape(value):
    return html.escape(str(value or ""), quote=True)


def paper_links(paper, prefix=""):
    entries = []
    for key, label in (("pdf", "Read PDF"), ("arxiv", "arXiv"), ("openreview", "OpenReview"), ("proceedings", "Proceedings")):
        value = paper.get(key, "")
        if not value:
            continue
        href = prefix + "papers/pdf/" + urllib.parse.quote(value) if key == "pdf" else value
        entries.append(f'<a href="{escape(href)}" {"target=\"_blank\" rel=\"noopener noreferrer\"" if key != "pdf" else ""}>{escape(label)} <span aria-hidden="true">↗</span></a>')
    return "".join(entries)


def citation_authors(value):
    """Split the editor's comma- or 'and'-separated author list."""
    return [name.strip() for name in re.split(r"\s*,\s*|\s+and\s+", value or "") if name.strip()]


def citation_name_parts(name):
    parts = name.split()
    return (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", name)


def citation_url(paper, site_url=""):
    for key in ("proceedings", "arxiv", "openreview"):
        if paper.get(key):
            return paper[key]
    if site_url:
        return site_url.rstrip("/") + "/papers/" + urllib.parse.quote(paper["slug"]) + "/"
    return ""


def bibtex_value(value):
    return "".join({"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "#": r"\#", "$": r"\$", "_": r"\_", "{": r"\{", "}": r"\}"}.get(char, char) for char in str(value))


def citations_for(paper, site_url=""):
    names = citation_authors(paper.get("authors", ""))
    title = paper["title"].strip()
    year = paper.get("year", "").strip() or "n.d."
    venue = paper.get("venue", "").strip()
    url = citation_url(paper, site_url)

    def inverted(name, initials=False):
        given, family = citation_name_parts(name)
        if initials:
            given = " ".join(part[0].upper() + "." for part in given.split() if part)
        return f"{family}, {given}" if given else family

    apa_authors = [inverted(name, True) for name in names]
    apa_authors = " & ".join(apa_authors) if len(apa_authors) <= 2 else ", ".join(apa_authors[:-1]) + ", & " + apa_authors[-1]
    apa_authors = apa_authors or "Unknown author"
    title_with_period = title if title.endswith((".", "!", "?")) else title + "."
    apa = f"{apa_authors}{'.' if not apa_authors.endswith('.') else ''} ({year}). {title_with_period}"
    if venue:
        apa += f" {venue}."
    if url:
        apa += f" {url}"

    mla_authors = (inverted(names[0]) + (" and " + names[1] if len(names) == 2 else ", et al." if len(names) > 2 else "")) if names else "Unknown author"
    mla = f"{mla_authors}{'.' if not mla_authors.endswith('.') else ''} \"{title_with_period}\""
    if venue:
        mla += f" {venue},"
    mla += f" {year}."
    if url:
        mla += f" {url}."

    chicago_authors = (inverted(names[0]) + (", et al." if len(names) > 3 else (", " + ", ".join(names[1:-1]) + ", and " + names[-1] if len(names) > 2 else " and " + names[-1] if len(names) == 2 else ""))) if names else "Unknown author"
    chicago = f"{chicago_authors}{'.' if not chicago_authors.endswith('.') else ''} \"{title_with_period}\""
    if venue:
        chicago += f" {venue}"
    chicago += f" ({year})."
    if url:
        chicago += f" {url}."

    family = citation_name_parts(names[0])[1] if names else "paper"
    key = slugify(family) + re.sub(r"\D", "", year) + "-" + paper["slug"]
    entry_type = "inproceedings" if paper.get("proceedings") else "misc"
    fields = [("title", "{" + bibtex_value(title) + "}"), ("author", bibtex_value(" and ".join(names) or "Unknown author")), ("year", bibtex_value(year))]
    if venue:
        fields.append(("booktitle" if entry_type == "inproceedings" else "howpublished", bibtex_value(venue)))
    if paper.get("arxiv"):
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", paper["arxiv"], re.I)
        if match:
            fields.extend((("eprint", bibtex_value(match.group(1).removesuffix(".pdf"))), ("archivePrefix", "arXiv")))
    if url:
        fields.append(("url", bibtex_value(url)))
    bibtex = "@" + entry_type + "{" + key + ",\n" + ",\n".join(f"  {field} = {{{value}}}" for field, value in fields) + "\n}"

    ris_type = "CPAPER" if paper.get("proceedings") else "GEN"
    ris_lines = [f"TY  - {ris_type}", *(f"AU  - {name}" for name in names), f"TI  - {title}", f"PY  - {year}"]
    if venue:
        ris_lines.append(f"T2  - {venue}")
    if url:
        ris_lines.append(f"UR  - {url}")
    if paper.get("abstract"):
        ris_lines.append("AB  - " + " ".join(paper["abstract"].split()))
    ris = "\n".join(ris_lines + ["ER  - ", ""])
    return {"APA": apa, "MLA": mla, "Chicago": chicago, "BibTeX": bibtex, "RIS": ris}


def citation_section(paper, site_url=""):
    citations = citations_for(paper, site_url)
    tabs = "".join(f'<button type="button" class="citation-tab{" active" if i == 0 else ""}" data-format="{label}" aria-pressed="{str(i == 0).lower()}">{label}</button>' for i, label in enumerate(citations))
    previews = "".join(f'<pre class="citation-text" data-format="{label}"{"" if i == 0 else " hidden"}>{escape(value)}</pre>' for i, (label, value) in enumerate(citations.items()))
    return f'''<section class="citations" aria-label="Cite this paper"><div class="citation-heading"><div><div class="eyebrow">Reference</div><h2>Cite this paper</h2></div><div class="citation-downloads"><a href="citation.bib" download>Download BibTeX ↓</a><a href="citation.ris" download>Download RIS ↓</a></div></div><div class="citation-tabs" role="group" aria-label="Citation format">{tabs}</div>{previews}<div class="citation-actions"><button type="button" class="copy-citation">Copy citation</button><button type="button" class="copy-link">Copy page link</button><span class="citation-status" role="status" aria-live="polite"></span></div></section>'''


def shell(title, description, body, prefix="", canonical=""):
    site_url = read_data()["settings"].get("site_url", "").rstrip("/")
    canonical_tag = f'<link rel="canonical" href="{escape(canonical)}">' if canonical else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><meta name="description" content="{escape(description[:200])}">
<meta property="og:type" content="article"><meta property="og:title" content="{escape(title)}"><meta property="og:description" content="{escape(description[:200])}">{canonical_tag}
<link rel="stylesheet" href="{prefix}assets/site.css"><link rel="icon" href="{prefix}assets/favicon.svg" type="image/svg+xml"></head><body>{body}</body></html>'''


def build(data=None):
    data = data or read_data()
    DOCS.mkdir(exist_ok=True)
    (DOCS / "assets").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "assets" / "site.css", DOCS / "assets" / "site.css")
    shutil.copy2(ROOT / "assets" / "citations.js", DOCS / "assets" / "citations.js")
    shutil.copy2(ROOT / "assets" / "favicon.svg", DOCS / "assets" / "favicon.svg")
    shutil.copy2(ROOT / "assets" / "ridham.jpg", DOCS / "assets" / "ridham.jpg")
    (DOCS / ".nojekyll").touch()
    public_tex = DOCS / "papers" / "tex"
    if public_tex.is_dir():
        shutil.rmtree(public_tex)
    for folder, key, origin in (("pdf", "pdf", PDF),):
        target = DOCS / "papers" / folder
        target.mkdir(parents=True, exist_ok=True)
        expected = {p[key] for p in data["papers"] if p.get(key)}
        for old in target.iterdir():
            if old.is_file() and old.name not in expected:
                old.unlink()
        for filename in expected:
            if (origin / filename).exists():
                shutil.copy2(origin / filename, target / filename)
    papers = sorted(data["papers"], key=lambda p: (p.get("date", ""), p.get("title", "")), reverse=True)
    name = data["settings"].get("name", "Research")
    site_url = data["settings"].get("site_url", "").rstrip("/")
    cards = []
    for p in papers:
        page = f"papers/{p['slug']}/"
        title = escape(p["title"])
        abstract = p.get("abstract", "")
        desc = escape(abstract)
        excerpt = escape(abstract[:270]) + ("…" if len(abstract) > 270 else "")
        author_names = citation_authors(p.get("authors", ""))
        institutes = institute_lines(p.get("institutes", []))
        unique_institutes = list(dict.fromkeys(institute for institute in institutes if institute))
        if unique_institutes:
            people = "".join(f'<div class="detail-person"><span class="detail-person-name">{escape(author)}</span><span class="detail-institute">{escape(institutes[i])}</span></div>' if i < len(institutes) and institutes[i] else f'<div class="detail-person"><span class="detail-person-name">{escape(author)}</span></div>' for i, author in enumerate(author_names))
            byline = f'<div class="detail-people" aria-label="Authors and institutes">{people}</div>'
        else:
            byline = f'<p class="detail-authors">{escape(p.get("authors", ""))}</p>'
        search_text = (p["title"] + " " + p.get("authors", "") + " " + p.get("abstract", "") + " " + p.get("venue", "")).lower()
        cards.append(f'''<article class="paper-card" data-search="{escape(search_text)}" data-year="{escape(p.get('year',''))}"><div class="paper-meta"><span>{escape(p.get('year',''))}</span><span class="dot"></span><span>{escape(p.get('venue') or 'Research paper')}</span></div><h2><a href="{escape(page)}">{title}</a></h2><p class="authors">{escape(p.get('authors',''))}</p><p class="excerpt">{excerpt}</p><div class="links"><a class="detail-link" href="{escape(page)}">Details <span aria-hidden="true">↗</span></a>{paper_links(p)}</div></article>''')
        detail_body = f'''<div class="site-wrap"><header class="topbar"><a class="brand" href="../../">{escape(name)}<span class="brand-mark"> / papers</span></a><a class="top-link" href="../../">All papers ↗</a></header><main class="detail"><a class="back" href="../../">← All papers</a><div class="eyebrow">{escape(p.get('year',''))} <span class="dot"></span> {escape(p.get('venue') or 'Research paper')}</div><h1>{title}</h1>{byline}<div class="detail-links">{paper_links(p, '../../')}</div><section class="abstract"><h2>Abstract</h2><p>{desc or 'Abstract forthcoming.'}</p></section>{citation_section(p, site_url)}</main><footer>© {escape(date.today().year)} {escape(name)} · Independently hosted research</footer></div><script src="../../assets/citations.js" defer></script>'''
        canonical = site_url + "/" + page if site_url else ""
        target = DOCS / "papers" / p["slug"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(shell(p["title"] + " | " + name, p.get("abstract", ""), detail_body, "../../", canonical), encoding="utf-8")
        citation_formats = citations_for(p, site_url)
        (target / "citation.bib").write_text(citation_formats["BibTeX"] + "\n", encoding="utf-8")
        (target / "citation.ris").write_text(citation_formats["RIS"], encoding="utf-8")
    paper_dirs = DOCS / "papers"
    for child in paper_dirs.iterdir():
        if child.is_dir() and child.name != "pdf" and child.name not in {p["slug"] for p in papers}:
            shutil.rmtree(child)
    years = sorted({p.get("year", "") for p in papers if p.get("year")}, reverse=True)
    options = ''.join(f'<option value="{escape(year)}">{escape(year)}</option>' for year in years)
    site_url = data["settings"].get("site_url", "").rstrip("/")
    contact = data["settings"].get("contact", "")
    social_urls = (
        ("GitHub", "https://github.com/ridhamp4"),
        ("LinkedIn", contact or "https://www.linkedin.com/in/ridhamp4/"),
        ("X", "https://x.com/ridhamp4"),
        ("Google Scholar", "https://scholar.google.com/citations?user=wDDnh9QAAAAJ&hl=en&oi=ao"),
    )
    socials = "".join(f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(label)} <span aria-hidden="true">↗</span></a>' for label, url in social_urls)
    profile = f'''<section class="profile-hero" aria-label="About {escape(name)}"><div class="profile-photo-frame"><img class="profile-photo" src="assets/ridham.jpg" alt="Portrait of {escape(name)}" width="196" height="196" decoding="async"></div><div class="profile-content"><div class="profile-kicker">About me</div><h1>{escape(name)}</h1><p>{escape(data['settings'].get('bio',''))}</p><div class="social-links" aria-label="Social profiles">{socials}</div></div></section>'''
    body = f'''<div class="site-wrap"><header class="topbar"><a class="brand" href="./">{escape(name)}<span class="brand-mark"> / papers</span></a><nav><a href="#papers">Research</a></nav></header><main>{profile}<section class="listing" id="papers"><div class="section-heading"><div><div class="eyebrow">Selected work</div><h2>Publications</h2></div><span class="count">{len(papers)} papers</span></div><div class="toolbar"><label class="search"><span aria-hidden="true">⌕</span><input id="search" type="search" placeholder="Search papers, topics, authors" aria-label="Search papers"></label><select id="year" aria-label="Filter by year"><option value="">All years</option>{options}</select></div><div id="paper-list">{''.join(cards) if cards else '<p class="empty">No papers yet. Add one through the local editor.</p>'}</div><p class="empty" id="no-results" hidden>No papers match your search.</p></section></main><footer><span>© {escape(date.today().year)} {escape(name)}</span><span>Research, independently hosted.</span></footer></div><script>const q=document.getElementById('search'),y=document.getElementById('year'),items=[...document.querySelectorAll('.paper-card')],empty=document.getElementById('no-results');function filter(){{let shown=0;for(const card of items){{const ok=card.dataset.search.includes(q.value.toLowerCase().trim())&&(!y.value||card.dataset.year===y.value);card.hidden=!ok;if(ok)shown++}}empty.hidden=shown>0||items.length===0}}q.addEventListener('input',filter);y.addEventListener('change',filter);</script>'''
    (DOCS / "index.html").write_text(shell(name + " | Research papers", data["settings"].get("subtitle", "Research papers"), body, canonical=site_url + "/" if site_url else ""), encoding="utf-8")
    (DOCS / "404.html").write_text(shell("Page not found | " + name, "Page not found", f'<div class="site-wrap"><header class="topbar"><a class="brand" href="/">{escape(name)}</a></header><main class="detail"><h1>Page not found</h1><p><a href="/">Return to papers →</a></p></main></div>'), encoding="utf-8")


def publish_to_github():
    if not (ROOT / ".git").exists():
        raise ValueError("Initialize a Git repository and connect a GitHub remote first. See README.md.")
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True)
    if remote.returncode:
        raise ValueError("Add a GitHub origin remote first. See README.md.")
    subprocess.run(["git", "rm", "-r", "--cached", "--ignore-unmatch", "--", "papers/tex", "docs/papers/tex"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", "catalog.json", "docs", "papers/pdf", "paperhost.py", "admin.html", "admin.js", "assets", "README.md", "start.command", ".gitignore"], cwd=ROOT, check=True)
    commit = subprocess.run(["git", "commit", "-m", "Update paper collection"], cwd=ROOT, capture_output=True, text=True)
    if commit.returncode and "nothing to commit" not in commit.stdout + commit.stderr:
        raise ValueError(commit.stderr.strip() or "Git commit failed")
    push = subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    if push.returncode:
        raise ValueError(push.stderr.strip() or "Git push failed")
    return "Changes pushed to GitHub. Pages will update shortly."


def connect_github_repository(data, url):
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a GitHub repository URL like https://github.com/username/repository.")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", parsed.path)
    if not match:
        raise ValueError("Enter a GitHub repository URL like https://github.com/username/repository.")
    owner, repo = match.groups()
    public_url = f"https://github.com/{owner}/{repo}"
    remote_url = public_url + ".git"
    if not (ROOT / ".git").exists():
        init = subprocess.run(["git", "init", "-b", "main"], cwd=ROOT, capture_output=True, text=True)
        if init.returncode:
            raise ValueError(init.stderr.strip() or "Could not initialize Git in this folder.")
    current = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True)
    action = ["git", "remote", "set-url" if current.returncode == 0 else "add", "origin", remote_url]
    changed = subprocess.run(action, cwd=ROOT, capture_output=True, text=True)
    if changed.returncode:
        raise ValueError(changed.stderr.strip() or "Could not connect the GitHub repository.")
    data["settings"]["github_repo"] = public_url
    save_data(data)
    return public_url


def remove_paper(data, slug):
    paper = next((p for p in data["papers"] if p["slug"] == slug), None)
    if not paper:
        raise ValueError("Paper not found.")
    data["papers"].remove(paper)
    save_data(data)
    trash = ROOT / ".paperhost-trash" / f"{slug}-{time.time_ns()}"
    for key, folder in (("source", TEX), ("pdf", PDF)):
        filename = paper.get(key, "")
        if not filename or Path(filename).name != filename:
            continue
        if any(other.get(key) == filename for other in data["papers"]):
            continue
        old = folder / filename
        if old.exists():
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(trash / filename))
    return paper


def auto_publish(data):
    if not data["settings"].get("auto_publish", True):
        return "Saved locally. Use Publish to GitHub when ready."
    if not (ROOT / ".git").exists():
        return "Saved locally. Connect a GitHub repository to enable automatic publishing."
    try:
        return publish_to_github()
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return "Saved locally; GitHub publish failed: " + str(exc)


def parse_multipart(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > MAX_UPLOAD:
        raise ValueError("Upload must be under 100 MB.")
    raw = handler.rfile.read(length)
    header = f"Content-Type: {handler.headers.get('Content-Type', '')}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    msg = BytesParser(policy=default).parsebytes(header + raw)
    if not msg.is_multipart():
        raise ValueError("Expected a multipart form upload.")
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if filename:
            suffix = Path(filename).suffix.lower()
            if name not in ("tex", "pdf") or suffix != (".zip" if name == "tex" else ".pdf"):
                raise ValueError("Choose a .zip for TeX and a .pdf for the paper.")
            files[name] = part.get_payload(decode=True)
        elif name:
            fields[name] = part.get_content()
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
            self.send_error(403)
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            self.reply(200, read_data())
            return
        if path == "/" or path == "/admin" or path == "/admin/":
            file = ROOT / "admin.html"
        elif path == "/admin.js":
            file = ROOT / "admin.js"
        elif path.startswith("/site/"):
            relative = urllib.parse.unquote(path[6:])
            file = (DOCS / relative).resolve()
            if not file.is_relative_to(DOCS.resolve()):
                self.send_error(403)
                return
            if file.is_dir():
                file = file / "index.html"
        else:
            self.send_error(404)
            return
        if not file.is_file():
            self.send_error(404)
            return
        if file.suffix.lower() in (".zip", ".tex"):
            self.send_error(404)
            return
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".pdf": "application/pdf"}.get(file.suffix, "application/octet-stream")
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
            self.reply(403, {"error": "Local editor only."})
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self.reply(403, {"error": "Request origin does not match the local editor."})
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            data = read_data()
            if path == "/api/papers":
                fields, files = parse_multipart(self)
                with tempfile.TemporaryDirectory(prefix="paperhost-upload-") as work:
                    tex_path = pdf_path = None
                    if "tex" in files:
                        tex_path = Path(work) / "source.zip"
                        tex_path.write_bytes(files["tex"])
                    if "pdf" in files:
                        pdf_path = Path(work) / "paper.pdf"
                        pdf_path.write_bytes(files["pdf"])
                    paper, message = add_paper(data, fields, tex_path, pdf_path)
                self.reply(200, {"paper": paper, "message": message + ". " + auto_publish(data)})
            elif path.startswith("/api/papers/") and path.endswith("/files"):
                slug = path.removeprefix("/api/papers/").removesuffix("/files")
                paper = next((p for p in data["papers"] if p["slug"] == slug), None)
                if not paper:
                    self.reply(404, {"error": "Paper not found"})
                    return
                _fields, files = parse_multipart(self)
                if not files:
                    raise ValueError("Choose a TeX ZIP or PDF.")
                message = "Files updated"
                if "tex" in files:
                    with tempfile.TemporaryDirectory(prefix="paperhost-update-") as work:
                        uploaded = Path(work) / "source.zip"
                        uploaded.write_bytes(files["tex"])
                        info = infer_metadata(uploaded)
                        source_name = f"{slug}.zip"
                        shutil.copy2(uploaded, TEX / source_name)
                        paper["source"] = source_name
                        if info["institutes"]:
                            paper["institutes"] = info["institutes"]
                        if "pdf" not in files:
                            pdf_name = f"{slug}.pdf"
                            message = compile_pdf(TEX / source_name, info["main_tex"], PDF / pdf_name)
                            if (PDF / pdf_name).exists():
                                paper["pdf"] = pdf_name
                if "pdf" in files:
                    if files["pdf"][:5] != b"%PDF-":
                        raise ValueError("The uploaded PDF is invalid.")
                    pdf_name = f"{slug}.pdf"
                    (PDF / pdf_name).write_bytes(files["pdf"])
                    paper["pdf"] = pdf_name
                    message = "PDF uploaded"
                save_data(data)
                self.reply(200, {"message": message + ". " + auto_publish(data)})
            elif path == "/api/settings" or path.startswith("/api/papers/"):
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 100000:
                    raise ValueError("Invalid request size.")
                fields = json.loads(self.rfile.read(length))
                if path == "/api/settings":
                    for key in ("name", "subtitle", "bio"):
                        data["settings"][key] = str(fields.get(key, "")).strip()[:1000]
                    for key in ("contact", "site_url"):
                        data["settings"][key] = valid_url(fields.get(key, ""))
                    data["settings"]["auto_publish"] = bool(fields.get("auto_publish"))
                else:
                    slug = path.removeprefix("/api/papers/")
                    paper = next((p for p in data["papers"] if p["slug"] == slug), None)
                    if not paper:
                        self.reply(404, {"error": "Paper not found"})
                        return
                    for key in ("title", "authors", "abstract", "year", "venue", "date"):
                        paper[key] = str(fields.get(key, "")).strip()[:20000]
                    paper["institutes"] = [line[:500] for line in institute_lines(fields.get("institutes", ""))[:50]]
                    for key in ("arxiv", "openreview", "proceedings"):
                        paper[key] = valid_url(fields.get(key, ""))
                    if not paper["title"]:
                        raise ValueError("A title is required.")
                save_data(data)
                self.reply(200, {"ok": True, "message": auto_publish(data)})
            elif path == "/api/github":
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 2000:
                    raise ValueError("Invalid repository URL.")
                fields = json.loads(self.rfile.read(length))
                repo = connect_github_repository(data, fields.get("url", ""))
                self.reply(200, {"message": f"Connected {repo}. Click Publish to GitHub to send the current site."})
            elif path == "/api/publish":
                self.reply(200, {"message": publish_to_github()})
            else:
                self.reply(404, {"error": "Unknown action"})
        except (ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            self.reply(400, {"error": str(exc)})
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            self.reply(500, {"error": str(exc)})

    def do_DELETE(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
            self.reply(403, {"error": "Local editor only."})
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self.reply(403, {"error": "Request origin does not match the local editor."})
            return
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/papers/") or "/" in path.removeprefix("/api/papers/"):
            self.reply(404, {"error": "Unknown action"})
            return
        try:
            data = read_data()
            paper = remove_paper(data, path.removeprefix("/api/papers/"))
            self.reply(200, {"message": f"Removed {paper['title']} from the site. Local files moved to .paperhost-trash. " + auto_publish(data)})
        except ValueError as exc:
            self.reply(404, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Local editor and GitHub Pages builder for research papers")
    parser.add_argument("command", choices=("import", "build", "serve"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "import":
        data = read_data()
        existing = {p.get("source") for p in data["papers"]}
        for archive in sorted(TEX.glob("*.zip")):
            if archive.name in existing or any(p.get("source") and p["source"].startswith(slugify(infer_metadata(archive)["title"])) for p in data["papers"]):
                continue
            paper, message = add_paper(data, {}, archive)
            print(f"{paper['title']}: {message}")
        build(data)
    elif args.command == "build":
        build()
        print("GitHub Pages site built in docs/")
    else:
        build()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"Local editor: http://127.0.0.1:{args.port}/", flush=True)
        print(f"Public preview: http://127.0.0.1:{args.port}/site/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
