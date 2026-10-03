"""Carga dos 13 snapshots mensais do Registro Aeronáutico Brasileiro.

Cada mês vira uma versão de `participacao`, o que preserva a possessão ao longo
do tempo em vez de sobrescrever o passado com o estado mais recente. A tabela
`aeronave` guarda o estado do snapshot mais recente e `snapshot` registra a
contagem de cada mês para a série temporal.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from common import FONTE, MESES, read_delimited, read_json_anac
from db import CatalogoRepo, PessoaRepo, log_ingestao
from rab_eras import (
    chave_natural,
    detecta_era,
    detectar_passo_era_b,
    extrai_operadores,
    extrai_proprietarios,
    normaliza_aeronave,
)

COLS_AERONAVE = [
    "id", "chave_natural", "nr_cert_matricula", "nr_serie", "marca", "cd_tipo",
    "ds_modelo", "nm_fabricante", "cd_classe", "nr_pmd", "cd_tipo_icao",
    "nr_tripulacao_min", "nr_passageiros_max", "nr_assentos",
    "nr_ano_fabricacao", "dt_validade_cva", "dt_validade_ca",
    "dt_cancelamento", "ds_motivo_cancelamento", "cd_interdicao", "ds_gravame",
    "dt_matricula", "tp_motor", "qt_motor", "tp_pouso", "tp_ca",
    "cd_proposito_cave", "cf_operacional", "ds_categoria_homologacao",
    "tp_operacao", "dt_venda", "ds_moeda", "nr_preco_venda",
    "modelo_id", "fabricante_id", "snapshot_mes",
]

# `aeronave` guarda o estado do snapshot mais recente: cada mês novo sobrescreve
# a mesma linha pelo mesmo id. Por isso é UPDATE, não INSERT — um REPLACE
# apagaria a linha e quebraria a FK de `participacao`, que é versionada por mês.
_ATUALIZA_AERONAVE = (
    f"INSERT INTO aeronave ({', '.join(COLS_AERONAVE)}) "
    f"VALUES ({', '.join('?' * len(COLS_AERONAVE))}) "
    f"ON CONFLICT(id) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in COLS_AERONAVE if c != "id")
)

COLS_PART = [
    "aeronave_id", "pessoa_id", "papel", "percentual", "uf", "snapshot_mes",
    "operacao_121", "operacao_135", "transp_reg_121", "transp_reg_135",
    "aut_pmac_121", "aut_pmac_135", "sae", "authistrut",
]


def _para_tupla(aer: dict, modelo_id, fab_id, mes: str, aero_id: int) -> tuple:
    linha = [aero_id] + [aer.get(c) for c in COLS_AERONAVE[1:33]]
    linha += [modelo_id, fab_id, mes]
    return tuple(linha)


def _insere_parte(pessoa_id, aero_id, papel, item, mes) -> tuple:
    return (
        aero_id, pessoa_id, papel, item.get("percentual"), item.get("uf"), mes,
        item.get("operacao_121"), item.get("operacao_135"),
        item.get("transp_reg_121"), item.get("transp_reg_135"),
        item.get("aut_pmac_121"), item.get("aut_pmac_135"),
        item.get("sae"), item.get("authistrut"),
    )


def carregar_rab(conn: sqlite3.Connection, pessoas: PessoaRepo, cat: CatalogoRepo,
                 pular: set[str] | None = None) -> dict:
    """Carrega os 13 meses. Retorna um resumo por mês para o painel de qualidade.

    `pular` recebe os meses já presentes em `snapshot` (modo `--manter`) e faz
    a recarga ser realmente incremental: sem isso, reprocessar um mês duplicaria
    a aeronave, porque o id é surrogateiro e recomeçaria do mesmo ponto.
    """
    pular = pular or set()
    resumo = {}
    aero_id_por_chave: dict[str, int] = {}
    proximo_aero = 1
    # Exposto para o carregador de CSV, que precisa escrever nos mesmos ids.

    for mes in MESES:
        caminho = FONTE / f"{mes}.json"
        if mes in pular:
            log_ingestao(conn, "RAB", caminho.name, 0, "ignorado", "ja carregado")
            row = conn.execute(
                "SELECT contagem, esquema_era FROM snapshot WHERE mes=?", (mes,)).fetchone()
            resumo[mes] = {"status": "ja_carregado", "contagem": row[0], "era": row[1]}
            conn.commit()
            print(f"  {mes}  {row[0]:>6} aeronaves  era {row[1]}  (ja carregado, ignorado)")
            continue
        if not caminho.exists():
            log_ingestao(conn, "RAB", caminho.name, 0, "ausente",
                         "snapshot nao baixado")
            resumo[mes] = {"status": "ausente"}
            continue

        try:
            dados = read_json_anac(caminho)
        except Exception as exc:
            log_ingestao(conn, "RAB", caminho.name, 0, "erro", str(exc))
            resumo[mes] = {"status": "erro", "mensagem": str(exc)}
            continue

        era = detecta_era(dados[0]) if dados else "?"
        # A era B tem dois layouts (3 ou 4 campos por proprietário). O passo é
        # decidido no mês inteiro, porque um valor com 12 partes é ambíguo
        # entre 3x4 e 4x3 e a resposta depende do formato daquele mês.
        passo_b = 3
        if era == "B":
            passo_b = detectar_passo_era_b(
                [r["PROPRIETARIOSARRAY"] for r in dados
                 if r.get("PROPRIETARIOSARRAY")])
        linhas_aero, linhas_part = [], []
        n_props, n_ops = 0, 0

        for rec in dados:
            aer = normaliza_aeronave(rec, era)
            natural = chave_natural(aer)
            aer["chave_natural"] = natural
            modelo_id = cat.modelo(aer.get("cd_tipo"), aer.get("ds_modelo"),
                                   aer.get("marca"), aer.get("nm_fabricante"))
            fab_id = cat.fabricante(aer.get("nm_fabricante"))

            aero_id = aero_id_por_chave.get(natural)
            if aero_id is None:
                aero_id = proximo_aero
                proximo_aero += 1
                aero_id_por_chave[natural] = aero_id
            linhas_aero.append(_para_tupla(aer, modelo_id, fab_id, mes, aero_id))

            for item in extrai_proprietarios(rec, era, passo_b):
                pid = pessoas.resolver(item["nome"], item.get("documento"), item.get("uf"))
                linhas_part.append(_insere_parte(pid, aero_id, "PROPRIETARIO", item, mes))
                n_props += 1
            for item in extrai_operadores(rec, era):
                pid = pessoas.resolver(item["nome"], item.get("documento"), item.get("uf"))
                linhas_part.append(_insere_parte(pid, aero_id, "OPERADOR", item, mes))
                n_ops += 1

        # As aircraft rows referenciam `modelo` e `fabricante`, que ainda estão
        # em cache. Persiste o catálogo antes de inserir, senão a FK falha.
        pessoas.flush()
        cat.flush()
        conn.executemany(_ATUALIZA_AERONAVE, linhas_aero)
        # OR IGNORE mantém a idempotência: reexecutar o ETL não duplica a
        # mesma relação (aeronave, pessoa, papel, mês), que tem chave única.
        conn.executemany(
            f"INSERT OR IGNORE INTO participacao ({', '.join(COLS_PART)}) "
            f"VALUES ({', '.join('?' * len(COLS_PART))})",
            linhas_part,
        )
        conn.execute(
            "INSERT OR REPLACE INTO snapshot (mes, contagem, esquema_era, arquivo_origem) "
            "VALUES (?, ?, ?, ?)",
            (mes, len(dados), era, caminho.name),
        )
        log_ingestao(conn, "RAB", caminho.name, len(dados), "ok",
                     f"era={era} passo_b={passo_b} participacoes={len(linhas_part)}")
        resumo[mes] = {
            "status": "ok", "contagem": len(dados), "era": era,
            "passo_b": passo_b, "proprietarios": n_props, "operadores": n_ops,
        }
        conn.commit()
        extra = f" passo {passo_b}" if era == "B" else ""
        print(f"  {mes}  {len(dados):>6} aeronaves  era {era}{extra}  "
              f"{n_props:>6} proprietarios  {n_ops:>6} operadores")

    return resumo, aero_id_por_chave, proximo_aero


def carregar_rab_csv_atual(conn: sqlite3.Connection, pessoas: PessoaRepo,
                           cat: CatalogoRepo, resumo: dict, pular: bool = False,
                           aero_id_por_chave: dict | None = None,
                           proximo_aero: int = 1) -> None:
    """Carrega `dados_aeronaves.csv` (mês corrente) com os 33 campos completos.

    Os snapshots JSON de 2026-09 trazem poucos campos; o CSV tem o registro
    inteiro. Carregá-lo sobre o mesmo mês completa as colunas que o JSON omite
    sem duplicar aeronave nem participação.
    """
    caminho = FONTE / "dados_aeronaves.csv"
    if not caminho.exists() or pular:
        return
    cabecalho, linhas = read_delimited(caminho)
    idx = {c: i for i, c in enumerate(cabecalho)}
    mes_atual = max(m for m in MESES if m in resumo and resumo[m].get("status") == "ok")

    def col(reg, nome):
        i = idx.get(nome)
        return reg[i] if i is not None and i < len(reg) else None

    import json as _json

    # O CSV completa o estado das mesmas aeronaves que o JSON criou. Precisa do
    # mesmo mapa de ids: com um mapa próprio, criaria linhas duplicadas e os
    # vínculos de `participacao` ficariam apontando para a aeronave errada.
    cache = aero_id_por_chave if aero_id_por_chave is not None else {}
    aero_rows, part_rows = [], []

    for reg in linhas:
        marcas = (col(reg, "MARCAS") or "").strip()
        cert = (col(reg, "NR_CERT_MATRICULA") or "").strip()
        serie = (col(reg, "NR_SERIE") or "").strip()
        natural = f"{marcas}|{cert}|{serie}"
        aero_id = cache.get(natural)
        novo = aero_id is None
        if novo:
            aero_id = proximo_aero
            proximo_aero += 1
            cache[natural] = aero_id
        # O CSV traz os 33 campos completos, então sempre atualiza o estado da
        # aeronave — mesmo para as que o JSON já criou.
        modelo_id = cat.modelo(col(reg, "CD_TIPO"), col(reg, "DS_MODELO"),
                               marcas, col(reg, "NM_FABRICANTE"))
        aero_rows.append((
                aero_id, natural, cert or None, serie or None, marcas or None,
                col(reg, "CD_TIPO"), col(reg, "DS_MODELO"), col(reg, "NM_FABRICANTE"),
                col(reg, "CD_CLS"), col(reg, "NR_PMD"), col(reg, "CD_TIPO_ICAO"),
                col(reg, "NR_TRIPULACAO_MIN"), col(reg, "NR_PASSAGEIROS_MAX"),
                col(reg, "NR_ASSENTOS"), col(reg, "NR_ANO_FABRICACAO"),
                col(reg, "DT_VALIDADE_CVA"), col(reg, "DT_VALIDADE_CA"),
                col(reg, "DT_CANC"), col(reg, "DS_MOTIVO_CANC"), col(reg, "CD_INTERDICAO"),
                col(reg, "DS_GRAVAME"), col(reg, "DT_MATRICULA"), col(reg, "TP_MOTOR"),
                col(reg, "QT_MOTOR"), col(reg, "TP_POUSO"), col(reg, "TP_CA"),
                col(reg, "CD_PROPOSITO_CAVE"), col(reg, "CF_OPERACIONAL"),
                col(reg, "DS_CATEGORIA_HOMOLOGACAO"), col(reg, "TP_OPERACAO"),
                col(reg, "DT_VENDA"), col(reg, "DS_MOEDA"), col(reg, "NR_PRECO_VENDA"),
                modelo_id, cat.fabricante(col(reg, "NM_FABRICANTE")), mes_atual,
            ))

        for papel, campo in (("PROPRIETARIO", "PROPRIETARIOS"), ("OPERADOR", "OPERADORES")):
            bruto = col(reg, campo)
            if not bruto or bruto.strip() in ("", "[]"):
                continue
            try:
                itens = _json.loads(bruto)
            except _json.JSONDecodeError:
                continue
            if isinstance(itens, dict):
                itens = [itens]
            for it in itens:
                pid = pessoas.resolver(it.get("NOME"), it.get("DOCUMENTO"), it.get("UF"))
                try:
                    pct = float(str(it.get("PERCENTUAL")).replace(",", "."))
                except (TypeError, ValueError):
                    pct = None
                part_rows.append((
                    aero_id, pid, papel, pct, it.get("UF"), mes_atual,
                    it.get("OPERACAO121"), it.get("OPERACAO135"),
                    it.get("TRANSPREGULAR121"), it.get("TRANSPREGULAR135"),
                    it.get("AUTORIZACAOPMAC121"), it.get("AUTORIZACAOPMAC135"),
                    it.get("SAE"), it.get("AUTHISTRUT"),
                ))

    if aero_rows:
        pessoas.flush()
        cat.flush()
        conn.executemany(_ATUALIZA_AERONAVE, aero_rows)
    if part_rows:
        conn.executemany(
            f"INSERT OR IGNORE INTO participacao ({', '.join(COLS_PART)}) "
            f"VALUES ({', '.join('?' * len(COLS_PART))})",
            part_rows,
        )
    log_ingestao(conn, "RAB", caminho.name, len(aero_rows), "ok",
                 f"csv mes={mes_atual} completando colunas")
    print(f"  dados_aeronaves.csv  {len(aero_rows)} aeronaves (campos completos, mes {mes_atual})")
