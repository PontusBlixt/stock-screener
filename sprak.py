"""
Språk på webbsidan – svenska (standard) eller engelska
======================================================

Sidorna skrivs på svenska som förut. När besökaren valt engelska översätts den färdiga HTML-sidan
precis innan den skickas (app.py, after_request):

  - varje textnod och attributen title, placeholder, aria-label, alt och value (knappar) slås upp
    i ORD (exakt text) och sedan i MONSTER (text med tal, datum och namn som varierar)
  - texten i confirm('...') i onsubmit/onclick översätts likadant
  - <script> och <style> rörs inte
  - det som inte hittas lämnas på svenska – hellre svenska än en felaktig gissning

Det betyder att också veckorapporten, analyssidorna och texter från strategiernas loggar översätts,
utan att mallarna i app.py behöver ändras. Ny svensk text på sidan syns på svenska tills den läggs till här.

Språket sparas i kakan "sprak" (ett år). /sprak/en och /sprak/sv byter.
Saknade översättningar kan listas: SCREENER_SPRAK_LOGG=1 skriver dem till <utmapp>/.saknade_oversattningar.txt
"""

from __future__ import annotations

import html
import re

from oversattning import MONSTER, ORD

STANDARD = "sv"
SPRAK = {"sv": "Svenska", "en": "English"}

_TAGG = re.compile(r"(<!--.*?-->|<[^>]+>)", re.S)
_ATTR = re.compile(r'''(\s(?:title|placeholder|aria-label|alt|data-tips)=)(["'])(.*?)\2''', re.S)
_KNAPP_VALUE = re.compile(r'''(\svalue=)(["'])(.*?)\2''', re.S)
_CONFIRM = re.compile(r"""(confirm\()(['"])(.*?)\2""", re.S)
_HTML_LANG = re.compile(r'<html lang="sv"')
_BOKSTAV = re.compile(r"[A-Za-zÅÄÖåäöÉé]")

# Mönster: "{}" = valfri text (tal, datum, namn). Kompileras en gång, längsta först.
_MONSTER = []
for _sv, _en in sorted(MONSTER.items(), key=lambda x: -len(x[0])):
    delar = [re.escape(d) for d in _sv.split("{}")]
    # En variabel del får inte gå över "; " eller " · " – då är det flera texter som ska översättas var för sig
    _MONSTER.append((re.compile("^" + r"((?:(?!;\s|\s·\s).)+?)".join(delar) + "$", re.S), _en))

saknade: set[str] = set()


_BARA_TAL = re.compile(r"^[\s\d.,:;%+\-−–×→←↑↓·/()|*=<>≤≥~]*(kr|SEK|MSEK|Mkr|st|%|x|d|v|pe)?[\s\d.,%+\-−–()]*$")
_DELNING = re.compile(r"(\s+·\s+|;\s+|\s+–\s+|(?<=[.!?:])\s+(?=[A-ZÅÄÖ0-9\"„]))")


def _hela(t: str) -> str | None:
    """Översättning av en hel textbit, eller None."""
    if t in ORD:
        return ORD[t]
    for rx, en in _MONSTER:
        m = rx.match(t)
        if m:
            return en.format(*[oversatt_del(g) for g in m.groups()])
    if t.endswith((".", ":")) and t[:-1] in ORD:      # samma text med eller utan punkt
        return ORD[t[:-1]] + t[-1]
    for led in ("· ", "– ", "— ", "• "):               # inledande avskiljare
        if t.startswith(led) and len(t) > len(led):
            x = _hela(t[len(led):])
            return led + x if x is not None else None
    return None


def oversatt_text(t: str) -> str:
    """En textbit (utan HTML). Behåller blanktecken före och efter."""
    if not t or not _BOKSTAV.search(t):
        return t
    kort = t.strip()
    fore, efter = t[: len(t) - len(t.lstrip())], t[len(t.rstrip()):]
    ren = " ".join(html.unescape(kort).split())     # radbrytningar i mallarna räknas som mellanslag
    if re.fullmatch(r"[+\-−]?[\d\s.,]+ kr", ren):        # belopp: "12 163 kr" → "12 163 SEK"
        return fore + ren[:-2] + "SEK" + efter
    if _BARA_TAL.match(ren):
        return t
    hel = _hela(ren)
    if hel is not None:
        return fore + html.escape(hel, quote=False) + efter
    # Flera meningar eller delar (·, ;, –): översätt bit för bit
    delar = _DELNING.split(ren)
    if len(delar) > 1:
        ut, traff = [], False
        for i, d in enumerate(delar):
            if i % 2 == 1 or not d or not _BOKSTAV.search(d) or _BARA_TAL.match(d):
                ut.append(d)
                continue
            x = _hela(d)
            traff |= x is not None
            if x is None:
                saknade.add(d)
            ut.append(x if x is not None else d)
        if traff:
            return fore + html.escape("".join(ut), quote=False) + efter
    saknade.add(ren)
    return t


def oversatt_del(t: str) -> str:
    """En variabel del inne i ett mönster: översätts om den själv är en känd text, annars som den är."""
    s = t.strip()
    if not s or not _BOKSTAV.search(s):
        return t
    x = _hela(s)
    return t.replace(s, x) if x is not None else t


def _attr(m: re.Match) -> str:
    return m.group(1) + m.group(2) + html.escape(html.unescape(oversatt_text(html.unescape(m.group(3)))),
                                                 quote=True) + m.group(2)


def _confirm(m: re.Match) -> str:
    return m.group(1) + m.group(2) + oversatt_text(m.group(3)).replace("'", "\\'") + m.group(2)


def _tagg(tagg: str) -> str:
    if tagg.startswith("<!--"):
        return tagg
    tagg = _ATTR.sub(_attr, tagg)
    lag = tagg[:12].lower()
    if lag.startswith("<input") or lag.startswith("<button") or lag.startswith("<option"):
        if re.search(r"""type=["']?(submit|button|reset)""", tagg, re.I) or not lag.startswith("<input"):
            tagg = _KNAPP_VALUE.sub(lambda m: m.group(1) + m.group(2) + oversatt_text(m.group(3)) + m.group(2)
                                    if not re.fullmatch(r"[\w.\-/:]+", m.group(3)) or m.group(3) in ORD else m.group(0),
                                    tagg)
    if "confirm(" in tagg:
        tagg = _CONFIRM.sub(_confirm, tagg)
    return tagg


def oversatt_html(sida: str, sprak: str) -> str:
    if sprak == STANDARD or not sida:
        return sida
    ut, i, skydd = [], 0, None
    for m in _TAGG.finditer(sida):
        text = sida[i:m.start()]
        ut.append(text if skydd else oversatt_text(text))
        tagg = m.group(0)
        lag = tagg[:8].lower()
        if skydd:
            if lag.startswith("</" + skydd):
                skydd = None
            ut.append(tagg)
        else:
            if lag.startswith("<script") or lag.startswith("<style"):
                skydd = "script" if lag.startswith("<script") else "style"
                ut.append(tagg)
            else:
                ut.append(_tagg(tagg))
        i = m.end()
    ut.append(oversatt_text(sida[i:]) if not skydd else sida[i:])
    return _HTML_LANG.sub(f'<html lang="{sprak}"', "".join(ut))


def vaxlare(sprak: str, nasta: str) -> str:
    """Liten länk i menyn: 🌐 EN / SV."""
    annat = "en" if sprak == "sv" else "sv"
    from urllib.parse import quote
    return (f'<a class="sprakval" href="/sprak/{annat}?nasta={quote(nasta, safe="/")}" '
            f'title="{"Switch to English" if annat == "en" else "Byt till svenska"}" lang="{annat}">'
            f'{"EN" if annat == "en" else "SV"}</a>')
