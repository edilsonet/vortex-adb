"""Orquestrador do ETL: monta o banco do zero e depois só incrementally.

    python etl/run.py            # reconstrói o banco
    python etl/run.py --manter   # não apaga o banco existente (recarga)
    python etl/run.py --redemet  # também coleta a API-REDEMET

A coleta da REDEMET é opt-in: a API da DECEA impõe limite de uso, e o ETL
principal não deve tocar na rede sem você pedir.

A ordem importa: `pessoa`, `marca`, `modelo` e `fabricante` são gravados
primeiro, porque as tabelas que os referenciam são inseridas depois.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import (CatalogoRepo, PessoaRepo, aplicar_migracoes, conectar,
               criar_schema, log_ingestao)
from ingest_aux import (
    carregar_aerodromos,
    carregar_org_producao,
    carregar_produtos_e_pecas,
    carregar_sisant,
)
from ingest_rab import carregar_rab, carregar_rab_csv_atual
from common import DB_PATH


def main() -> int:
    manter = "--manter" in sys.argv
    usar_redemet = "--redemet" in sys.argv
    t0 = time.time()

    if manter and DB_PATH.exists():
        conn = conectar(criar=False)
        novos = aplicar_migracoes(conn)
        print(f"modo --manter: banco existente preservado"
              + (f" | {novos} objeto(s) de schema criado(s)" if novos else ""))
    else:
        if DB_PATH.exists():
            DB_PATH.unlink()
        conn = conectar()
        criar_schema(conn)
        print("schema criado")

    pessoas = PessoaRepo(conn)
    cat = CatalogoRepo(conn, pessoas)

    print("\n[1/5] Registro Aeronáutico Brasileiro")
    if manter:
        # Recarga incremental: mês já presente em `snapshot` não é reprocessado.
        ja_carregados = {r[0] for r in conn.execute("SELECT mes FROM snapshot")}
        if ja_carregados:
            print(f"  {len(ja_carregados)} mes(es) ja carregados; recarga incremental")
        pessoas._proximo = (conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM pessoa").fetchone()[0]) + 1
        pessoas.precarregar()
        cat.precarregar()
    else:
        ja_carregados = set()
    resumo, aero_ids, proximo = carregar_rab(conn, pessoas, cat, ja_carregados)
    carregar_rab_csv_atual(conn, pessoas, cat, resumo, pular=bool(ja_carregados),
                           aero_id_por_chave=aero_ids, proximo_aero=proximo)

    print("\n[2/5] Identidades, marcas, modelos e fabricantes")
    pessoas.flush()
    cat.flush()
    conn.commit()
    n_pessoa = conn.execute("SELECT COUNT(*) FROM pessoa").fetchone()[0]
    n_marca = conn.execute("SELECT COUNT(*) FROM marca").fetchone()[0]
    n_modelo = conn.execute("SELECT COUNT(*) FROM modelo").fetchone()[0]
    n_fab = conn.execute("SELECT COUNT(*) FROM fabricante").fetchone()[0]
    print(f"  {n_pessoa} pessoas | {n_marca} marcas | "
          f"{n_modelo} modelos | {n_fab} fabricantes")

    print("\n[3/5] SISANT (drones e aeronaves de pequeno porte)")
    carregar_sisant(conn, pessoas)
    pessoas.flush()
    cat.flush()
    conn.commit()

    print("\n[4/5] Aeródromos")
    carregar_aerodromos(conn)
    conn.commit()

    print("\n[5/5] Produtos aeronáuticos, peças e organizações de produção")
    carregar_produtos_e_pecas(conn, cat, pessoas)
    carregar_org_producao(conn)
    pessoas.flush()
    cat.flush()
    conn.commit()

    log_ingestao(conn, "ETL", "run.py", 0, "ok", f"concluido em {time.time() - t0:.1f}s")

    if usar_redemet:
        print("\n[6/6] API-REDEMET (DECEA)")
        from ingest_redemet import coletar
        try:
            resumo_redemet = coletar(conn)
            log_ingestao(conn, "REDEMET", "api", resumo_redemet["requisicoes"],
                         "ok", str(resumo_redemet))
        except Exception as exc:
            # Falha da REDEMET não pode derrubar o ETL dos dados abertos.
            log_ingestao(conn, "REDEMET", "api", 0, "erro", str(exc))
            print(f"  REDEMET indisponível: {exc}")
    conn.commit()

    print("\n" + "=" * 62)
    total = conn.execute("SELECT COUNT(*) FROM aeronave").fetchone()[0]
    parts = conn.execute("SELECT COUNT(*) FROM participacao").fetchone()[0]
    print(f"concluido em {time.time() - t0:.1f}s | {total} aeronaves | {parts} participacoes")
    print(f"banco: {DB_PATH}")
    print("=" * 62)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
