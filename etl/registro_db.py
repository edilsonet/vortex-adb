"""Acesso à camada editável do registro.

Separa o dado que vem da ANAC (recarregável, em `schema/schema.sql`) do dado
que o operador curou (permanente, em `schema/registro.sql`).

O backfill roda uma vez e é idempotente: percorre os dois únicos arquivos da
pasta que de fato trazem dados societários e de contato, e grava em
`pessoa_extra` com `origem = 'ANAC'`. Tudo o mais fica `NULL`, que a interface
mostra como "não consta na fonte" — nunca como zero e nunca preenchido por
dedução.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "registro.sql"
DADOS = Path(r"C:\Users\edils\dados-anac")

# `Organizacoes de Producao.json` traz razão social, nome fantasia, CNPJ,
# endereço, CEP, cidade, UF, país e home page para 28 organizações.
ARQ_ORG_PRODUCAO = "Organizacoes de Producao.json"
# `EmpresasBrasileirasCertificadas.json` traz o site de 2 empresas, e só o site.
ARQ_CERTIFICADAS = "EmpresasBrasileirasCertificadas.json"


def _reparar_json(texto: str) -> str:
    """O escape corrompido da ANAC: `/""` onde deveria ser `\"`."""
    return texto.replace('/\\"\\"', '\\"').replace('/""', '"')


def _so_digitos(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _carregar(nome: str):
    caminho = DADOS / nome
    if not caminho.exists():
        return []
    bruto = caminho.read_text(encoding="utf-8-sig", errors="replace")
    return json.loads(_reparar_json(bruto))


def _arruma_site(bruto: str | None) -> str | None:
    """Corrige o esquema duplicado que vem na fonte.

    A ANAC publica `http://https://www.amquimica.com.br/` em vez de
    `https://www.amquimica.com.br/`. A string original fica preservada em
    `pessoa_extra.fonte_ref`; o que se corrige é a URL, que é o campo que
    alguém vai clicar.
    """
    s = (bruto or "").strip()
    if not s:
        return None
    s = re.sub(r"^https?://https?://", lambda m: m.group(0)[len("http://"):]
               if m.group(0).startswith("http://https") else m.group(0)[len("https://"):], s)
    if not s.lower().startswith(("http://", "https://")):
        s = "https://" + s
    return s


def aplicar_schema(conn: sqlite3.Connection) -> None:
    """Cria a camada editável. Idempotente e sem `DROP`."""
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _chave_pj(cnpj: str) -> str:
    return f"PJ:{_so_digitos(cnpj)}"


def garantir_pessoa(conn, *, cnpj: str | None = None, nome: str | None = None,
                    uf: str | None = None) -> int | None:
    """Devolve o id de `pessoa`, criando o registro se a empresa ainda não existir.

    18 das 28 organizações de produção não aparecem como proprietário nem
    operador em nenhum snapshot, então o ETL nunca as criou em `pessoa`. Elas
    são empresas reais, com CNPJ e razão social vindos da ANAC, e o cadastro
    precisa poder mostrá-las. A identidade é a `chave`, que é estável entre
    recargas.
    """
    dig = _so_digitos(cnpj)
    if dig:
        r = conn.execute("SELECT id FROM pessoa WHERE chave = ?",
                         (_chave_pj(cnpj),)).fetchone()
        if r:
            return r[0]
        r = conn.execute("SELECT id FROM pessoa WHERE documento = ?", (dig,)).fetchone()
        if r:
            return r[0]
    if not nome:
        return None
    chave = f"NOME:{_normaliza(nome)}|{uf or ''}"
    r = conn.execute("SELECT id FROM pessoa WHERE chave = ?", (chave,)).fetchone()
    if r:
        return r[0]
    novo = conn.execute(
        "INSERT INTO pessoa (natureza, chave, nome, documento, uf) VALUES (?, ?, ?, ?, ?)",
        ("JURIDICA", chave, nome, dig or None, uf)).lastrowid
    return novo


def _pessoa_por_cnpj(conn, cnpj: str | None) -> int | None:
    dig = _so_digitos(cnpj)
    if not dig:
        return None
    r = conn.execute("SELECT id FROM pessoa WHERE documento = ?", (dig,)).fetchone()
    return r[0] if r else None


def _pessoa_por_nome(conn, nome: str | None) -> int | None:
    """A entrada mais Busy: o mesmo org aparece em `pessoa` sob grafias distintas."""
    if not nome:
        return None
    alvo = _normaliza(nome)
    melhor = None
    for pid, nome_pessoa in conn.execute(
            "SELECT id, nome FROM pessoa WHERE natureza = 'JURIDICA'"):
        if _normaliza(nome_pessoa) == alvo:
            return pid
        if melhor is None and alvo and alvo in _normaliza(nome_pessoa):
            melhor = pid
    return melhor


def _normaliza(s: str | None) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", t).strip().upper()


def _upsert_extra(conn, pessoa_id: int, campos: dict, fonte_ref: str,
                  somente_anac: bool = True) -> bool:
    """Grava em `pessoa_extra` preservando o que já foi digitado à mão.

    `somente_anac=True` faz o backfill não sobrescrever edição manual: se o
    campo já tem `origem = 'MANUAL'`, o valor do operador é que fica.
    """
    if pessoa_id is None:
        return False
    chave = conn.execute("SELECT chave FROM pessoa WHERE id = ?",
                         (pessoa_id,)).fetchone()
    campos = {"chave": chave[0] if chave else None, **campos}
    atual = conn.execute(
        "SELECT origem_societario, origem_contato FROM pessoa_extra "
        "WHERE pessoa_id = ? OR chave = ?", (pessoa_id, campos["chave"])).fetchone()
    if atual is not None:
        # Recarga do ETL renumerou `pessoa.id`: reanexar pelo `chave` preserva
        # o que o operador digitou.
        vinculo = conn.execute("SELECT pessoa_id FROM pessoa_extra WHERE chave = ?",
                               (campos["chave"],)).fetchone()
        if vinculo and vinculo[0] != pessoa_id:
            conn.execute("DELETE FROM pessoa_extra WHERE pessoa_id = ?", (vinculo[0],))
    if atual is None:
        cols = ", ".join(campos)
        marcas = ", ".join("?" for _ in campos)
        conn.execute(f"INSERT INTO pessoa_extra (pessoa_id, {cols}) VALUES (?, {marcas})",
                     (pessoa_id, *campos.values()))
        return True
    if not somente_anac:
        return False
    origem_societ = (atual[0] if atual[0] else "MANUAL")
    updates, valores = [], []
    for campo, valor in campos.items():
        if valor is None:
            continue
        if campo in _SOCIETARIOS and origem_societ == "MANUAL":
            continue          # não mexe no que o operador digitou
        updates.append(f"{campo} = ?")
        valores.append(valor)
    if updates:
        conn.execute(f"UPDATE pessoa_extra SET {', '.join(updates)} WHERE pessoa_id = ?",
                     (*valores, pessoa_id))
    return True


_SOCIETARIOS = {
    "razao_social", "nome_fantasia", "end_logradouro", "end_numero",
    "end_complemento", "end_bairro", "end_cep", "end_municipio", "end_uf",
    "end_pais", "site",
}


def _separa_endereco(bruto: str | None, complemento: str | None) -> dict:
    """A fonte traz o endereço numa string só: 'Rua X n 01, Vila Buriti'.

    Separar número de bairro por regex seria adivinhação, então o texto vai
    inteiro para `end_logradouro` e `end_complemento` fica com o complemento
    declarativo. A coluna `end_numero` e `end_bairro` existem para quem
    quiser completar à mão, e a interface deixa claro que estão vazias.
    """
    return {
        "end_logradouro": (bruto or None),
        "end_complemento": (complemento or None),
    }


def backfill(conn) -> dict:
    """Carrega da ANAC os únicos dados de contato que existem na pasta.

    Idempotente, e devolve um resumo para o servidor imprimir no arranque.
    """
    aplicar_schema(conn)
    resumo = {"org_producao": 0, "certificadas": 0,
              "orgs_sem_pessoa": [], "certificadas_sem_fabricante": []}

    for linha in _carregar(ARQ_ORG_PRODUCAO):
        cnpj = linha.get("CNPJ")
        pid = (garantir_pessoa(conn, cnpj=cnpj, nome=linha.get("RazaoSocial"),
                        uf=linha.get("UF"))
               or _pessoa_por_nome(conn, linha.get("RazaoSocial")))
        if pid is None:
            resumo["orgs_sem_pessoa"].append(
                {"razao_social": linha.get("RazaoSocial"), "cnpj": cnpj})
            continue
        site = _arruma_site(linha.get("HomePage"))
        campos = {
            "razao_social": linha.get("RazaoSocial"),
            "nome_fantasia": linha.get("NomeFantasia"),
            "end_cep": linha.get("CEP") or None,
            "end_municipio": linha.get("Cidade") or None,
            "end_uf": linha.get("UF") or None,
            "end_pais": linha.get("Pais") or None,
            "site": site,
            "origem_societario": "ANAC",
            "origem_contato": "ANAC",
            "fonte_ref": f"{ARQ_ORG_PRODUCAO}:RazaoSocial",
        }
        campos.update(_separa_endereco(linha.get("Endereco"), linha.get("Complemento")))
        # Telefone, e-mail e logo não existem neste arquivo, e nada é inventado:
        # ficam fora do dict, logo NULL, e a origem 'ANAC' vale só para o que
        # o arquivo realmente traz.
        campos["origem_contato"] = "ANAC" if site else "MANUAL"
        if _upsert_extra(conn, pid, campos, campos["fonte_ref"]):
            resumo["org_producao"] += 1

    for linha in _carregar(ARQ_CERTIFICADAS):
        nabrev = (linha.get("ORG_NABREV") or "").strip()
        site = _arruma_site(linha.get("ORG_SITE"))
        if not nabrev:
            continue
        r = conn.execute(
            "SELECT pessoa_id FROM fabricante WHERE org_nabrev = ? "
            "OR org_nabrev = ? OR org_codigo = ?",
            (nabrev, nabrev.upper(), nabrev.upper())).fetchone()
        if r is None:
            resumo["certificadas_sem_fabricante"].append(
                {"org_nabrev": nabrev, "site": site})
            continue
        # Este arquivo só traz o site. Nome fantasia e razão social não estão
        # nele, então não são preenchidas por dedução do nome abreviado.
        campos = {
            "site": site,
            "origem_contato": "ANAC" if site else "MANUAL",
            "fonte_ref": f"{ARQ_CERTIFICADAS}:ORG_SITE",
        }
        if _upsert_extra(conn, r[0], campos, campos["fonte_ref"]):
            resumo["certificadas"] += 1

    conn.commit()
    return resumo


def registrar_auditoria(conn, tabela: str, chave: str, rotulo: str,
                        mudancas: dict, por: str | None) -> None:
    """Grava o antes/depois de cada campo que mudou."""
    em = _agora()
    for campo, (antes, depois) in mudancas.items():
        if antes == depois:
            continue
        conn.execute(
            "INSERT INTO registro_auditoria (em, por, tabela, chave, rotulo, campo, antes, depois) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (em, por, tabela, chave, rotulo, campo,
             None if antes is None else str(antes),
             None if depois is None else str(depois)))
