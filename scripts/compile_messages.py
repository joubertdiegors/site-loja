"""Compila os arquivos .po em .mo sem depender do gettext instalado.

O comando ``python manage.py compilemessages`` do Django precisa do binário
``msgfmt`` (pacote gettext), que não vem no Windows. Este script faz o mesmo
trabalho em Python puro, no formato binário .mo documentado pelo GNU gettext.

Uso::

    python scripts/compile_messages.py            # compila tudo em locale/
    python scripts/compile_messages.py fr nl      # só esses idiomas

Se você instalar o gettext (por exemplo com ``choco install gettext``), pode
usar ``manage.py makemessages`` / ``manage.py compilemessages`` normalmente —
este script continua funcionando e produz o mesmo resultado.
"""

from __future__ import annotations

import array
import struct
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / "locale"


def parse_po(path: Path) -> dict[str, str]:
    """Lê um .po e devolve {msgid: msgstr}.

    Suporta msgid/msgstr em várias linhas, msgid_plural/msgstr[n] e comentários.
    Entradas fuzzy ou com tradução vazia são ignoradas (o gettext faz o mesmo:
    sem tradução, vale o texto original).
    """
    catalog: dict[str, str] = {}

    msgid = msgid_plural = None
    msgstrs: dict[int, str] = {}
    current: list[str] | None = None
    buffer: dict[str, list[str]] = {}
    fuzzy = False
    plural_index = 0

    def flush():
        nonlocal msgid, msgid_plural, msgstrs, fuzzy
        if msgid is None:
            return
        if not fuzzy:
            if msgid_plural is not None:
                singular = msgstrs.get(0, "")
                plural = msgstrs.get(1, "")
                if singular:
                    catalog[f"{msgid}\x00{msgid_plural}"] = f"{singular}\x00{plural or singular}"
            else:
                text = msgstrs.get(0, "")
                if text:
                    catalog[msgid] = text
        msgid = msgid_plural = None
        msgstrs = {}
        fuzzy = False

    def unquote(line: str) -> str:
        line = line.strip()
        start = line.find('"')
        end = line.rfind('"')
        if start == -1 or end <= start:
            return ""
        raw = line[start + 1 : end]
        return (
            raw.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
            continue
        if line.startswith("#") or not line:
            if not line:
                flush()
                current = None
            continue

        if line.startswith("msgid_plural"):
            msgid_plural = unquote(line)
            current = ["plural_id"]
            continue
        if line.startswith("msgid"):
            flush()
            msgid = unquote(line)
            current = ["id"]
            continue
        if line.startswith("msgstr["):
            plural_index = int(line[line.index("[") + 1 : line.index("]")])
            msgstrs[plural_index] = unquote(line)
            current = ["str", plural_index]
            continue
        if line.startswith("msgstr"):
            msgstrs[0] = unquote(line)
            current = ["str", 0]
            continue

        if line.startswith('"') and current:
            piece = unquote(line)
            if current[0] == "id":
                msgid = (msgid or "") + piece
            elif current[0] == "plural_id":
                msgid_plural = (msgid_plural or "") + piece
            else:
                index = current[1]
                msgstrs[index] = msgstrs.get(index, "") + piece

    flush()
    buffer.clear()
    return catalog


def build_mo(catalog: dict[str, str]) -> bytes:
    """Serializa o catálogo no formato binário .mo (little-endian)."""
    entries = sorted(catalog.items())
    ids = b""
    strs = b""
    offsets = []

    for msgid, msgstr in entries:
        encoded_id = msgid.encode("utf-8")
        encoded_str = msgstr.encode("utf-8")
        offsets.append((len(ids), len(encoded_id), len(strs), len(encoded_str)))
        ids += encoded_id + b"\x00"
        strs += encoded_str + b"\x00"

    count = len(entries)
    key_start = 7 * 4 + 16 * count
    value_start = key_start + len(ids)

    key_offsets: list[int] = []
    value_offsets: list[int] = []
    for id_offset, id_length, str_offset, str_length in offsets:
        key_offsets += [id_length, id_offset + key_start]
        value_offsets += [str_length, str_offset + value_start]

    output = struct.pack(
        "Iiiiiii",
        0x950412DE,  # número mágico
        0,  # versão
        count,
        7 * 4,  # início da tabela de chaves
        7 * 4 + count * 8,  # início da tabela de valores
        0,
        0,
    )
    output += array.array("i", key_offsets + value_offsets).tobytes()
    output += ids
    output += strs
    return output


def compile_locale(language_dir: Path) -> int:
    compiled = 0
    for po_path in sorted(language_dir.glob("LC_MESSAGES/*.po")):
        catalog = parse_po(po_path)
        mo_path = po_path.with_suffix(".mo")
        mo_path.write_bytes(build_mo(catalog))
        print(f"  {po_path.relative_to(BASE_DIR)} -> {mo_path.name} ({len(catalog)} entradas)")
        compiled += 1
    return compiled


def main(argv: list[str]) -> int:
    if not LOCALE_DIR.exists():
        print(f"Pasta não encontrada: {LOCALE_DIR}")
        return 1

    wanted = set(argv[1:])
    total = 0
    for language_dir in sorted(LOCALE_DIR.iterdir()):
        if not language_dir.is_dir():
            continue
        if wanted and language_dir.name not in wanted:
            continue
        total += compile_locale(language_dir)

    print(f"{total} arquivo(s) compilado(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
