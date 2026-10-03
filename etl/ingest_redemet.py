"""Ingestão da API-REDEMET: status de aeródromo, METAR e TAF.

A API é infraestrutura militar e a documentação impõe limite de uso. Por isso a
coleta é limitada por padrão e configurável por variável de ambiente:

    REDEMET_MAX_AERODROMOS   quantas localidades buscar (padrão 25)
    REDEMET_DELAY_S         intervalo entre requisições (padrão 1.0s)
    REDEMET_HORAS           janela de histórico para METAR/TAF (padrão 6)

A escolha das localidades é o ponto sensível: consultar as 6.139 do cadastro
seria abusivo. O padrão usa as que a própria REDEMET reporta com METAR
disponível, e ordena pelas que o banco da ANAC reconhece como aeródromo público —
as que alguém realmente consulta.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from redemet_client import RedemetClient, RedemetErro

MAX_AERODROMOS = int(os.environ.get("REDEMET_MAX_AERODROMOS", "25"))
DELAY_S = float(os.environ.get("REDEMET_DELAY_S", "1.0"))
HORAS = int(os.environ.get("REDEMET_HORAS", "6"))
LOTE = 10  # localidades por requisição de mensagem
# Lista explícita tem precedencia sobre a escolha automática.
ICAOS_FIXOS = [c.strip().upper() for c in
               os.environ.get("REDEMET_ICAOS", "").split(",") if c.strip()]


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _janela() -> tuple[str, str]:
    """`data_ini`/`data_fim` no formato `YYYYMMDDHH` que a API exige."""
    fim = datetime.now(timezone.utc)
    ini = fim - timedelta(hours=HORAS)
    return ini.strftime("%Y%m%d%H"), fim.strftime("%Y%m%d%H")


def escolher_icaos(conn: sqlite3.Connection, limite: int = MAX_AERODROMOS) -> list[str]:
    """Escolhe quais localidades consultar.

    A lista da própria REDEMET é a melhor fonte: são exatamente as localidades
    que têm serviço meteorológico ativo. Consultar aeródromos do cadastro da
    ANAC às cegas geraria requisições vazias para quem não emite METAR — e a
    resposta de status carrega essa informação.

    Filtra pelas que também existem no cadastro da ANAC, para que a mensagem
    tenha onde se ligar, e cai para os públicos se a interseção vier vazia.
    """
    if ICAOS_FIXOS:
        return ICAOS_FIXOS[:limite]

    direta = conn.execute("""
        SELECT r.icao
        FROM redemet_aerodromo_status r
        JOIN aerodromo a ON a.icao = r.icao AND a.tipo = 'PUBLICO'
        ORDER BY r.icao LIMIT ?
    """, (limite,)).fetchall()
    if direta:
        return [r[0] for r in direta]

    alternativa = conn.execute("""
        SELECT icao FROM aerodromo
        WHERE tipo='PUBLICO' AND icao IS NOT NULL
        ORDER BY icao LIMIT ?
    """, (limite,)).fetchall()
    return [r[0] for r in alternativa]


def carregar_status(conn: sqlite3.Connection, cli: RedemetClient) -> int:
    """`/aerodromos/status` — cor por visibilidade e teto de nuvem, conforme a tabela da API.

    O endpoint devolve listas posicionais, não objetos: a cor é o 5º campo e a
    mensagem de ausência de METAR o 6º (ausente em registros sem problema).
    """
    t0 = time.monotonic()
    try:
        dados = cli.status_aerodromos()
    except RedemetErro as exc:
        conn.execute(
            "INSERT INTO redemet_coleta (endpoint, parametros, status, mensagem) "
            "VALUES (?, ?, ?, ?)", ("/aerodromos/status", "pais=BRASIL", "erro", str(exc)))
        conn.commit()
        print(f"  status de aerodromos: FALHOU ({exc})")
        return 0

    conhecidos = {r[0] for r in conn.execute("SELECT icao FROM aerodromo")}
    linhas, sem_cadastro = [], 0
    for reg in dados:
        if not reg or not reg[0]:
            continue
        icao = str(reg[0]).strip().upper()
        def campo(i, padrao=None):
            return str(reg[i]).strip() if len(reg) > i and reg[i] is not None else padrao
        lat, lon = campo(2), campo(3)
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            lat = lon = None
        if icao not in conhecidos:
            sem_cadastro += 1
        linhas.append((icao, icao if icao in conhecidos else None, campo(1),
                       lat, lon, campo(4), campo(5), _agora()))

    conn.executemany(
        "INSERT INTO redemet_aerodromo_status (icao, aerodromo_icao, nome, lat, lon, "
        "cor, mensagem, colhido_em) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(icao) DO UPDATE SET aerodromo_icao=excluded.aerodromo_icao, "
        "nome=excluded.nome, lat=excluded.lat, lon=excluded.lon, cor=excluded.cor, "
        "mensagem=excluded.mensagem, colhido_em=excluded.colhido_em",
        linhas)
    dur = time.monotonic() - t0
    conn.execute(
        "INSERT INTO redemet_coleta (endpoint, parametros, status, registros, "
        "requisicoes, duracao_s) VALUES (?, ?, ?, ?, ?, ?)",
        ("/aerodromos/status", "pais=BRASIL", "ok", len(linhas), 1, dur))
    conn.commit()
    print(f"  status de aerodromos: {len(linhas)} localidades "
          f"({sem_cadastro} sem correspondência no cadastro da ANAC) em {dur:.1f}s")
    return len(linhas)


def _grava_mensagens(conn, tipo: str, registros: list[dict]) -> int:
    conhecidos = {r[0] for r in conn.execute("SELECT icao FROM aerodromo")}
    agora = _agora()
    linhas = []
    for r in registros:
        icao = str(r.get("id_localidade") or "").strip().upper()
        if not icao:
            continue
        linhas.append((
            tipo, icao, icao if icao in conhecidos else None,
            r.get("validade_inicial"), r.get("validade_final"),
            r.get("mens"), r.get("recebimento"), agora,
        ))
    if not linhas:
        return 0
    conn.executemany(
        "INSERT INTO redemet_mensagem (tipo, icao, aerodromo_icao, validade_inicial, "
        "validade_final, mensagem, recebimento, colhido_em) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(tipo, icao, validade_inicial, mensagem) DO UPDATE SET "
        "validade_final=excluded.validade_final, recebimento=excluded.recebimento, "
        "colhido_em=excluded.colhido_em",
        linhas)
    return len(linhas)


def carregar_mensagens(conn: sqlite3.Connection, cli: RedemetClient,
                       icaos: list[str], tipo: str) -> int:
    """METAR ou TAF, em lotes de `LOTE` localidades."""
    metodo = cli.metar if tipo == "METAR" else cli.taf
    data_ini, data_fim = _janela()
    total = 0
    for i in range(0, len(icaos), LOTE):
        lote = icaos[i:i + LOTE]
        t0 = time.monotonic()
        try:
            registros = metodo(lote, data_ini=data_ini, data_fim=data_fim)
        except RedemetErro as exc:
            conn.execute(
                "INSERT INTO redemet_coleta (endpoint, parametros, status, mensagem) "
                "VALUES (?, ?, ?, ?)",
                (f"/mensagens/{tipo.lower()}", ",".join(lote), "erro", str(exc)))
            conn.commit()
            print(f"  {tipo} {','.join(lote)}: FALHOU ({exc})")
            continue
        n = _grava_mensagens(conn, "METAR" if tipo == "METAR" else "TAF", registros)
        total += n
        dur = time.monotonic() - t0
        conn.execute(
            "INSERT INTO redemet_coleta (endpoint, parametros, status, registros, "
            "duracao_s) VALUES (?, ?, ?, ?, ?)",
            (f"/mensagens/{tipo.lower()}", ",".join(lote), "ok", n, dur))
        conn.commit()
        print(f"  {tipo} {len(lote):>3} localidades -> {n:>4} mensagens ({dur:.1f}s)")
    return total


def coletar(conn: sqlite3.Connection) -> dict:
    """Coleta completa: status do país + METAR e TAF das localidades escolhidas."""
    cli = RedemetClient(delay=DELAY_S)
    print(f"  limite: {MAX_AERODROMOS} localidades, intervalo {DELAY_S}s, janela {HORAS}h")

    # O status vem primeiro: é ele que diz quais localidades têm METAR ativo,
    # e portanto quais vale a pena consultar em seguida.
    n_status = carregar_status(conn, cli)
    icaos = escolher_icaos(conn)
    print(f"  localidades selecionadas: {len(icaos)}"
          + (f" ({', '.join(icaos[:6])}{'...' if len(icaos) > 6 else ''})" if icaos else ""))

    n_metar = carregar_mensagens(conn, cli, icaos, "METAR") if icaos else 0
    n_taf = carregar_mensagens(conn, cli, icaos, "TAF") if icaos else 0

    resumo = {"status": n_status, "metar": n_metar, "taf": n_taf,
              "localidades": len(icaos), "requisicoes": cli.requisicoes}
    print(f"  total: {n_status} status | {n_metar} METAR | {n_taf} TAF "
          f"| {cli.requisicoes} requisições")
    return resumo
