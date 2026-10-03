"""Monta as pastas de teste do botão de atualização.

Duas datas, para provar que o botão escolhe a mais recente: a pasta de 21/07
traz uma mudança que **não** deve ser aplicada; a de 30/09 traz a que deve.
Cada arquivo carrega as linhas reais do banco, alterando um campo só, para
que o relatório mostre exatos quantos registros mudaram.
"""

import csv
import json
import pathlib
import sqlite3

RAIZ = pathlib.Path(__file__).resolve().parent.parent
BANCO = RAIZ / "build" / "anac.db"
UPDATE = RAIZ / "update"

# O registro de teste precisa ser removido a cada execução, senão a segunda
# rodada acha que ele já existe e não prova a inserção.
import sqlite3 as _s
with _s.connect(BANCO) as _c:
    _c.execute("DELETE FROM pessoa WHERE documento = '99999000111'")
    _c.execute("UPDATE aerodromo SET situacao = 'Cadastrado' WHERE icao = 'SBGR'")
    _c.execute("UPDATE pessoa SET nome = 'BEECH AIRCRAFT' WHERE id = 1")

conn = sqlite3.connect(BANCO)
conn.row_factory = sqlite3.Row


def linha(tabela: str, chave: str, coluna: str) -> dict:
    """A linha real, com um campo trocado por um valor de teste."""
    where = "icao = ?" if tabela == "aerodromo" else "id = ?"
    d = dict(conn.execute(f"SELECT * FROM {tabela} WHERE {where}", (chave,)).fetchone())
    d[coluna] = "TESTE " + str(d[coluna])
    return d


# --- 20260721: mais antiga, não pode ser a escolhida -----------------------
antiga = UPDATE / "20260721"
antiga.mkdir(parents=True, exist_ok=True)
(antiga / "aerodromo.csv").write_text(
    "icao,nome,municipio,uf,situacao,tipo\n"
    "SBGR,NOME QUE NAO DEVE SER APLICADO,Guarulhos,SP,Cadastrado,PUBLICO\n",
    encoding="utf-8",
)
(antiga / "notas.txt").write_text("Pasta antiga, apenas para provar a escolha.\n", encoding="utf-8")

# --- 20260930: a mais recente, é esta que vale -----------------------------
nova = UPDATE / "20260930"
nova.mkdir(parents=True, exist_ok=True)

aero = linha("aerodromo", "SBGR", "situacao")
colunas = ["icao", "nome", "municipio", "uf", "situacao", "tipo"]
with (nova / "aerodromo.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=colunas)
    w.writeheader()
    w.writerow({k: aero[k] for k in colunas})

pessoa = linha("pessoa", "1", "nome")
with (nova / "pessoa.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(pessoa))
    w.writeheader()
    w.writerow(pessoa)

# Idempotência: um segundo arquivo com o **mesmo** valor já gravado não pode
# gerar diferença nova. Precisa repetir o valor de `aerodromo.csv`, senão os
# dois arquivos brigariam entre si.
(nova / "aerodromo_igual.csv").write_text(
    "icao,situacao\nSBGR,%s\n" % aero["situacao"], encoding="utf-8"
)
# Registro novo: chega só com `nome`, sem `natureza` nem `chave`, e o deduzido
# tem de virar cadastro em vez de erro de NOT NULL.
(nova / "pessoas.json").write_text(
    json.dumps(
        [
            # Já existe e traz o MESMO valor do `pessoa.csv`: o arquivo existe
            # para provar que uma reexecução não gera diferença nova.
            {"id": 1, "nome": pessoa["nome"]},
            {"documento": "99999000111", "nome": "EMPRESA DE TESTE DO UPDATE"},
        ],
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

# Ruído que o sistema deve ignorar em vez de adivinhar a tabela.
(nova / "relatorio.md").write_text("# Relatório\n\nnão é cadastro.\n", encoding="utf-8")

print("pastas criadas:")
for pasta in sorted(UPDATE.iterdir()):
    print(" ", pasta.name, "->", sorted(p.name for p in pasta.iterdir()))