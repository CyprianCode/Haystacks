"""
Search data and search logic for Haystacks, separate from the window so
it can be tested on its own.
"""
import datetime as dt
import re

import pipeline


def hms(t):
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_terms(query):
    """Words and "quoted phrases", lowercased."""
    return [(p or w).strip() for p, w in re.findall(r'"([^"]+)"|(\S+)', query.lower())
            if (p or w).strip()]


def highlight_spans(text, terms):
    """[(start, end)] of every term in text, case-insensitive, non-overlapping."""
    if not terms:
        return []
    rx = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)),
                    re.IGNORECASE)
    return [m.span() for m in rx.finditer(text)]


class Library:
    """Search data of every folder merged into one, oldest recording first."""

    def __init__(self, entries):
        """entries: the settings folder entries ({"path", optional "transcripts"})."""
        self.files = []    # dicts: folder, stem, video, when, label, key, date
        self.segs = []     # (file index, start, text, dB or None), in file order
        self.moments = []  # (file index, time, dB, seg index or -1)
        per = []
        for entry in entries:
            folder, out, _ = pipeline.entry_dirs(entry)
            data = pipeline.load_folder(folder, out)
            if data:
                per.append((entry["path"], data))

        order = sorted((f[2] is None, f[2] or dt.datetime.min, f[0].lower(), k, lf)
                       for k, (_, (files, *_)) in enumerate(per)
                       for lf, f in enumerate(files))
        seg_ids = {}
        for k, (_, (_, segs, _, _)) in enumerate(per):
            for gi, s in enumerate(segs):
                seg_ids.setdefault((k, s[0]), []).append(gi)

        new_fi, new_si = {}, {}
        for *_, k, lf in order:
            path, (files, segs, _, _) = per[k]
            stem, video, when, label = files[lf]
            new_fi[k, lf] = fi = len(self.files)
            self.files.append({"folder": path, "stem": stem, "video": video, "when": when,
                               "label": label, "key": f"{path}|{stem}",
                               "date": when.date().isoformat() if when else None})
            for gi in seg_ids.get((k, lf), []):
                new_si[k, gi] = len(self.segs)
                _, start, text, db = segs[gi]
                self.segs.append((fi, start, text, db))
        for k, (_, (_, _, moments, _)) in enumerate(per):
            for lf, t, db, si in moments:
                self.moments.append((new_fi[k, lf], t, db,
                                     new_si.get((k, si), -1) if si >= 0 else -1))
        self.norm = [s[2].lower() for s in self.segs]

    def allowed(self, hidden=(), folder=None, date_from=None, date_to=None):
        """File indexes not hidden, in the folder (None: all) and the date range
        (ISO dates or None). Undated recordings drop out once a date is set."""
        ok = set()
        for fi, f in enumerate(self.files):
            if f["key"] in hidden or (folder and f["folder"] != folder):
                continue
            if date_from or date_to:
                d = f["date"]
                if not d or (date_from and d < date_from) or (date_to and d > date_to):
                    continue
            ok.add(fi)
        return ok

    def search(self, terms, ok, sort="new"):
        """Sentence indexes containing every term, from allowed files.
        sort: "new" (newest recording first), "old", or "loud"."""
        if not terms:
            return []
        segs, norm = self.segs, self.norm
        first, rest = terms[0], terms[1:]  # one cheap test rules out most sentences
        hits = [i for i, n in enumerate(norm) if first in n and segs[i][0] in ok]
        if rest:
            hits = [i for i in hits if all(t in norm[i] for t in rest)]
        if sort == "loud":
            hits.sort(key=lambda i: -(segs[i][3] if segs[i][3] is not None else -999))
        elif sort == "new":
            hits.sort(key=lambda i: (-segs[i][0], i))
        return hits

    def loudest(self, ok):
        """Loud moments from allowed files, loudest first."""
        return sorted((m for m in self.moments if m[0] in ok), key=lambda m: -m[2])

    def context(self, i):
        """(previous sentence, next sentence) in the same recording, or ''."""
        fi = self.segs[i][0]
        before = self.segs[i - 1][2] if i > 0 and self.segs[i - 1][0] == fi else ""
        after = (self.segs[i + 1][2]
                 if i + 1 < len(self.segs) and self.segs[i + 1][0] == fi else "")
        return before, after
