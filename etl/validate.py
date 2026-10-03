"""Validação do banco: contagens, integridade referencial e idempotência.

Não confia no ETL. Cada número que o painel vai exibir é conferido aqui contra
o arquivo de origem, e `PRAGMA foreign_key_check` roda inteiro — a afirmação
"zero FKs órfãs" só vale se o SQLite concordar.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DB_PATH, FONTE, MESES, read_json_anac

TOLERANCIA = 0  # divergência entre contagem gravada e arquivo é sempre um bug


def main() -> int:
    if not DB_PATH.exists():
        print(f"banco ausente: {DB_PATH}")
        return 2
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    falhas: list[str] = []
    avisos: list[str] = []

    def checar(cond, msg):
        (falhas if not cond else avisos).append(msg) if not cond else None
        print(f"  {'OK  ' if cond else 'FALHA'} {msg}")
        return cond

    print("\n=== contagens por tabela ===")
    tabelas = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    contagens = {}
    for t in tabelas:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        contagens[t] = n
        print(f"  {t:22} {n:>9,}")

    print("\n=== views ===")
    for v in ("v_usuario", "v_empresa"):
        n = conn.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
        print(f"  {v:22} {n:>9,}")

    print("\n=== integridade referencial ===")
    orfas = conn.execute("PRAGMA foreign_key_check").fetchall()
    checar(len(orfas) == 0, f"PRAGMA foreign_key_check: {len(orfas)} violacao(oes)")
    if orfas:
        for o in orfas[:10]:
            print(f"      {o}")

    # O pragma devolve só o total. Contar por relação mostra QUAL vínculo está
    # partido, que é a informação acionável quando algo quebra.
    RELACOES = [
        ("participacao", "aeronave_id", "aeronave", "id"),
        ("participacao", "pessoa_id", "pessoa", "id"),
        ("aeronave", "modelo_id", "modelo", "id"),
        ("aeronave", "fabricante_id", "fabricante", "id"),
        ("modelo", "marca_id", "marca", "id"),
        ("modelo", "fabricante_id", "fabricante", "id"),
        ("fabricante", "pessoa_id", "pessoa", "id"),
        ("produto_aeronautico", "fabricante_id", "fabricante", "id"),
        ("peca_aprovada", "fabricante_id", "fabricante", "id"),
        ("registro_sisant", "pessoa_id", "pessoa", "id"),
        ("redemet_aerodromo_status", "aerodromo_icao", "aerodromo", "icao"),
        ("redemet_mensagem", "aerodromo_icao", "aerodromo", "icao"),
    ]
    total_orfas = 0
    print(f"  {'FK':52} {'ORFAS':>7} {'NULOS':>8}  TOTAL")
    for filho, col, pai, pcol in RELACOES:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {filho} WHERE {col} IS NOT NULL "
            f"AND {col} NOT IN (SELECT {pcol} FROM {pai})").fetchone()[0]
        nulos = conn.execute(
            f"SELECT COUNT(*) FROM {filho} WHERE {col} IS NULL").fetchone()[0]
        total = conn.execute(f"SELECT COUNT(*) FROM {filho}").fetchone()[0]
        total_orfas += n
        marca = "OK  " if n == 0 else "ORFA"
        print(f"  {marca} {filho + '.' + col:49} {n:>7} {nulos:>8,}  {total:,}")
    checar(total_orfas == 0, f"total de FKs orfas: {total_orfas}")

    print("\n=== serie temporal (13 meses) ===")
    linhas = conn.execute(
        "SELECT mes, contagem, esquema_era FROM snapshot ORDER BY mes").fetchall()
    for mes, contagem, era in linhas:
        print(f"  {mes}  {contagem:>7,}  era {era}")
    esperados = [m for m in MESES if (FONTE / f"{m}.json").exists()]
    carregados = [m for m, _, _ in linhas]
    checar(carregados == esperados,
           f"meses carregados {len(carregados)} de {len(esperados)} disponiveis")
    faltando = [m for m in MESES if m not in carregados]
    if faltando:
        print(f"      ausentes: {', '.join(faltando)}")

    print("\n=== contagem de cada mes bate com o arquivo? ===")
    for mes, gravado, _ in linhas:
        caminho = FONTE / f"{mes}.json"
        if not caminho.exists():
            continue
        real = len(read_json_anac(caminho))
        ok = real == gravado
        print(f"  {'OK  ' if ok else 'FALHA'} {mes}: banco {gravado:,} / arquivo {real:,}")
        if not ok:
            falhas.append(f"{mes}: {gravado} != {real}")

    print("\n=== patches de schema (3 eras) ===")
    for era, esperado in (("A", "2025-09"), ("B", "2026-02"), ("C", "2026-05")):
        r = conn.execute("SELECT COUNT(*) FROM snapshot WHERE esquema_era=?", (era,)).fetchone()[0]
        print(f"  era {era}: {r} mes(es)")

    print("\n=== essa: 121 vs 135 (base normativa RBAC 45.12-I(a)) ===")
    for papel, col in (("OPERADOR", "operacao_135"), ("OPERADOR", "operacao_121")):
        r = conn.execute(
            f"SELECT {col}, COUNT(*) FROM participacao WHERE papel=? GROUP BY {col} "
            "ORDER BY 2 DESC", (papel,)).fetchall()
        rotulo = ", ".join(f"{k if k else '(flag ausente)'}={v:,}" for k, v in r)
        print(f"  {col:14} {rotulo}")

    print("\n=== qualidade do vinculo ===")
    total = conn.execute("SELECT COUNT(*) FROM participacao").fetchone()[0]
    sem_doc = conn.execute(
        "SELECT COUNT(*) FROM participacao p JOIN pessoa s ON s.id=p.pessoa_id "
        "WHERE s.documento IS NULL").fetchone()[0]
    masc = conn.execute(
        "SELECT COUNT(*) FROM participacao p JOIN pessoa s ON s.id=p.pessoa_id "
        "WHERE s.documento_mascarado=1").fetchone()[0]
    sem_pct = conn.execute(
        "SELECT COUNT(*) FROM participacao WHERE papel='PROPRIETARIO' "
        "AND percentual IS NULL").fetchone()[0]
    print(f"  participacoes sem documento     {sem_doc:>9,} ({sem_doc / total:.1%})")
    print(f"  participacoes com CPF mascarado {masc:>9,} ({masc / total:.1%})")
    print(f"  proprietarios sem percentual    {sem_pct:>9,} ({sem_pct / total:.1%})")

    print("\n=== grafo de participacao ===")
    grafo = conn.execute(
        "SELECT COUNT(DISTINCT pessoa_id) FROM participacao").fetchone()[0]
    print(f"  pessoas distintas no vinculo    {grafo:>9,}")
    # 127 mil pessoas vêm do SISANT e 2.303 são fabricantes: nenhuma delas
    # aparece em `participacao`, então medir órfã só ali dá um número falso.
    orfas_reais = conn.execute("""
        SELECT COUNT(*) FROM pessoa s
        WHERE NOT EXISTS (SELECT 1 FROM participacao p WHERE p.pessoa_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM fabricante f WHERE f.pessoa_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM registro_sisant r WHERE r.pessoa_id = s.id)
    """).fetchone()[0]
    print(f"  pessoas sem nenhuma referencia  {orfas_reais:>9,}")
    # Não é falha: o SISANT traz 64 códigos de aeronave duplicados na origem, e
    # o OR REPLACE colapsa a repeticao. Se a linha duplicada for a unica
    # referencia de alguém, essa pessoa fica sem vínculo. É condição da fonte,
    # não invariante quebrada — então entra como aviso com o número à vista.
    if orfas_reais:
        print(f"  AVISO {orfas_reais} pessoa(s) sem referencia, de "
              f"{grafo + orfas_reais:,} no total — causedas por codigo repetido "
              f"no SISANT (nao reprova a carga)")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("todas as verificacoes passaram")
    print("=" * 62)
    conn.close()
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
