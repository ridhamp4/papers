# Paperhost

Paperhost is a local paper editor plus a ready-to-publish GitHub Pages website. The editor runs only on your computer at `127.0.0.1`. It reads metadata from TeX ZIP files, compiles PDFs when `latexmk` is installed, and rebuilds `docs/` whenever you add or edit a paper. The public site has a home page and a separate shareable page for each paper.

## Start the local editor

On macOS, double-click `start.command`. Or from this folder run:

```bash
python3 paperhost.py serve
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) for the editor and [http://127.0.0.1:8765/site/](http://127.0.0.1:8765/site/) for the public site preview. Stop the server with `Control-C` in its terminal window.

Upload a TeX ZIP, a PDF, or both. The ZIP can contain `main.tex` or `paper.tex` at its top level or inside a project folder. You can leave the title, authors, institutes, and abstract empty when uploading TeX; Paperhost extracts them when the source provides them. The institutes field accepts one line per author, in the same order as the authors, and can be edited later. The paper listing shows author names; each paper's detail page pairs authors with their institutes. If PDF compilation fails, add a finished PDF through **Edit details → Replace files**. The editor accepts arXiv, OpenReview, and proceedings URLs, and shows only the links you provide. Existing papers live in `papers/tex/` and `papers/pdf/`; editable metadata lives in `catalog.json`.

Each public paper page generates APA, MLA, Chicago, BibTeX, and RIS citations from the saved metadata. Visitors can copy any format or download `.bib` and `.ris` files. Add a proceedings URL when one exists so the BibTeX entry uses `@inproceedings` with the venue in `booktitle`; other papers use `@misc` with the venue in `howpublished`. When a published site URL is set, papers without external publication links cite their own public page.

TeX compilation runs code from the archive. Upload only source files you trust. The editor is bound to the local computer, not a public server. If the initial PDF build fails, Paperhost retries with a few formatting repairs in its temporary build copy: loading the missing math font package, correcting nested inline math delimiters, removing identical duplicate bibliography entries, and escaping bibliography ampersands. The uploaded source ZIP stays unchanged.

## Put the public site on GitHub Pages

1. Sign in to GitHub and create an **empty public repository** for the papers. Do not add a README or license on GitHub; this folder already has a README.
2. In the local editor, open **GitHub repository**, paste the new repository URL, and click **Connect repository**. This initializes Git locally if needed and sets the repository as the publishing destination.
3. Click **Publish to GitHub**. Sign in to GitHub if Git asks you to. The button commits the current site and pushes it to the repository. Your GitHub account must have working Git credentials; `gh auth login` can refresh GitHub CLI sign-in if needed.
4. On GitHub, open **Settings → Pages**. Under **Build and deployment**, choose **Deploy from a branch**, branch **main**, folder **/docs**, then save. GitHub will show your public Pages URL after deployment.
5. Paste that URL into **Site details → Published GitHub Pages URL** in the local editor and save. This adds canonical sharing links to every paper page. You may also add your LinkedIn profile as the contact URL.

**Publish automatically after changes** is on by default. Once the Git repository is connected, adding, editing, or deleting a paper rebuilds `docs/`, commits the changes, and pushes them. GitHub Pages then updates from the new commit. Turn the option off if you want to review changes locally and use **Publish to GitHub** manually.

Each paper in the editor has a **Delete** button. Deleting removes its public page and download links. Its local PDF and TeX ZIP move to `.paperhost-trash/` so you can recover them. That folder is excluded from GitHub publishing.

To share one paper on LinkedIn, open its page on the published site, click **Copy page link**, and paste it into your post. GitHub Pages and all files in `docs/` are public. Public paper pages offer PDF downloads, citations, and publication links; TeX ZIPs stay in the private local `papers/tex/` folder and are excluded from Git publishing. The local editor is not published.

## Rebuild or import from the command line

```bash
python3 paperhost.py build
python3 paperhost.py import
```

`build` regenerates the public site from `catalog.json`. `import` adds new ZIP files found in `papers/tex/` that are not already in the catalog. A TeX distribution with a prebuilt PDF will use it only if compilation is unavailable or fails; a successful compilation uses the current TeX source.

Python 3.10 or newer is required. `latexmk` and a TeX installation are needed only for PDF compilation. The website and local editor require no third-party Python packages.
