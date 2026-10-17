"""Geldbeträge sind überall ganze Cent (int), Mengen sind Decimal. Keine Fließkommazahlen."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def cent_aus_text(text: str) -> int:
    """Liest Beträge wie '1.234,56', '-12,5', '+3,00 €', '1234.56' oder '12,50-' als Cent.

    Stehen Punkt und Komma im Text, ist das hintere Zeichen das Dezimaltrennzeichen.
    Ein einzelner Punkt mit genau drei Ziffern dahinter gilt als Tausenderpunkt
    ('12.500' = 12500 €), sonst als Dezimalpunkt. Wirft ValueError bei Unsinn.
    """
    s = (text or "").strip().replace(" ", "").replace(" ", "")
    s = re.sub(r"(?i)(eur|€)", "", s)
    if not s:
        raise ValueError("leerer Betrag")
    negativ = False
    if s.endswith("-"):
        negativ, s = True, s[:-1]
    if s[0] in "+-":
        negativ, s = negativ ^ (s[0] == "-"), s[1:]
    if "," in s and "." in s:
        dezimal = "," if s.rfind(",") > s.rfind(".") else "."
    elif "," in s:
        dezimal = ","
    elif s.count(".") == 1 and len(s.split(".")[1]) != 3:
        dezimal = "."
    else:
        dezimal = None
    tausender = {",": ".", ".": ",", None: "."}[dezimal]
    s = s.replace(tausender, "")
    if dezimal == ",":
        s = s.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError(f"kein Betrag: {text!r}")
    cent = int((Decimal(s) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return -cent if negativ else cent


def euro(cent: int, zeichen: bool = True) -> str:
    """12345 → '123,45 €', -100000 → '-1.000,00 €'."""
    vorzeichen = "-" if cent < 0 else ""
    ganz, rest = divmod(abs(cent), 100)
    text = f"{vorzeichen}{ganz:,}".replace(",", ".") + f",{rest:02d}"
    return f"{text} €" if zeichen else text


def menge_aus_text(text: str) -> Decimal:
    """'1,5' oder '1.5' → Decimal('1.5'). Wirft ValueError bei Unsinn."""
    s = (text or "").strip().replace(" ", "").replace(",", ".")
    try:
        menge = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"keine Menge: {text!r}") from None
    if not menge.is_finite():
        raise ValueError(f"keine Menge: {text!r}")
    return menge


def menge_text(menge: Decimal) -> str:
    """Decimal('1.50') → '1,5', Decimal('2') → '2'."""
    text = format(menge.normalize(), "f")
    return text.replace(".", ",")


def mal(menge: Decimal, cent: int) -> int:
    """Menge × Einzelpreis in Cent, kaufmännisch gerundet."""
    return int((menge * cent).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def anteil(cent: int, prozent: int) -> int:
    """Prozentanteil in Cent, kaufmännisch gerundet (z. B. Umsatzsteuer)."""
    return int((Decimal(cent) * prozent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
