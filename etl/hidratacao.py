"""Hidratação de um aeródromo: tudo que a REDEMET e a AISWEB sabem sobre ele.

Quando se consulta um aeródromo, a API não devolve só o status dele: as duas
fontes têm serviços próprios keyed por `icaoCode`, e cada um devolve algo
diferente sobre o mesmo lugar. Este módulo consulta **todos** eles numa ida.

**A regra que dá sentido ao módulo: só grava o que mudou.** Cada resposta é
guardada em `aerodromo_hidratacao` com o `sha256` do conteúdo. Reidratar o mesmo
aeródromo duas vezes custa duas consultas e **zero** gravações — e a tela mostra
quantos registros eram novos e quantos eram idênticos, que é a diferença entre
"buscou" e "atualizou".

**Nada disso sobrescreve `aerodromo`.** A fonte da ANAC continua intacta: as duas
informações ficam lado a lado e a divergência fica visível. A hidratação escreve
em tabelas próprias justamente para isso — um aeródromo cujo nome mudou na ANAC
não pode ser corrigido por um serviço meteorológico.

Credenciais: a REDEMET usa `secrets/redemet.key` (já existe). A AISWEB exige
`apiKey` **e** `apiPass`, e vem de `secrets/aisweb.key` no formato
`apiKey:apiPass` (ou duas linhas). Sem esse arquivo, os oito serviços do AISWEB
são marcados `sem_chave` e a tela diz o que fazer — em vez de falhar a
hidratação inteira por causa de um serviço só.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from redemet_client import RedemetClient, RedemetErro

AISWEB = "https://aisweb.decea.gov.br/api"
SEGREDO_AISWEB = Path(__file__).resolve().parents[1] / "secrets" / "aisweb.key"
TIMEOUT = 40
JANELA_HORAS = 6
DELAY_AISWEB = 0.6          # entre requisições, para não martelar o DECEA

# Os oito serviços do AISWEB que aceitam `icaoCode`. A ordem importa: as
# primeiras são as mais específicas do aeródromo e saem antes na tela.
AREAS_AISWEB = (
    ("cartas", {"icaoCode": "{icao}"}, "cartas aeronáuticas"),
    ("suplementos", {"IcaoCode": "{icao}"}, "suplementos AIP"),
    ("rotaer", {"icaoCode": "{icao}"}, "ROTAER"),
    ("notam", {"icaocode": "{icao}"}, "NOTAM"),
    ("met", {"icaoCode": "{icao}"}, "meteorologia"),
    ("sol", {"icaoCode": "{icao}"}, "nascer e pôr do sol"),
    ("geiloc", {"name": "{icao}"}, "geo-localização"),
    ("waypoints", {}, "surrounding waypoints"),
)


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ============================================================ credenciais
def credencial_aisweb() -> tuple[str, str] | None:
    """(apiKey, apiPass) de `secrets/aisweb.key`, ou `None`.

    Aceita `apiKey:apiPass` numa linha e também uma chave por linha, porque
    ninguém decora o formato exato e exigir um formato único só cria atrito.
    """
    if not SEGREDO_AISWEB.exists():
        return None
    bruto = SEGREDO_AISWEB.read_text(encoding="utf-8").strip()
    if not bruto:
        return None
    if ":" in bruto:
        chave, _, senha = bruto.partition(":")
        return chave.strip(), senha.strip()
    linhas = [l.strip() for l in bruto.splitlines() if l.strip()]
    if len(linhas) >= 2:
        return linhas[0], linhas[1]
    return None, ""


def _get_aisweb(chave: str, senha: str, area: str, params: dict) -> dict:
    consulta = {"apiKey": chave, "apiPass": senha, "area": area, **params}
    url = AISWEB + "?" + urllib.parse.urlencode(consulta)
    req = urllib.request.Request(url, headers={
        "Accept": "application/xml, application/json, */*",
        "User-Agent": "anac-db/1.0 (dados abertos ANAC)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return {"bytes": resp.read(), "status": resp.status,
                "tipo": resp.headers.get("Content-Type", "")}


def _resumo_texto(bruto: bytes, tipo: str) -> dict:
    """Contagens do que veio, para mostrar sem despejar o XML na tela."""
    try:
        texto = bruto.decode("utf-8", errors="replace")
    except Exception:                              # noqa: BLE001
        return {"itens": None}
    if "json" in tipo or texto.lstrip()[:1] in "{[":
        try:
            dados = json.loads(texto)
        except ValueError:
            return {"itens": None}
        if isinstance(dados, list):
            return {"itens": len(dados)}
        if isinstance(dados, dict):
            for chave in ("data", "features", "results"):
                if isinstance(dados.get(chave), list):
                    return {"itens": len(dados[chave])}
            return {"itens": 1}
        return {"itens": 1}
    # XML: conta itens pelo nome do elemento que mais se repete.
    import re
    achados = re.findall(r"<([A-Za-z][\w.-]*)>", texto)
    contagem: dict[str, int] = {}
    for nome in achados:
        contagem[nome] = contagem.get(nome, 0) + 1
    itens = [n for n in contagem if n.lower() in
             ("item", "notam", "carta", "point", "feature", "suplemento", "record")]
    return {"itens": (max(contagem[n] for n in itens) if itens
                      else len(re.findall(r"<item|<record|<Feature", texto)) or None),
            "tags": len(achados)}


# ================================================================== gravação
def _grava(conn, icao, fonte, endpoint, parametros, status, resumo,
           conteudo: bytes | None) -> bool:
    """Guarda uma resposta. Devolve `True` **só** se o conteúdo era diferente.

    O `sha256` é a chave: mesma resposta ⇒ mesma linha, sem `UPDATE`. É isso
    que permite reidratar sem rewrite, e é o que a tela reporta como
    "iguais" em vez de "atualizados".
    """
    bruto = conteudo or b""
    sha = hashlib.sha256(bruto).hexdigest()
    texto = None
    if bruto:
        try:
            texto = bruto.decode("utf-8", errors="replace")
            if len(texto) > 200_000:
                texto = texto[:200_000] + "\n… (truncado para exibição)"
        except Exception:                          # noqa: BLE001
            texto = None

    anterior = conn.execute(
        "SELECT conteudo_sha256 FROM aerodromo_hidratacao "
        "WHERE icao = ? AND fonte = ? AND endpoint = ?",
        (icao, fonte, endpoint)).fetchone()
    mudou = anterior is None or anterior[0] != sha

    conn.execute(
        "INSERT INTO aerodromo_hidratacao (icao, fonte, endpoint, parametros, "
        "status, resumo, conteudo, conteudo_sha256, bytes, coletado_em, mudou) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(icao, fonte, endpoint) DO UPDATE SET "
        "parametros=excluded.parametros, status=excluded.status, "
        "resumo=excluded.resumo, conteudo=excluded.conteudo, "
        "conteudo_sha256=excluded.conteudo_sha256, bytes=excluded.bytes, "
        "coletado_em=excluded.coletado_em, "
        "mudou=CASE WHEN aerodromo_hidratacao.conteudo_sha256 "
        "            IS NOT excluded.conteudo_sha256 THEN 1 ELSE 0 END",
        (icao, fonte, endpoint, json.dumps(parametros, ensure_ascii=False),
         status, json.dumps(resumo, ensure_ascii=False), texto, sha,
         len(bruto), _agora(), 1 if mudou else 0))
    return mudou


# ================================================================== REDEMET
def _redemet(conn, icao: str, cli: RedemetClient, relatorio: list) -> int:
    """Status, METAR e TAF da localidade. Devolve quantos registros mudaram."""
    novos = 0

    # O endpoint de status é do país inteiro, não por localidade: a API da
    # REDEMET não tem filtro de OACI ali. Filtrar no servidor é o que existe —
    # e é uma requisição, não seis.
    try:
        dados = cli.status_aerodromos()
        achado = next((r for r in dados if r and str(r[0]).strip().upper() == icao), None)
        if achado is None:
            relatorio.append({"fonte": "REDEMET", "endpoint": "/aerodromos/status",
                              "status": "sem_registro",
                              "detalhe": f"{icao} não consta na lista da API"})
        else:
            lat = achado[2] if len(achado) > 2 else None
            lon = achado[3] if len(achado) > 3 else None
            resumo = {"icao": icao, "cor": achado[4] if len(achado) > 4 else None,
                      "mensagem": (achado[5] if len(achado) > 5 else None)}
            # A comparação de coordenada vem da ANAC e é do servidor que roda
            # isto: se a REDEMET mover o ponto, isso aparece como divergência.
            resumo["divergencia_lat"] = _divergencia(conn, icao, "latitude", lat)
            resumo["divergencia_lon"] = _divergencia(conn, icao, "longitude", lon)
            mudou = _grava(conn, icao, "REDEMET", "/aerodromos/status",
                           {"pais": "BRASIL", "icao": icao}, "ok", resumo,
                           json.dumps(achado, ensure_ascii=False).encode("utf-8"))
            novos += 1 if mudou else 0
            relatorio.append({
                "fonte": "REDEMET", "endpoint": "/aerodromos/status",
                "status": "ok" if mudou else "inalterado",
                "detalhe": f"cor={resumo.get('cor')} · "
                           + (f"divergência de coordenada: {resumo['divergencia_lat']}"
                              if resumo.get("divergencia_lat") else "coordenada confere com a ANAC")})
    except RedemetErro as exc:
        relatorio.append({"fonte": "REDEMET", "endpoint": "/aerodromos/status",
                          "status": "erro", "detalhe": str(exc)})
        _grava(conn, icao, "REDEMET", "/aerodromos/status", {"icao": icao},
               "erro", {"erro": str(exc)}, None)
        return novos

    fim = datetime.now(timezone.utc)
    ini = (fim - timedelta(hours=JANELA_HORAS)).strftime("%Y%m%d%H")
    data_fim = fim.strftime("%Y%m%d%H")
    for tipo, metodo in (("METAR", cli.metar), ("TAF", cli.taf)):
        try:
            registros = metodo([icao], data_ini=ini, data_fim=data_fim)
        except RedemetErro as exc:
            relatorio.append({"fonte": "REDEMET", "endpoint": f"/mensagens/{tipo.lower()}",
                              "status": "erro", "detalhe": str(exc)})
            _grava(conn, icao, "REDEMET", f"/mensagens/{tipo.lower()}", {"icao": icao},
                   "erro", {"erro": str(exc)}, None)
            continue

        resumo = {"tipo": tipo, "mensagens": len(registros),
                  "validade": [r.get("validade_inicial") for r in registros[:3]]}
        corpo = json.dumps(registros, ensure_ascii=False).encode("utf-8")
        if _grava(conn, icao, "REDEMET", f"/mensagens/{tipo.lower()}",
                  {"icao": icao, "data_ini": ini, "data_fim": data_fim},
                  "ok" if registros else "sem_registro", resumo, corpo):
            novos += 1

        # As mensagens também vão para `redemet_mensagem`, que é onde o painel
        # e o detalhe as leem. Aqui a escrita é idempotente por
        # (tipo, icao, validade_inicial, mensagem), então repetir não duplica.
        if registros:
            conhecidos = {r[0] for r in conn.execute("SELECT icao FROM aerodromo")}
            linhas = [(tipo, icao, icao if icao in conhecidos else None,
                       r.get("validade_inicial"), r.get("validade_final"),
                       r.get("mens"), r.get("recebimento"), _agora())
                      for r in registros if r.get("id_localidade")]
            conn.executemany(
                "INSERT INTO redemet_mensagem (tipo, icao, aerodromo_icao, "
                "validade_inicial, validade_final, mensagem, recebimento, colhido_em) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tipo, icao, validade_inicial, mensagem) DO UPDATE SET "
                "validade_final=excluded.validade_final, "
                "recebimento=excluded.recebimento, colhido_em=excluded.colhido_em",
                linhas)
            relatorio.append({"fonte": "REDEMET", "endpoint": f"/mensagens/{tipo.lower()}",
                              "status": "ok", "detalhe": f"{len(registros)} mensagem(ns)"})
        else:
            relatorio.append({"fonte": "REDEMET", "endpoint": f"/mensagens/{tipo.lower()}",
                              "status": "sem_registro",
                              "detalhe": f"nenhum {tipo} na janela de {JANELA_HORAS}h"})
    return novos


def _divergencia(conn, icao, coluna, valor) -> str | None:
    """A fonte da ANAC e a fonte externa discordam? Devolve a frase, não um bool.

    Comparar string com float daria falso positivo em quase toda linha — o
    SQLite guarda `-23.4322` de um jeito e a API devolve `-23.43220` de outro.
    Por isso a comparação é numérica quando dá, e o texto só existe se a
    diferença for de fato relevante.
    """
    if valor in (None, ""):
        return None
    oficial = conn.execute(f"SELECT {coluna} FROM aerodromo WHERE icao = ?",
                           (icao,)).fetchone()
    if oficial is None or oficial[0] in (None, ""):
        return None
    try:
        if abs(float(oficial[0]) - float(valor)) < 0.001:
            return None
    except (TypeError, ValueError):
        return None
    return f"ANAC {coluna}={oficial[0]} · fonte externa={valor}"


# =================================================================== AISWEB
def _aisweb(conn, icao: str, relatorio: list) -> int:
    """Os oito serviços do AISWEB para um OACI. Devolve quantos mudaram."""
    credencial = credencial_aisweb()
    if credencial is None or not credencial[0]:
        relatorio.append({"fonte": "AISWEB", "endpoint": "*", "status": "sem_chave",
                          "detalhe": f"credencial ausente em {SEGREDO_AISWEB.name} "
                                     "(formato apiKey:apiPass). A hidratação do AISWEB "
                                     "fica desligada até o arquivo existir."})
        return 0

    chave, senha = credencial
    novos = 0
    import time
    for area, modelo, rotulo in AREAS_AISWEB:
        parametros = {k: v.format(icao=icao) for k, v in modelo.items()}
        endpoint = f"/{area}" + ("" if not parametros else
                                 "?" + "&".join(f"{k}={v}" for k, v in parametros.items()))
        try:
            resposta = _get_aisweb(chave, senha, area, parametros)
        except urllib.error.HTTPError as exc:
            relatorio.append({"fonte": "AISWEB", "endpoint": endpoint,
                              "status": "erro", "detalhe": f"HTTP {exc.code}"})
            _grava(conn, icao, "AISWEB", area, parametros, "erro",
                   {"erro": f"HTTP {exc.code}"}, None)
            time.sleep(DELAY_AISWEB)
            continue
        except Exception as exc:                   # noqa: BLE001
            relatorio.append({"fonte": "AISWEB", "endpoint": endpoint,
                              "status": "erro", "detalhe": f"{type(exc).__name__}: {exc}"})
            _grava(conn, icao, "AISWEB", area, parametros, "erro",
                   {"erro": str(exc)}, None)
            time.sleep(DELAY_AISWEB)
            continue

        corpo = resposta["bytes"]
        resumo = _resumo_texto(corpo, resposta["tipo"])
        resumo.update({"rotulo": rotulo, "http": resposta["status"],
                       "content_type": resposta["tipo"]})
        vazio = (len(corpo) < 400 and (resumo.get("itens") in (0, None)))
        if _grava(conn, icao, "AISWEB", area, parametros,
                  "sem_registro" if vazio else "ok", resumo, corpo):
            novos += 1
        relatorio.append({"fonte": "AISWEB", "endpoint": endpoint,
                          "status": "sem_registro" if vazio else "ok",
                          "detalhe": f"{rotulo}: {resumo.get('itens') or 0} registro(s), "
                                     f"{len(corpo)} bytes"})
        time.sleep(DELAY_AISWEB)
    return novos


# ============================================================== orquestrador
def hidratar(conn: sqlite3.Connection, icao: str) -> dict:
    """Hidrata um aeródromo inteiro e devolve o relatório da ida."""
    icao = (icao or "").strip().upper()
    inicio = _agora()
    if not icao:
        raise ValueError("icao é obrigatório")
    if conn.execute("SELECT 1 FROM aerodromo WHERE icao = ?", (icao,)).fetchone() is None:
        raise ValueError(f"aeródromo {icao} não existe no cadastro da ANAC")

    exec_id = conn.execute(
        "INSERT INTO hidratacao_execucao (icao, iniciada_em, estado) "
        "VALUES (?, ?, 'rodando')", (icao, inicio)).lastrowid
    conn.commit()

    relatorio: list[dict] = []
    novos = iguais = 0
    try:
        antes = {r[0] for r in conn.execute(
            "SELECT endpoint FROM aerodromo_hidratacao WHERE icao = ?", (icao,))}

        novos += _redemet(conn, icao, RedemetClient(delay=1.0), relatorio)
        novos += _aisweb(conn, icao, relatorio)

        depois = {r[0] for r in conn.execute(
            "SELECT endpoint FROM aerodromo_hidratacao WHERE icao = ?", (icao,))}
        iguais = len(depois) - novos
        conn.execute(
            "UPDATE hidratacao_execucao SET concluida_em = ?, estado = 'ok', "
            "endpoints_ok = ?, endpoints_erro = ?, registros_novos = ?, "
            "registros_iguais = ? WHERE id = ?",
            (_agora(), sum(1 for r in relatorio if r["status"] == "ok"),
             sum(1 for r in relatorio if r["status"] not in ("ok", "sem_chave")),
             novos, max(iguais, 0), exec_id))
    except Exception as exc:                       # noqa: BLE001
        relatorio.append({"fonte": "*", "endpoint": "*", "status": "erro",
                          "detalhe": f"{type(exc).__name__}: {exc}"})
        conn.execute(
            "UPDATE hidratacao_execucao SET concluida_em = ?, estado = 'erro', "
            "mensagem = ?, registros_novos = ? WHERE id = ?",
            (_agora(), str(exc), novos, exec_id))
    conn.commit()

    return {
        "icao": icao,
        "execucao_id": exec_id,
        "iniciada_em": inicio,
        "novos": novos,
        "iguais": max(iguais, 0),
        "relatorio": relatorio,
        "credencial_aisweb": bool(credencial_aisweb()),
        "fontes": listar(conn, icao),
    }


def listar(conn: sqlite3.Connection, icao: str, com_conteudo: bool = False) -> list[dict]:
    """O que já foi hidratado de um aeródromo."""
    colunas = ("icao, fonte, endpoint, parametros, status, resumo, bytes, "
               "conteudo_sha256, coletado_em, mudou")
    extra = ", conteudo" if com_conteudo else ""
    linhas = conn.execute(
        f"SELECT {colunas}{extra} FROM aerodromo_hidratacao WHERE icao = ? "
        "ORDER BY fonte, endpoint", (icao,)).fetchall()
    nomes = [c.strip() for c in colunas.split(",")] + (
        ["conteudo"] if com_conteudo else [])
    saida = []
    for linha in linhas:
        item = dict(zip(nomes, linha))
        try:
            item["resumo"] = json.loads(item["resumo"] or "{}")
            item["parametros"] = json.loads(item["parametros"] or "{}")
        except ValueError:
            item["resumo"] = {"bruto": item["resumo"]}
        saida.append(item)
    return saida
