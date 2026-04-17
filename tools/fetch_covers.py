#!/usr/bin/env python3
"""
fetch_covers.py — interactive TMDB poster fetcher for EasyPlay.

For each media folder under the target directory, parses title+year from
the folder name, queries The Movie Database (TMDB), shows the top matches,
lets you pick one, and downloads the poster to <folder>/cover.jpg.

Existing cover.jpg is renamed to cover.jpg.bak on first fetch so originals
are always recoverable.

Setup
-----
1. Create a free TMDB account at https://www.themoviedb.org/signup
2. Go to Settings -> API and request an API key (instant approval).
3. Grab the "API Read Access Token" (a long v4 bearer token).
4. Either:
     export TMDB_TOKEN='eyJhbGciOiJIUzI1NiJ9.xxxxxx...'
   or put it in ~/.config/easyplay/tmdb_token on a single line.
5. pip install requests pillow

Usage
-----
    # With a path:
    python3 tools/fetch_covers.py /Volumes/BIGF/codevideos
    python3 tools/fetch_covers.py /Volumes/BIGF/codevideos --only "Jojo*"
    python3 tools/fetch_covers.py /Volumes/BIGF/codevideos --skip-existing

    # Without a path — a native folder picker pops up:
    python3 tools/fetch_covers.py

Keys in the picker:
    1-9   pick that match
    s     skip this folder
    q     quit
    o     open the TMDB page for the top match in browser (verify)
    r     retry with a different query string (you'll be prompted)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

# requests is imported lazily so the parser functions can be used/tested
# without the dependency installed. See TMDBClient.__init__.
requests = None  # type: ignore

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w780"   # Good balance of quality and size
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".ts", ".wmv"}
IMG_EXTS = {".jpg", ".jpeg", ".png"}

# Folders that aren't media and should never be processed.
EXCLUDE_FOLDERS = {
    "System Volume Information", "$RECYCLE.BIN", "RECYCLER",
    "lost+found", ".Trashes", ".fseventsd", ".Spotlight-V100",
    "Subs", "Extras", "Sample", "Samples",
}


# ── Folder-name parsing ──────────────────────────────────────────────────────

# Prefer years in brackets/parens like (2019) or [2019] over bare years like .2019.
YEAR_BRACKET_RE = re.compile(r"[\(\[](19\d{2}|20\d{2})[\)\]]")
YEAR_BARE_RE = re.compile(r"[\. ](19\d{2}|20\d{2})[\. ]")
SEASON_RE = re.compile(r"\bS(?:eason)?\.?\s*(\d{1,2})(?:E\d{1,3})?\b", re.I)
EPISODE_RE = re.compile(r"\bS\d{1,2}E\d{1,3}\b", re.I)

NOISE_PATTERNS = [
    r"\[[^\]]*\]",              # [YTS.MX], [BluRay], etc.
    r"\([^)]*\)",               # (2019), (BluRay)
    r"\b\d{3,4}p\b",            # 720p, 1080p, 2160p
    r"\b\d+[\._ ]?bit\b",       # 10bit, 10.bit, 10 bit
    r"\b\d+(?:\.\d+)?\s?MB\b",  # 900MB, 1.5GB
    r"\b\d+(?:\.\d+)?\s?GB\b",
    r"\bWEB[\- \.]?RIP\b", r"\bWEB[\- \.]?DL\b", r"\bBRRip\b",
    r"\bBluRay\b", r"\bBlu[\- ]Ray\b",
    r"\bHDRip\b", r"\bHDTV\b",
    r"\bHEVC\b", r"\bx[\s\.]?26[45]\b", r"\bh[\s\.]?26[45]\b",
    # Audio codecs — consume any sticky channel digits so that e.g.
    # OPUS51 / AC35 1 / DTS-HD MA 5 1 don't leave orphan numbers behind.
    r"\bAAC\d*(?:\.\d)?(?:[\s\.]?\d)*\b",
    r"\bAC3(?:[\s\.]?\d)*\b",
    r"\bDTS(?:[\s\.\-]?HD)?(?:[\s\.]?MA)?(?:[\s\.]?\d)*\b",
    r"\bMA(?:[\s\.]?\d)*\b",  # stray "MA" from DTS-HD MA variants
    r"\bTrueHD(?:[\s\.]?\d)*\b",
    r"\bFLAC(?:[\s\.]?\d)*\b",
    r"\bEAC3(?:[\s\.]?\d)*\b", r"\bE-AC-3(?:[\s\.]?\d)*\b",
    r"\bOPUS(?:[\s\.]?\d)*\b",
    r"\bAtmos\b", r"\bMP3\b", r"\bOGG\b",
    # Audio channel specs as whole chunks (longest first so alternation
    # doesn't grab a shorter prefix and leave an orphan digit behind)
    r"\bDDP?[\s\.]?\d[\s\.]\d\b",   # DD 5 1 / DDP5.1 / DD.5.1
    r"\bDDP?[\s\.]?\d\b",           # DDP5 / DD.5 / DDP 5
    r"\bDDP?\b",                    # DDP / DD standalone
    r"\b5[\.\s]?1\b",               # plain 5.1 / 5 1
    r"\b7[\.\s]?1\b",
    r"\b2[\.\s]?0\b",
    r"\bYTS\.?\w*\b", r"\bYIFY\b", r"\bRARBG\b", r"\bGalaxyRG\b",
    r"\bEZTVx?\b", r"\bNeoNoir\b", r"\bFENiX\b", r"\bMeGusta\b",
    r"\bi_c\b", r"\bTGx\b", r"\bBONE\b",
    r"\bREMASTERED\b", r"\bEXTENDED\b", r"\bUNRATED\b",
    r"\bDIRECTORS?[\s\.]?CUT\b",
    r"\bComplete\b",
    r"\bNF\b", r"\bAMZN\b", r"\bSUCCESSFULCRAB\b", r"\bExKinoRay\b",
    r"\bFeranki\d*\b", r"\bscarabey\b",
    r"\bDVDScr\b", r"\bDVDRip\b", r"\bREPACK\b",
    r"\bHDCAM\b", r"\bDLRip\b", r"\baWEBRip\b",
    r"\bPROPER\b", r"\bCOMPLETE\b", r"\bCriterion\b",
    r"\bESub\b", r"\bEng\b", r"\bFHC\b", r"\biTA\b",
    r"\bION265\b", r"\beztv(?:\.re)?\b", r"\bGalaxyTV\b", r"\bUKB\b",
    r"\bHMAX\b",
    r"\bWEB\b",     # standalone WEB separator (e.g. "S02E04.WEB.H264")
    r"\bH264\b", r"\bH265\b",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)

# Applied as a SECOND pass after NOISE_RE, so the \B lookbehind sees the
# already-blanked string (e.g. "Spider-Man -NTb" with a space before the
# trailing dash), not the original. \B keeps hyphenated titles like
# "Spider-Man" safe because a dash preceded by a word char fails the
# non-word-boundary test.
TRAILING_GROUP_RE = re.compile(r"\B-[A-Za-z][A-Za-z0-9]{2,}[\s\-]*$")


@dataclass
class ParsedTitle:
    title: str
    year: int | None
    season: int | None   # None = movie, int = TV season N
    raw: str

    @property
    def is_tv(self) -> bool:
        return self.season is not None


def parse_folder_name(name: str) -> ParsedTitle:
    raw = name
    # Prefer bracketed year (2019) over bare year .2019. to avoid grabbing
    # numeric titles like "1917" as the year.
    year_match = YEAR_BRACKET_RE.search(name) or YEAR_BARE_RE.search(" " + name + " ")
    year = int(year_match.group(1)) if year_match else None

    season_match = SEASON_RE.search(name)
    season = int(season_match.group(1)) if season_match else None

    # Normalize separators first so \b-anchored noise patterns match
    # consistently regardless of whether the name uses dots or spaces.
    cleaned = re.sub(r"[._]+", " ", name)
    # Strip everything noisy
    cleaned = NOISE_RE.sub(" ", cleaned)
    # Remove season/episode tokens
    cleaned = EPISODE_RE.sub(" ", cleaned)
    cleaned = SEASON_RE.sub(" ", cleaned)
    # Strip the 4-digit year (anywhere, not just in brackets)
    if year:
        cleaned = re.sub(rf"\b{year}\b", " ", cleaned)
    # Second pass: strip any leftover trailing scene-release group like
    # "-EMPATHY" / "-NTb" now that the noise-substituted string has
    # whitespace before the dash, so the \B anchor can see non-word context.
    cleaned = TRAILING_GROUP_RE.sub(" ", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")

    return ParsedTitle(title=cleaned, year=year, season=season, raw=raw)


def _is_real_file(p: Path) -> bool:
    """Filter out macOS AppleDouble shadows, .DS_Store, and other dotfiles."""
    return not (p.name.startswith(".") or p.name.startswith("._"))


def folder_is_tv(subdir: Path) -> bool:
    """Detect TV series folder heuristically: multiple video files OR S01/Season token in name."""
    if SEASON_RE.search(subdir.name):
        return True
    videos = [f for f in subdir.iterdir()
              if f.is_file() and _is_real_file(f) and f.suffix.lower() in VIDEO_EXTS]
    return len(videos) > 1


# ── TMDB API ─────────────────────────────────────────────────────────────────

class TMDBClient:
    def __init__(self, token: str):
        global requests
        if requests is None:
            try:
                import requests as _requests
                requests = _requests
            except ImportError:
                sys.exit("Missing dependency: pip install requests")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self.session.headers["Accept"] = "application/json"

    def search(self, query: str, year: int | None, kind: str) -> list[dict]:
        """kind: 'movie' or 'tv'. Returns raw result list."""
        endpoint = f"{TMDB_BASE}/search/{kind}"
        params = {"query": query, "include_adult": "false"}
        if year and kind == "movie":
            params["year"] = year
        if year and kind == "tv":
            params["first_air_date_year"] = year
        r = self.session.get(endpoint, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("results", [])

    def download_poster(self, poster_path: str, dest: Path) -> None:
        url = f"{TMDB_IMG_BASE}/{POSTER_SIZE}{poster_path}"
        with self.session.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)


# ── UI ───────────────────────────────────────────────────────────────────────

def fmt_match(i: int, m: dict, kind: str) -> str:
    if kind == "movie":
        title = m.get("title") or m.get("original_title") or "?"
        date = m.get("release_date") or ""
    else:
        title = m.get("name") or m.get("original_name") or "?"
        date = m.get("first_air_date") or ""
    year = (date or "????")[:4]
    overview = (m.get("overview") or "").strip().replace("\n", " ")
    if len(overview) > 110:
        overview = overview[:107] + "…"
    poster = "✓" if m.get("poster_path") else "✗"
    return f"  {i}. [{year}] {title}  (poster {poster})\n     {overview}"


def tmdb_page_url(m: dict, kind: str) -> str:
    return f"https://www.themoviedb.org/{kind}/{m['id']}"


def pick_match(parsed: ParsedTitle, folder: Path, client: TMDBClient,
               auto: bool = False) -> dict | None:
    """Prompt loop for a single folder. Returns chosen TMDB result or None to skip.

    If auto=True, never prompts: auto-picks the first result with a poster,
    skips the folder otherwise.
    """
    kind = "tv" if folder_is_tv(folder) else "movie"
    query = parsed.title
    year = parsed.year

    while True:
        print(f"\n┌─ {folder.name}")
        print(f"│  parsed: title='{query}' year={year} kind={kind}")
        try:
            results = client.search(query, year, kind)
        except requests.HTTPError as e:
            print(f"│  TMDB error: {e}")
            return None

        if not results:
            other = "tv" if kind == "movie" else "movie"
            print(f"│  No matches as {kind}. Trying {other}…")
            try:
                alt = client.search(query, year, other)
            except requests.HTTPError:
                alt = []
            if alt:
                kind = other
                results = alt

        if results:
            print(f"│  {min(len(results), 9)} matches as {kind}:")
            for i, m in enumerate(results[:9], start=1):
                print(fmt_match(i, m, kind))
        else:
            print("│  No matches either way.")

        # Auto mode: pick the first result with a poster, otherwise skip.
        if auto:
            for m in results:
                if m.get("poster_path"):
                    print("│  (auto) picking match 1")
                    return m
            print("│  (auto) no usable match, skipping")
            return None

        print("└─ [1-9] pick | s skip | r retry different query | t toggle movie/tv | o open top in browser | q quit")
        choice = input("   > ").strip().lower()

        if not choice:
            continue
        if choice == "q":
            print("Quitting.")
            sys.exit(0)
        if choice == "s":
            return None
        if choice == "t":
            kind = "tv" if kind == "movie" else "movie"
            continue
        if choice == "o" and results:
            webbrowser.open(tmdb_page_url(results[0], kind))
            continue
        if choice == "r":
            new_q = input("   new query> ").strip()
            if new_q:
                query = new_q
            new_y = input(f"   year (blank to keep {year})> ").strip()
            if new_y:
                year = int(new_y) if new_y.isdigit() else None
            continue
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(results[:9]):
                chosen = results[n - 1]
                if not chosen.get("poster_path"):
                    print("   That match has no poster on TMDB. Pick another or retry.")
                    continue
                return chosen
        print("   ?")


def save_cover(folder: Path, client: TMDBClient, match: dict) -> Path:
    cover = folder / "cover.jpg"
    if cover.exists():
        bak = folder / "cover.jpg.bak"
        if not bak.exists():
            shutil.move(str(cover), str(bak))
            print(f"   backed up existing cover -> {bak.name}")
        else:
            cover.unlink()
    client.download_poster(match["poster_path"], cover)
    return cover


# ── Token loading ────────────────────────────────────────────────────────────

def load_token() -> str:
    tok = os.environ.get("TMDB_TOKEN")
    if tok:
        return tok.strip()
    cfg = Path.home() / ".config" / "easyplay" / "tmdb_token"
    if cfg.exists():
        return cfg.read_text().strip()
    sys.exit(
        "No TMDB token found.\n"
        "  Get one at https://www.themoviedb.org/settings/api (Read Access Token),\n"
        "  then:  export TMDB_TOKEN='eyJ...'\n"
        "  or:    mkdir -p ~/.config/easyplay && "
        "echo 'eyJ...' > ~/.config/easyplay/tmdb_token"
    )


# ── Organize loose videos ─────────────────────────────────────────────────────

def organize_loose_videos(library: Path) -> int:
    """Move loose video files at the library root into their own folders.

    For each video file sitting directly in the library (not in a subfolder),
    creates a folder named after the video (using the clean title from
    parse_folder_name, preserving the year if present), moves the video into
    it, and also moves any sidecar files with the same stem (.srt, .nfo, .txt,
    .jpg, .png).

    Returns the number of videos organized.
    """
    SIDECAR_EXTS = {".srt", ".sub", ".idx", ".nfo", ".txt", ".jpg", ".jpeg", ".png"}

    loose = [f for f in library.iterdir()
             if f.is_file()
             and _is_real_file(f)
             and f.suffix.lower() in VIDEO_EXTS]
    if not loose:
        return 0

    count = 0
    for video in sorted(loose):
        # Build a clean folder name: use the video stem as-is (preserving the
        # original scene-release name), which mirrors how the rest of the
        # library is already structured. EasyPlay's clean_media_name will
        # handle display cleaning.
        folder_name = video.stem
        dest_dir = library / folder_name

        # Avoid collisions: if a folder already exists with that name, the
        # video probably belongs there — move it in without creating a new one.
        dest_dir.mkdir(exist_ok=True)

        dest_file = dest_dir / video.name
        if dest_file.exists():
            print(f"  ⚠ {video.name} already in {folder_name}/, skipping")
            continue

        # Move video
        shutil.move(str(video), str(dest_file))
        print(f"  📁 {video.name} → {folder_name}/")

        # Move any sidecars with matching stem or name prefix.
        # Matches both "movie.srt" (same stem) and "movie.mkv_cover.jpg"
        # (name starts with the video stem).
        for sidecar in list(library.iterdir()):
            if (sidecar.is_file()
                    and _is_real_file(sidecar)
                    and sidecar.name.startswith(video.stem)
                    and sidecar.suffix.lower() in SIDECAR_EXTS):
                shutil.move(str(sidecar), str(dest_dir / sidecar.name))
                print(f"       + {sidecar.name}")

        count += 1

    return count


# ── main ─────────────────────────────────────────────────────────────────────

def pick_library_folder() -> Path | None:
    """Open a native folder-picker dialog. Returns selected Path or None if cancelled."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        sys.exit("tkinter not available; pass the library path as a command-line argument.")
    root = tk.Tk()
    root.withdraw()
    # Nudge default starting location toward likely library spots on macOS.
    initial = None
    for candidate in ("/Volumes/BIGF/codevideos",
                      str(Path.home() / "Desktop" / "codevideos"),
                      "/Volumes"):
        if Path(candidate).is_dir():
            initial = candidate
            break
    selected = filedialog.askdirectory(
        title="Pick the codevideos library folder",
        initialdir=initial,
        mustexist=True,
    )
    root.destroy()
    return Path(selected) if selected else None


def main():
    ap = argparse.ArgumentParser(description="Interactive TMDB cover fetcher for EasyPlay.")
    ap.add_argument("library", type=Path, nargs="?", default=None,
                    help="Path to the codevideos folder (if omitted, a folder picker pops up)")
    ap.add_argument("--only", default=None, help="Only process folders matching this glob (e.g. 'Jojo*')")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip folders that already have a cover.jpg")
    ap.add_argument("--auto", action="store_true",
                    help="Non-interactive: auto-pick the top TMDB match per folder, "
                         "skip folders with no match. Safe because original covers "
                         "are backed up as cover.jpg.bak.")
    args = ap.parse_args()

    if args.library is None:
        picked = pick_library_folder()
        if picked is None:
            sys.exit("No folder selected.")
        args.library = picked
        print(f"Selected library: {args.library}")

    if not args.library.is_dir():
        sys.exit(f"Not a directory: {args.library}")

    organized = organize_loose_videos(args.library)
    if organized:
        print(f"Organized {organized} loose video(s) into folders.\n")

    token = load_token()
    client = TMDBClient(token)

    folders = sorted(p for p in args.library.iterdir()
                     if p.is_dir()
                     and not p.name.startswith(".")
                     and p.name not in EXCLUDE_FOLDERS)
    if args.only:
        import fnmatch
        folders = [p for p in folders if fnmatch.fnmatch(p.name, args.only)]
    if not folders:
        sys.exit("No folders to process.")

    print(f"{len(folders)} folders to process in {args.library}")
    if args.auto:
        print("(--auto: picking top match per folder, no prompts)")

    written = skipped = failed = 0
    for folder in folders:
        if args.skip_existing and (folder / "cover.jpg").exists() and not (folder / "cover.jpg.bak").exists():
            print(f"\n─ {folder.name}: has cover.jpg, skipping")
            skipped += 1
            continue

        parsed = parse_folder_name(folder.name)
        if not parsed.title:
            print(f"\n─ {folder.name}: couldn't parse title, skipping")
            skipped += 1
            continue

        match = pick_match(parsed, folder, client, auto=args.auto)
        if match is None:
            skipped += 1
            continue
        try:
            dest = save_cover(folder, client, match)
            print(f"   ✓ wrote {dest}")
            written += 1
        except Exception as e:
            print(f"   ✗ download failed: {e}")
            failed += 1

    print(f"\nDone. written={written} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
