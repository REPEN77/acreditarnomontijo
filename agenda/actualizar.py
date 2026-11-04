#!/usr/bin/env python3
"""Agenda do site Acreditar no Montijo!

Lê o calendário do Google (endereço iCal privado, na variável ICAL_URL) e
escreve agenda/eventos.json só com o que pode ser público: título, data,
local, categoria e ligação para a fonte. Descrições e notas internas do
calendário nunca saem daqui.

Regras para um evento aparecer no site:
  - tem uma linha "Fonte: ..." com uma fonte pública (sítio, Instagram);
  - ou tem a etiqueta #site ou #candidatura (no título ou na descrição);
  - ou é uma sessão da Assembleia Municipal (as sessões são públicas).
Nunca aparece se tiver #privado ou se a fonte for "indicação ...".
Eventos com #candidatura ficam em destaque.
Eventos recorrentes não são mostrados.

Corre sem dependências externas (Python 3.9+).
"""
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LISBOA = ZoneInfo("Europe/Lisbon")
SAIDA = Path(__file__).resolve().parent / "eventos.json"
DIAS_A_FRENTE = 180
MAXIMO = 60

CATEGORIAS = [
    ("🎵🎶🎤🎻🎸🎹🎺", "Música"),
    ("🎭🎬🎟", "Espetáculo"),
    ("🎓", "Comunidade"),
    ("🥾🚴🏃⚽🏊🏐🏀🚣🏅", "Desporto"),
    ("🍞🍷🧀🍽🥘🌰", "Tradição"),
    ("📇🤝", "Ação social"),
    ("🎄🎅", "Natal"),
    ("🎪🛍", "Feira e mercado"),
    ("📖📚🖼🎨", "Cultura"),
    ("🏛", "Debate e cidadania"),
]


def _texto(v):
    return (v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\"))


def _quando(valor, params):
    """Devolve (datetime local Lisboa sem fuso | date, dia_inteiro)."""
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", valor):
        return datetime.strptime(valor[:8], "%Y%m%d").date(), True
    if valor.endswith("Z"):
        dt = datetime.strptime(valor, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    else:
        fuso = ZoneInfo(params["TZID"]) if "TZID" in params else LISBOA
        dt = datetime.strptime(valor, "%Y%m%dT%H%M%S").replace(tzinfo=fuso)
    return dt.astimezone(LISBOA).replace(tzinfo=None), False


def ler_ics(texto):
    linhas = []
    for l in texto.replace("\r\n", "\n").split("\n"):
        if l[:1] in (" ", "\t") and linhas:
            linhas[-1] += l[1:]
        else:
            linhas.append(l)
    eventos, atual = [], None
    for l in linhas:
        if l == "BEGIN:VEVENT":
            atual = {}
        elif l == "END:VEVENT":
            if atual is not None:
                eventos.append(atual)
            atual = None
        elif atual is not None and ":" in l:
            cab, valor = l.split(":", 1)
            nome, *ps = cab.split(";")
            params = dict(p.split("=", 1) for p in ps if "=" in p)
            atual.setdefault(nome.upper(), (valor, params))
    saida, ignorados = [], 0
    for e in eventos:
        if "RRULE" in e or "RECURRENCE-ID" in e or e.get("STATUS", ("",))[0] == "CANCELLED" or "DTSTART" not in e:
            ignorados += 1
            continue
        ini, dia = _quando(*e["DTSTART"])
        fim = _quando(*e["DTEND"])[0] if "DTEND" in e else (ini + timedelta(days=1) if dia else ini + timedelta(hours=1))
        saida.append({
            "id": e.get("UID", ("",))[0],
            "titulo": _texto(e.get("SUMMARY", ("",))[0]),
            "descricao": _texto(e.get("DESCRIPTION", ("",))[0]),
            "local": _texto(e.get("LOCATION", ("",))[0]),
            "inicio": ini.isoformat(),
            "fim": fim.isoformat(),
            "dia_inteiro": dia,
        })
    return saida, ignorados


def _fonte(desc):
    m = re.search(r"^\s*Fonte:\s*(.+)$", desc or "", re.M | re.I)
    if not m:
        return None, None
    linha = m.group(1).strip()
    if linha.lower().startswith("indica"):
        return False, None
    url = re.search(r"https?://[^\s<>\"]+", linha)
    if url:
        return True, url.group(0).rstrip(".,;)")
    insta = re.search(r"instagram\s+@([A-Za-z0-9_.]+)", linha, re.I)
    if insta:
        return True, f"https://www.instagram.com/{insta.group(1)}/"
    return True, None


def _titulo(t):
    t = re.sub(r"\s*\|\s*PSD Montijo\s*$", "", t)
    t = re.sub(r"#\w+", "", t)
    t = re.sub(r"^[^\wÀ-ÿ\d]+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _categoria(t_bruto, titulo):
    if re.search(r"assembleia municipal", titulo, re.I) and not re.search(r"conselho", titulo, re.I):
        return "Sessão pública"
    inicio = t_bruto.strip()[:3]
    for emojis, nome in CATEGORIAS:
        if any(c in inicio for c in emojis):
            return nome
    return "Montijo"


def _local(l):
    l = re.sub(r"\(([^)]*confirmar[^)]*)\)", "(a confirmar)", l or "", flags=re.I)
    l = re.sub(r",\s*Portugal\s*$", "", l).strip()
    partes = [p.strip() for p in l.split(",") if p.strip() and not re.match(r"^\d{4}-\d{3}", p.strip())]
    curto = ", ".join(partes[:2])
    return curto, ", ".join(partes)


def publico(ev):
    tudo = f"{ev['titulo']}\n{ev.get('descricao', '')}"
    if re.search(r"#privado\b", tudo, re.I):
        return None
    titulo = _titulo(ev["titulo"])
    ok, url = _fonte(ev.get("descricao", ""))
    etiqueta = re.search(r"#(site|candidatura)\b", tudo, re.I)
    sessao = re.search(r"assembleia municipal", titulo, re.I) and not re.search(r"conselho", titulo, re.I)
    if ok is False and not etiqueta:
        return None
    if not (ok or etiqueta or sessao):
        return None
    ini, fim = ev["inicio"], ev["fim"]
    if ev["dia_inteiro"]:
        d0, d1 = date.fromisoformat(ini[:10]), date.fromisoformat(fim[:10])
        t0 = datetime(d0.year, d0.month, d0.day, tzinfo=LISBOA)
        t1 = datetime(d1.year, d1.month, d1.day, tzinfo=LISBOA)
    else:
        t0 = datetime.fromisoformat(ini).replace(tzinfo=LISBOA)
        t1 = datetime.fromisoformat(fim).replace(tzinfo=LISBOA)
    curto, morada = _local(ev.get("local", ""))
    return {
        "id": hashlib.sha1(ev["id"].encode()).hexdigest()[:10],
        "titulo": titulo,
        "categoria": "Candidatura" if re.search(r"#candidatura\b", tudo, re.I) else _categoria(ev["titulo"], titulo),
        "inicio": ini[:16] if not ev["dia_inteiro"] else ini[:10],
        "fim": fim[:16] if not ev["dia_inteiro"] else fim[:10],
        "dia_inteiro": ev["dia_inteiro"],
        "t0": int(t0.timestamp()),
        "t1": int(t1.timestamp()),
        "local": curto,
        "morada": morada,
        "url": url,
        "destaque": bool(re.search(r"#candidatura\b", tudo, re.I)),
    }


def publicar(eventos, agora=None):
    agora = agora or datetime.now(timezone.utc)
    limite = agora + timedelta(days=DIAS_A_FRENTE)
    lista = [p for p in (publico(e) for e in eventos) if p]
    lista = [p for p in lista if p["t1"] > agora.timestamp() and p["t0"] < limite.timestamp()]
    lista.sort(key=lambda p: (p["t0"], p["titulo"]))
    return {
        "actualizado": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fonte": "Google Calendar",
        "eventos": lista[:MAXIMO],
    }


def main():
    url = os.environ.get("ICAL_URL", "").strip()
    if not url:
        print("ICAL_URL não definido: a agenda fica como está.")
        return 0
    with urllib.request.urlopen(url, timeout=30) as r:
        texto = r.read().decode("utf-8", "replace")
    eventos, ignorados = ler_ics(texto)
    dados = publicar(eventos)
    anterior = None
    if SAIDA.exists():
        try:
            anterior = json.loads(SAIDA.read_text())["eventos"]
        except Exception:
            anterior = None
    if anterior == dados["eventos"]:
        print(f"Sem alterações ({len(dados['eventos'])} eventos públicos).")
        return 0
    SAIDA.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(dados['eventos'])} eventos públicos escritos; {ignorados} recorrentes ou cancelados ignorados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
