"""Acesso ao SQLite: conexão, upserts idempotentes e helpers de log.

O ponto central é `upsert_pessoa`. Toda entidade do grafo — proprietário,
operador, fabricante — resolve para uma linha em `pessoa` através da chave de
dedup, de modo que a mesma empresa não vira duas linhas só porque apareceu em
arquivos diferentes. O `INSERT ... ON CONFLICT DO UPDATE` mantém o ETL
re-executável sem duplicar nada.
"""

from __future__ import annotations

import sqlite3

from common import (
    BUILD,
    DB_PATH,
    SCHEMA,
    chave_pessoa,
    classifica_documento,
    limpa_texto,
    normaliza_nome,
    normaliza_uf,
)


def conectar(criar: bool = True) -> sqlite3.Connection:
    if criar:
        BUILD.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def criar_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def aplicar_migracoes(conn: sqlite3.Connection) -> int:
    """Cria o que falta sem destruir o que existe (modo `--manter`).

    `schema.sql` continua sendo a fonte única do DDL, com os `DROP` para a carga
    cheia. Aqui eles saem e os `CREATE` viram `IF NOT EXISTS`, para que rodar o
    ETL de novo não apague 860 mil vínculos só porque uma tabela nova entrou.
    """
    import re
    sql = SCHEMA.read_text(encoding="utf-8")
    sql = re.sub(r"^\s*DROP TABLE IF EXISTS .*?;\s*$", "", sql, flags=re.M)
    sql = re.sub(r"\bCREATE TABLE (?!IF NOT EXISTS)", "CREATE TABLE IF NOT EXISTS ", sql)
    sql = re.sub(r"\bCREATE VIEW (?!IF NOT EXISTS)", "CREATE VIEW IF NOT EXISTS ", sql)
    sql = re.sub(r"\bCREATE INDEX (?!IF NOT EXISTS)", "CREATE INDEX IF NOT EXISTS ", sql)
    antes = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','index','view')"
    ).fetchone()[0]
    conn.executescript(sql)
    conn.commit()
    depois = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','index','view')"
    ).fetchone()[0]
    return depois - antes


def log_ingestao(conn, fonte: str, arquivo: str, linhas: int, status: str,
                 mensagem: str = "") -> None:
    conn.execute(
        "INSERT INTO ingestao_log (fonte, arquivo, linhas, status, mensagem) "
        "VALUES (?, ?, ?, ?, ?)",
        (fonte, arquivo, linhas, status, mensagem[:1000]),
    )


class PessoaRepo:
    """Cache de identidades em memória.

    A série tem ~450 mil participações e poucas dezenas de milhares de pessoas
    distintas. Resolver cada uma direto no banco a cada linha transformaria a
    carga em centenas de milhares de queries; o dicionário mantém o mapa
    chave -> id e só escreve no SQLite o que é novo.

    O id é atribuído na resolução, por contador, porque as linhas de
    `aeronave` e `participacao` já referenciam esses ids antes do flush.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._cache: dict[str, int] = {}
        self._pendentes: dict[str, tuple] = {}
        self._proximo = 1
        self.n_lidas = 0
        self.n_novas = 0

    def resolver(self, nome: str, documento_bruto=None, uf=None) -> int:
        """Retorna o id de `pessoa` para (nome, documento, uf), criando se preciso.

        `limpa_texto` entra aqui e não nos leitores: este é o único caminho por
        onde um nome vira linha de `pessoa`, então é o único lugar em que a
        limpeza precisa estar para valer para toda ingestão, inclusive as
        colunas que vierem de arquivos novos em `update/`.
        """
        nome = limpa_texto(nome) or ""
        natureza, documento, mascarado, invalido = classifica_documento(documento_bruto)
        uf = normaliza_uf(uf)
        uf_indisponivel = 1 if (uf is None and documento_bruto) else 0

        if not nome and not documento:
            nome = "DESCONHECIDO"

        chave = chave_pessoa(natureza, documento, nome, uf)
        self.n_lidas += 1

        if chave in self._cache:
            return self._cache[chave]
        if chave in self._pendentes:
            return self._pendentes[chave][0]

        self.n_novas += 1
        novo_id = self._proximo
        self._proximo += 1
        self._pendentes[chave] = (
            novo_id, natureza, chave, nome, documento, documento_bruto,
            mascarado, invalido, uf, uf_indisponivel,
        )
        self._cache[chave] = novo_id
        return novo_id

    def precarregar(self) -> int:
        """Hidrata o cache a partir do banco (modo `--manter`).

        Sem isso, uma recarga trataria todo mundo já ingerido como novo e
        colidiria com a UNIQUE de `chave`.
        """
        for chave, pid in self.conn.execute("SELECT chave, id FROM pessoa"):
            self._cache[chave] = pid
        self._proximo = (self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM pessoa").fetchone()[0]) + 1
        return len(self._cache)

    def flush(self) -> int:
        """Grava as identidades acumuladas. Retorna quantas foram inseridas."""
        if not self._pendentes:
            return 0
        # A ordem das colunas do INSERT e a ordem da tupla precisam bater. A
        # tupla em `_pendentes` começa pelo id, então id vem primeiro.
        linhas = [
            (pid, natureza, chave, nome, documento, bruto, mascarado, invalido, uf, uf_indisp)
            for pid, natureza, chave, nome, documento, bruto, mascarado, invalido, uf, uf_indisp
            in self._pendentes.values()
        ]
        self.conn.executemany(
            "INSERT INTO pessoa (id, natureza, chave, nome, documento, "
            "documento_bruto, documento_mascarado, documento_invalido, uf, "
            "uf_indisponivel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            linhas,
        )
        # Limpa o lote já gravado; o cache chave->id permanece, então uma
        # resolução posterior devolve o mesmo id sem recriar a linha.
        self._pendentes.clear()
        return len(linhas)


class CatalogoRepo:
    """Marcas, modelos e fabricantes, com a mesma política de dedup."""

    def __init__(self, conn: sqlite3.Connection, pessoas: PessoaRepo):
        self.conn = conn
        self.pessoas = pessoas
        self.marca_id: dict[str, int] = {}
        self.modelo_id: dict[tuple, int] = {}
        self.fabricante_id: dict[str, int] = {}
        self._marca_novas: dict[str, tuple] = {}
        self._modelo_novos: dict[tuple, tuple] = {}
        self._fabricante_novos: dict[str, tuple] = {}
        self._proximo_marca = 1
        self._proximo_modelo = 1
        self._proximo_fabricante = 1

    def marca(self, nome):
        nome = (nome or "").strip()
        if not nome:
            return None
        chave = normaliza_nome(nome)
        if chave in self.marca_id:
            return self.marca_id[chave]
        novo = self._proximo_marca
        self._proximo_marca += 1
        self._marca_novas[chave] = (novo, nome)
        self.marca_id[chave] = novo
        return novo

    def fabricante(self, nome, org_codigo=None, org_nabrev=None):
        """Resolve (ou cria) o fabricante.

        A identidade é a `pessoa`, não o código de organização: dois
        `org_codigo` diferentes podem apontar para o mesmo nome sem documento, e
        `pessoa` já os funde. Deduplicar por `org_codigo` criaria duas linhas de
        fabricante para a mesma pessoa e violaria a UNIQUE de `pessoa_id`.
        """
        nome = (nome or "").strip()
        if not nome and not org_codigo:
            return None
        # Fabricante é pessoa jurídica por definição do domínio.
        pessoa_id = self.pessoas.resolver(nome or f"Fabricante {org_codigo}", None, None)
        if pessoa_id in self.fabricante_id:
            return self.fabricante_id[pessoa_id]
        novo = self._proximo_fabricante
        self._proximo_fabricante += 1
        self._fabricante_novos[pessoa_id] = (novo, pessoa_id, org_codigo, org_nabrev)
        self.fabricante_id[pessoa_id] = novo
        return novo

    def modelo(self, cd_tipo, ds_modelo, marca_nome=None, fabricante_nome=None):
        if not ds_modelo and not cd_tipo:
            return None
        marca_id = self.marca(marca_nome) if marca_nome else None
        fab_id = self.fabricante(fabricante_nome) if fabricante_nome else None
        chave = ((cd_tipo or "").strip(), (ds_modelo or "").strip())
        if chave in self.modelo_id:
            return self.modelo_id[chave]
        novo = self._proximo_modelo
        self._proximo_modelo += 1
        self._modelo_novos[chave] = (novo, marca_id, cd_tipo, ds_modelo, fab_id)
        self.modelo_id[chave] = novo
        return novo

    def precarregar(self) -> None:
        """Hidrata marcas, modelos e fabricantes já gravados (modo `--manter`)."""
        for nome, mid in self.conn.execute("SELECT nome, id FROM marca"):
            self.marca_id[normaliza_nome(nome)] = mid
        for cd_tipo, ds_modelo, mid in self.conn.execute(
                "SELECT cd_tipo, ds_modelo, id FROM modelo"):
            self.modelo_id[((cd_tipo or "").strip(), (ds_modelo or "").strip())] = mid
        for pid, fid in self.conn.execute("SELECT pessoa_id, id FROM fabricante"):
            self.fabricante_id[pid] = fid
        self._proximo_marca = (self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM marca").fetchone()[0]) + 1
        self._proximo_modelo = (self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM modelo").fetchone()[0]) + 1
        self._proximo_fabricante = (self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM fabricante").fetchone()[0]) + 1

    def flush(self) -> None:
        if self._marca_novas:
            self.conn.executemany(
                "INSERT INTO marca (id, nome) VALUES (?, ?)",
                list(self._marca_novas.values()),
            )
            self._marca_novas.clear()
        if self._fabricante_novos:
            self.conn.executemany(
                "INSERT INTO fabricante (id, pessoa_id, org_codigo, org_nabrev) "
                "VALUES (?, ?, ?, ?)",
                list(self._fabricante_novos.values()),
            )
            self._fabricante_novos.clear()
        if self._modelo_novos:
            self.conn.executemany(
                "INSERT INTO modelo (id, marca_id, cd_tipo, ds_modelo, fabricante_id) "
                "VALUES (?, ?, ?, ?, ?)",
                list(self._modelo_novos.values()),
            )
            self._modelo_novos.clear()
