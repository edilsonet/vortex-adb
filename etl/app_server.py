"""Servidor local do registro editável.

`python etl/app_server.py` sobe em `127.0.0.1` e serve duas coisas: a interface
(um HTML só, sem CDN) e uma API JSON de leitura e escrita.

Só biblioteca padrão: nada de pip, nada de npm, nada de framework. O banco é o
`build/anac.db` que o ETL já produz, e a camada editável vem de
`schema/registro.sql`.

Três decisões que valem explicar:

**1. Editar não é reescrever a fonte.** Um `PUT` grava em `pessoa_extra` ou
`registro_override`, nunca em `pessoa`, `aeronave` ou `aerodromo`. A camada
editável é `ON DELETE CASCADE` e sobrevive a um `etl/run.py` completo; o dado da
ANAC continua recarregável e o curado continua íntegro.

**2. Toda escrita passa por auditoria.** `registro_auditoria` guarda o antes e o
depois de cada campo alterado, com quem alterou. Um `PUT` que muda nada não
grava linha nenhuma.

**3. A API não aceita nome de coluna vindo do cliente.** `registro_override` é
chave-valor genérico, e key-value genérico com nome de coluna dinâmico é
injeção de SQL esperando acontecer. Cada entidade tem uma lista explícita de
campos editáveis, e o servidor valida contra ela antes de escrever.

Escopo de rede: `127.0.0.1` apenas. É uma aplicação local de cadastro, sem
autenticação porque não há rede exposta; se um dia for publicada fora do
localhost, precisa de sessão e de política de acesso por papel.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import consulta as CST
import registro_db as RD
from common import DB_PATH

HOST = "127.0.0.1"
PORTA_PADRAO = 8730
LIMITE_PAGINA = 200

CAMPOS_PESSOA = (
    "nome_alterado", "uf_alterada", "justificativa",
    "razao_social", "nome_fantasia",
    "telefone", "email", "site", "logo_url",
    "end_logradouro", "end_numero", "end_complemento", "end_bairro",
    "end_cep", "end_municipio", "end_uf", "end_pais",
)
CAMPOS_AERONAVE = (
    "matricula", "modelo", "fabricante", "numero_serie", "ano_fabricacao",
    "classe", "tipo_icao", "motivo_cancelamento",
)
CAMPOS_AERODROMO = (
    "nome", "municipio", "uf", "situacao", "tipo", "operador", "observacao",
)
CAMPOS_SOCIO = ("socio_nome", "socio_cpf", "qual_cargo", "participacao_pct")

# --- o que é da ANAC e o que não é -------------------------------------------
#
# A regra é uma só, e ela vale para as três entidades: **campo cujo valor veio
# de um arquivo de dados abertos da ANAC é somente-leitura**. Quem altera dado
# oficial é a ANAC; o operador anota o que falta e corrige o que a fonte errou
# em campo próprio, mas não reescreve a fonte.
#
# Na prática isso significa duas coisas bem diferentes:
#
# - `pessoa_extra` guarda a procedência (`origem_societario`/`origem_contato`).
#   Campo com valor e origem `ANAC` fica travado. Campo vazio é editável, porque
#   vazio quer dizer "a fonte não traz" — não "a fonte traz em branco".
# - `aeronave` e `aerodromo` não têm coluna de procedência, porque *todas* as
#   colunas vêm da fonte. Aí o teste é direto: se a coluna tem valor, o campo
#   está travado. `operador` e `observacao` não têm coluna nenhuma em
#   `aerodromo` — nunca estão travados, e é aí que o operador trabalha.
#
# `nome_alterado`, `uf_alterada` e `justificativa` nunca são travados: eles não
# são o dado da ANAC, são a anotação de que o dado da ANAC está errado. É o
# mecanismo que permite corrigir identidade sem mutar a fonte.
CAMPOS_CORRECAO = ("nome_alterado", "uf_alterada", "justificativa")
CAMPOS_SOCIETARIOS = ("razao_social", "nome_fantasia", "end_logradouro",
                      "end_numero", "end_complemento", "end_bairro", "end_cep",
                      "end_municipio", "end_uf", "end_pais", "site")
CAMPOS_CONTATO = ("telefone", "email", "site", "logo_url")
CAMPOS_SEM_FONTE = ("operador", "observacao")

# Ponte entre o nome que a interface edita e a coluna real de `aeronave`. Ler
# o valor da fonte para comparar com o que o operador digitou depende disto:
# sem o mapa, "o campo voltou ao valor público" nunca aconteceria, porque o
# override é gravado com `matricula` e a coluna se chama `nr_cert_matricula`.
COLUNA_AERONAVE = {
    "matricula": "nr_cert_matricula",
    "modelo": "ds_modelo",
    "fabricante": "nm_fabricante",
    "numero_serie": "nr_serie",
    "ano_fabricacao": "nr_ano_fabricacao",
    "classe": "cd_classe",
    "tipo_icao": "cd_tipo_icao",
    "motivo_cancelamento": "ds_motivo_cancelamento",
}
# Em `aerodromo` os nomes já coincidem, exceto `operador` e `observacao`, que
# não existem na fonte: são campo só-manual e por isso não têm coluna para
# comparar.
COLUNA_AERODROMO = {
    "nome": "nome", "municipio": "municipio", "uf": "uf", "situacao": "situacao",
    "tipo": "tipo", "operador": None, "observacao": None,
}
# `aerodromo` é chaveado por `icao` (texto), não por id numérico.
CHAVE_AERODROMO = "icao"

# A busca do usuário vira LIKE. Estes dois caracteres ficam escapados para que o
# termo não vire um curinga: "100%" não pode casar com a frota inteira.
_ESCAPE_LIKE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _like(termo: str) -> str:
    return "%" + termo.translate(_ESCAPE_LIKE) + "%"


def _numero(valor, padrao=None, minimo=None, maximo=None):
    """Número aceitando vírgula decimal, que é como o pt-BR digita.

    A participação do sócio é um percentual: "49,5" é a forma normal de
    escrever, e `int("49.5")` levantaria ValueError, devolvendo o padrão e
    descartando o dado em silêncio.
    """
    try:
        n = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return padrao
    if minimo is not None and n < minimo:
        return minimo
    if maximo is not None and n > maximo:
        return maximo
    return int(n) if n.is_integer() else n


def _int(valor, padrao=None, minimo=None, maximo=None):
    n = _numero(valor, None, minimo, maximo)
    return padrao if n is None else int(n)


def _texto(valor):
    """Normaliza o que veio do formulário.

    Texto vazio vira NULL. Na interface, vazio é o estado honesto de "a fonte
    não traz", e guardar string vazia faria o campo parecer preenchido.
    """
    return (str(valor).strip() if valor is not None else None) or None


def pessoa_bloqueados(extra: dict | None) -> set[str]:
    """Campos de `pessoa` que a ANAC preencheu e portanto estão travados."""
    if not extra:
        return set()
    bloq: set[str] = set()
    if (extra.get("origem_societario") or "MANUAL") == "ANAC":
        bloq |= {c for c in CAMPOS_SOCIETARIOS if _texto(extra.get(c))}
    if (extra.get("origem_contato") or "MANUAL") == "ANAC":
        bloq |= {c for c in CAMPOS_CONTATO if _texto(extra.get(c))}
    return bloq - set(CAMPOS_CORRECAO)


def fonte_bloqueados(base: str, fonte: dict, colunas: dict,
                     allow: tuple) -> set[str]:
    """Campos de `aeronave`/`aerodromo` cujo valor já existe na fonte.

    Campo sem coluna correspondente (`operador`, `observacao`) nunca é
    bloqueado: ele não existe no arquivo da ANAC, que é exatamente o caso em
    que o operador tem o que preencher.
    """
    return {campo for campo in allow
            if campo not in CAMPOS_SEM_FONTE and _texto(fonte.get(colunas.get(campo)))}


class ValorErro(Exception):
    """Dado invalido do cliente. Vira 400, nao 500."""


def _extra(conn: sqlite3.Connection, pid: int) -> dict:
    """Linha de `pessoa_extra` como dict, ou `{}` se a pessoa não tem uma."""
    linha = conn.execute("SELECT * FROM pessoa_extra WHERE pessoa_id = ?",
                         (pid,)).fetchone()
    if linha is None:
        return {}
    nomes = [d[0] for d in conn.execute("SELECT * FROM pessoa_extra WHERE 0").description]
    return dict(zip(nomes, linha))


class Consulta:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------- estado
    def estado(self) -> dict:
        c = self.conn
        q = c.execute

        def um(sql, args=()):
            r = q(sql, args).fetchone()
            return r[0] if r else 0

        # Cobertura: quanto de cada coisa a ANAC de fato traz. É o número que
        # separa "campo vazio" de "campo que a fonte não tem".
        return {
            "banco": str(DB_PATH),
            "pessoas": um("SELECT COUNT(*) FROM pessoa"),
            "pessoas_fisicas": um("SELECT COUNT(*) FROM pessoa WHERE natureza='FISICA'"),
            "pessoas_juridicas": um("SELECT COUNT(*) FROM pessoa WHERE natureza='JURIDICA'"),
            "aeronaves": um("SELECT COUNT(*) FROM aeronave"),
            "aerodromos": um("SELECT COUNT(*) FROM aerodromo"),
            "fabricantes": um("SELECT COUNT(*) FROM fabricante"),
            "socios": um("SELECT COUNT(*) FROM pessoa_socio"),
            "editados": um("SELECT COUNT(*) FROM pessoa_extra "
                           "WHERE editado_em IS NOT NULL OR razao_social IS NOT NULL "
                           "OR telefone IS NOT NULL OR email IS NOT NULL "
                           "OR nome_alterado IS NOT NULL"),
            "cobertura": {
                "razao_social": um("SELECT COUNT(*) FROM pessoa_extra WHERE razao_social IS NOT NULL"),
                "site": um("SELECT COUNT(*) FROM pessoa_extra WHERE site IS NOT NULL"),
                "endereco": um("SELECT COUNT(*) FROM pessoa_extra WHERE end_municipio IS NOT NULL"),
                "telefone": um("SELECT COUNT(*) FROM pessoa_extra WHERE telefone IS NOT NULL"),
                "email": um("SELECT COUNT(*) FROM pessoa_extra WHERE email IS NOT NULL"),
                "logo": um("SELECT COUNT(*) FROM pessoa_extra WHERE logo_url IS NOT NULL"),
                "socios": um("SELECT COUNT(*) FROM pessoa_socio"),
            },
            "aviso": ("Telefone, e-mail e logo não existem em nenhum arquivo de dados "
                      "abertos da ANAC. Razão social, nome fantasia, endereço e site "
                      "só existem para organizações de produção e empresas "
                      "certificadas. O que estiver vazio não foi esquecido: não está "
                      "na fonte."),
        }

    # ------------------------------------------------------------ pessoas
    def listar_pessoas(self, q: dict) -> dict:
        onde, args = [], []
        termo = (q.get("q") or [""])[0].strip()
        if termo:
            # A busca cobre o nome e o documento, e a razão social/nome fantasia
            # que o operador tenha preenchido.
            onde.append("(v.nome LIKE ? ESCAPE '\\' OR v.documento LIKE ? ESCAPE '\\' "
                        "OR v.razao_social LIKE ? ESCAPE '\\' "
                        "OR v.nome_fantasia LIKE ? ESCAPE '\\' "
                        "OR v.email LIKE ? ESCAPE '\\' OR v.site LIKE ? ESCAPE '\\')")
            padrao = _like(termo)
            args += [padrao] * 6
        nat = (q.get("natureza") or [""])[0]
        if nat in ("FISICA", "JURIDICA"):
            onde.append("v.natureza = ?")
            args.append(nat)
        uf = (q.get("uf") or [""])[0].strip().upper()
        if uf:
            onde.append("v.uf = ?")
            args.append(uf)
        cont = (q.get("contato") or [""])[0]
        if cont == "com":
            onde.append("(v.telefone IS NOT NULL OR v.email IS NOT NULL "
                        "OR v.site IS NOT NULL OR v.logo_url IS NOT NULL)")
        elif cont == "sem":
            onde.append("(v.telefone IS NULL AND v.email IS NULL "
                        "AND v.site IS NULL AND v.logo_url IS NULL)")
        if (q.get("fabricante") or [""])[0] == "1":
            onde.append("v.eh_fabricante = 1")

        sql_onde = ("WHERE " + " AND ".join(onde)) if onde else ""
        limite = _int((q.get("limite") or [LIMITE_PAGINA])[0], LIMITE_PAGINA, 1, LIMITE_PAGINA)
        offset = _int((q.get("offset") or [0])[0], 0, 0)

        total = self.conn.execute(
            f"SELECT COUNT(*) FROM v_pessoa_registro v {sql_onde}", args).fetchone()[0]
        linhas = self.conn.execute(f"""
            SELECT v.id, v.nome, v.natureza, v.documento, v.uf, v.razao_social,
                   v.nome_fantasia, v.telefone, v.email, v.site, v.logo_url,
                   v.origem_societario, v.origem_contato, v.eh_fabricante,
                   (SELECT COUNT(DISTINCT p.aeronave_id) FROM participacao p
                     WHERE p.pessoa_id = v.id AND p.papel='PROPRIETARIO') AS n_prop,
                   (SELECT COUNT(DISTINCT p.aeronave_id) FROM participacao p
                     WHERE p.pessoa_id = v.id AND p.papel='OPERADOR') AS n_op,
                   (SELECT COUNT(*) FROM pessoa_socio s WHERE s.empresa_pessoa_id = v.id) AS n_socios
            FROM v_pessoa_registro v {sql_onde}
            ORDER BY (v.razao_social IS NULL), v.razao_social, v.nome
            LIMIT ? OFFSET ?
        """, (*args, limite, offset)).fetchall()
        colunas = ("id", "nome", "natureza", "documento", "uf", "razao_social",
                   "nome_fantasia", "telefone", "email", "site", "logo_url",
                   "origem_societario", "origem_contato", "eh_fabricante",
                   "n_prop", "n_op", "n_socios")
        return {"total": total, "limite": limite, "offset": offset,
                "itens": [dict(zip(colunas, l)) for l in linhas]}

    def pessoa(self, pid: int) -> dict | None:
        c = self.conn.execute
        cur = c("SELECT * FROM v_pessoa_registro WHERE id = ?", (pid,))
        linha = cur.fetchone()
        if linha is None:
            return None
        pessoa = dict(zip([d[0] for d in cur.description], linha))
        pessoa["aeronaves"] = [dict(zip(
            ("papel", "percentual", "matricula", "modelo", "fabricante"), l))
            for l in c("""
            SELECT q.papel, q.percentual, a.nr_cert_matricula, a.ds_modelo, a.nm_fabricante
            FROM participacao q JOIN aeronave a ON a.id = q.aeronave_id
            WHERE q.pessoa_id = ? AND q.papel = 'PROPRIETARIO'
            ORDER BY a.nr_cert_matricula LIMIT 25""", (pid,))]
        pessoa["aeronaves_como_operador"] = c("""
            SELECT COUNT(DISTINCT q.aeronave_id) FROM participacao q
            WHERE q.pessoa_id = ? AND q.papel = 'OPERADOR'""", (pid,)).fetchone()[0]
        pessoa["aeronaves_como_proprietario"] = c("""
            SELECT COUNT(DISTINCT q.aeronave_id) FROM participacao q
            WHERE q.pessoa_id = ? AND q.papel = 'PROPRIETARIO'""", (pid,)).fetchone()[0]
        pessoa["socios"] = [dict(zip(
            ("id", "socio_nome", "socio_cpf", "qual_cargo", "participacao_pct",
             "socio_pessoa_id", "origem", "editado_em"), l))
            for l in c("SELECT id, socio_nome, socio_cpf, qual_cargo, "
                       "participacao_pct, socio_pessoa_id, origem, editado_em "
                       "FROM pessoa_socio WHERE empresa_pessoa_id = ? ORDER BY socio_nome",
                       (pid,))]
        pessoa["auditoria"] = [dict(zip(("em", "por", "campo", "antes", "depois"), l))
                               for l in self._auditoria("pessoa_extra", str(pid))]
        pessoa["bloqueados"] = sorted(pessoa_bloqueados(_extra(self.conn, pid)))
        pessoa["editaveis"] = sorted(set(CAMPOS_PESSOA) - set(pessoa["bloqueados"]))
        if pessoa.get("natureza") == "JURIDICA":
            linha = c("""SELECT f.org_codigo, f.org_nabrev FROM fabricante f
                         WHERE f.pessoa_id = ?""", (pid,)).fetchone()
            pessoa["fabricante"] = (
                {"org_codigo": linha[0], "org_nabrev": linha[1]} if linha else None)
        return pessoa

    def _auditoria(self, tabela, chave, limite=20):
        return self.conn.execute(
            "SELECT em, por, campo, antes, depois FROM registro_auditoria "
            "WHERE tabela = ? AND chave = ? ORDER BY id DESC LIMIT ?",
            (tabela, chave, limite)).fetchall()
# ------------------------------------------------------- escritas
    def salvar_pessoa(self, pid: int, dados: dict, por: str) -> dict:
        """Grava a sobreposição da pessoa e audita campo a campo.

        Campo que não veio no corpo é ignorado, e campo fora da allowlist é
        recusado.
        """
        linha = self.conn.execute(
            "SELECT * FROM pessoa_extra WHERE pessoa_id = ?", (pid,)).fetchone()
        antes = _extra(self.conn, pid)

        campos, mudancas = [], {}
        bloq = pessoa_bloqueados(antes)
        for chave, valor in (dados or {}).items():
            if chave not in CAMPOS_PESSOA:
                raise ValorErro(f"campo não editável: {chave}")
            if chave in bloq:
                raise ValorErro(
                    f"campo oficial da ANAC, somente leitura: {chave}")
            texto = _texto(valor)
            if antes.get(chave) != texto:
                mudancas[chave] = (antes.get(chave), texto)
            campos.append((chave, texto))

        if not mudancas:
            # Nada difere do que já estava gravado. Devolver antes de escrever
            # evita carimbar `editado_em` e poluir a auditoria com um evento
            # que não mudou nada.
            return {"ok": True, "alterado": False, "campos": []}

        agora = RD._agora()
        # `DO NOTHING` preserva a procedência que o backfill da ANAC gravou; a
        # linha nova nasce MANUAL porque veio do formulário.
        self.conn.execute(
            "INSERT INTO pessoa_extra (pessoa_id, origem_societario, origem_contato, editado_em) "
            "VALUES (?, 'MANUAL', 'MANUAL', ?) ON CONFLICT(pessoa_id) DO NOTHING",
            (pid, agora))
        for chave, valor in campos:
            self.conn.execute(
                f"UPDATE pessoa_extra SET {chave} = ? WHERE pessoa_id = ?", (valor, pid))
        # Só marca MANUAL o que de fato mudou. Reenviar o formulário sem
        # editar nada não pode converter um dado da ANAC em curado.
        if any(c in ("telefone", "email", "site", "logo_url") for c in mudancas):
            self.conn.execute("UPDATE pessoa_extra SET origem_contato = 'MANUAL' "
                              "WHERE pessoa_id = ?", (pid,))
        if any(c in ("razao_social", "nome_fantasia") for c in mudancas):
            self.conn.execute("UPDATE pessoa_extra SET origem_societario = 'MANUAL' "
                              "WHERE pessoa_id = ?", (pid,))
        self.conn.execute("UPDATE pessoa_extra SET editado_em = ?, editado_por = ? "
                          "WHERE pessoa_id = ?", (agora, por, pid))
        rotulo = (antes.get("nome_alterado") or self.conn.execute(
            "SELECT nome FROM pessoa WHERE id = ?", (pid,)).fetchone()[0])
        RD.registrar_auditoria(self.conn, "pessoa_extra", str(pid), rotulo, mudancas, por)
        self.conn.commit()
        return {"ok": True, "alterado": True,
                "campos": sorted(mudancas), "editado_em": agora}

    def salvar_override(self, tabela: str, chave, dados: dict, por: str) -> dict:
        """Sobrepõe campos de `aeronave` ou `aerodromo` via `registro_override`."""
        spec = {
            "aeronave": (CAMPOS_AERONAVE, COLUNA_AERONAVE, "aeronave", "id"),
            "aerodromo": (CAMPOS_AERODROMO, COLUNA_AERODROMO, "aerodromo", CHAVE_AERODROMO),
        }.get(tabela)
        if spec is None:
            raise ValorErro(f"tabela não editável: {tabela}")
        allow, colunas, base, coluna_chave = spec

        linha = self.conn.execute(
            f"SELECT * FROM {base} WHERE {coluna_chave} = ?", (chave,)).fetchone()
        if linha is None:
            raise ValorErro(f"registro não encontrado: {chave}")
        fonte = dict(zip([d[0] for d in self.conn.execute(
            f"SELECT * FROM {base} WHERE 0").description], linha))

        # O que está gravado agora, depois da sobreposição: é contra isso que se
        # decide se houve mudança. Comparar só com a fonte daria "alterado" para
        # quem reenviasse o formulário sem mexer em nada.
        gravado = {r[0]: r[1] for r in self.conn.execute(
            "SELECT campo, valor FROM registro_override WHERE tabela = ? AND chave = ?",
            (tabela, str(chave)))}

        agora, mudancas = RD._agora(), {}
        bloq = fonte_bloqueados(base, fonte, colunas, allow)
        for campo, valor in (dados or {}).items():
            if campo not in allow:
                raise ValorErro(f"campo não editável: {campo}")
            if campo in bloq:
                raise ValorErro(
                    f"campo oficial da ANAC, somente leitura: {campo}")
            texto = _texto(valor)
            col = colunas[campo]
            # A fonte guarda `''` onde um campo em branco viria do formulário.
            # Sem normalizar os dois lados, "não preenchido" da tela e "" do
            # banco pareciam diferentes e a gravação criava uma linha de
            # sobreposição com NULL — que faz o campo parecer curado na tela.
            original = _texto(fonte.get(col) if col else None)
            if texto == original:
                # Igual à fonte: não deve haver sobreposição, e o COALESCE da
                # view devolve o valor público. Mas, se havia uma sobreposição,
                # voltar ao valor público É uma mudança e precisa entrar na
                # auditoria — é o caminho de desfazer.
                anterior = _texto(gravado.get(campo, original))
                self.conn.execute(
                    "DELETE FROM registro_override WHERE tabela=? AND chave=? AND campo=?",
                    (tabela, str(chave), campo))
                if anterior != texto:
                    mudancas[campo] = (anterior, texto)
                continue
            anterior = gravado.get(campo, original)
            if texto == _texto(anterior):
                continue
            mudancas[campo] = (_texto(anterior), texto)
            self.conn.execute(
                "INSERT INTO registro_override (tabela, chave, campo, valor, editado_em, editado_por) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(tabela, chave, campo) DO UPDATE SET "
                "valor=excluded.valor, editado_em=excluded.editado_em, editado_por=excluded.editado_por",
                (tabela, str(chave), campo, texto, agora, por))
        if mudancas:
            rotulo = str(fonte.get("nr_cert_matricula") or fonte.get("icao") or chave)
            RD.registrar_auditoria(self.conn, base, str(chave), rotulo, mudancas, por)
        self.conn.commit()
        return {"ok": True, "alterado": bool(mudancas), "campos": sorted(mudancas)}

    def adicionar_socio(self, pid: int, dados: dict, por: str) -> dict:
        nome = _texto(dados.get("socio_nome"))
        if not nome:
            raise ValorErro("socio_nome é obrigatório")
        if self.conn.execute("SELECT 1 FROM pessoa WHERE id = ?", (pid,)).fetchone() is None:
            raise ValorErro(f"empresa não encontrada: {pid}")
        campos = []
        for chave in CAMPOS_SOCIO:
            if chave == "socio_nome":
                continue
            valor = dados.get(chave)
            if chave == "participacao_pct" and valor not in (None, ""):
                valor = _numero(valor, None, 0, 100)
            campos.append((chave, _texto(valor)))
        cols = [c for c, _ in campos]
        try:
            novo = self.conn.execute(
                f"INSERT INTO pessoa_socio (empresa_pessoa_id, socio_nome, "
                f"{', '.join(cols)}, origem, editado_em) "
                f"VALUES (?, ?, {', '.join('?' for _ in cols)}, 'MANUAL', ?)",
                (pid, nome, *[v for _, v in campos], RD._agora())).lastrowid
        except sqlite3.IntegrityError:
            raise ValorErro("já existe sócio com esse nome e CPF nesta empresa") from None
        self.conn.commit()
        return {"ok": True, "id": novo}

    def remover_socio(self, socio_id: int) -> dict:
        self.conn.execute("DELETE FROM pessoa_socio WHERE id = ?", (socio_id,))
        self.conn.commit()
        return {"ok": True}

    # ------------------------------------------------- aeronaves/aerodromos
    def listar(self, tabela: str, q: dict) -> dict:
        spec = {
            "aeronave": ("v_aeronave_registro", "matricula_ed",
                         ("matricula_ed", "modelo_ed", "fabricante_ed")),
            "aerodromo": ("v_aerodromo_registro", "nome_ed",
                          ("nome_ed", "municipio_ed", "tipo_ed")),
        }[tabela]
        view, rotulo_ordem, campos_busca = spec
        onde, args = [], []
        termo = (q.get("q") or [""])[0].strip()
        if termo:
            padrao = _like(termo)
            onde.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in campos_busca) + ")")
            args += [padrao] * len(campos_busca)
        uf = (q.get("uf") or [""])[0].strip().upper()
        if uf and tabela == "aerodromo":
            onde.append("uf_ed = ?")
            args.append(uf)
        sql_onde = ("WHERE " + " AND ".join(onde)) if onde else ""
        limite = _int((q.get("limite") or [LIMITE_PAGINA])[0], LIMITE_PAGINA, 1, LIMITE_PAGINA)
        offset = _int((q.get("offset") or [0])[0], 0, 0)
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM {view} {sql_onde}", args).fetchone()[0]
        linhas = self.conn.execute(
            f"SELECT * FROM {view} {sql_onde} ORDER BY {rotulo_ordem} LIMIT ? OFFSET ?",
            (*args, limite, offset)).fetchall()
        desc = [d[0] for d in self.conn.execute(f"SELECT * FROM {view} LIMIT 0").description]
        return {"total": total, "limite": limite, "offset": offset,
                "itens": [dict(zip(desc, l)) for l in linhas]}

    def obter(self, tabela: str, chave) -> dict | None:
        spec = {
            "aeronave": ("v_aeronave_registro", "id"),
            "aerodromo": ("v_aerodromo_registro", "id"),
            "pessoa": ("v_pessoa_registro", "id"),
        }[tabela]
        view, coluna = spec
        linha = self.conn.execute(
            f"SELECT * FROM {view} WHERE {coluna} = ?", (chave,)).fetchone()
        if linha is None:
            return None
        desc = [d[0] for d in self.conn.execute(f"SELECT * FROM {view} LIMIT 0").description]
        reg = dict(zip(desc, linha))
        if tabela == "pessoa":
            reg = self.pessoa(int(chave))
            reg["overrides"] = {}
        else:
            reg["auditoria"] = [dict(zip(("em", "por", "campo", "antes", "depois"), l))
                                for l in self._auditoria(tabela, str(chave))]
            reg["overrides"] = {r[0]: r[1] for r in self.conn.execute(
                "SELECT campo, valor FROM registro_override WHERE tabela = ? AND chave = ?",
                (tabela, str(chave)))}
            spec = {"aeronave": (CAMPOS_AERONAVE, COLUNA_AERONAVE, "aeronave", "id"),
                    "aerodromo": (CAMPOS_AERODROMO, COLUNA_AERODROMO, "aerodromo",
                                  CHAVE_AERODROMO)}[tabela]
            reg["bloqueados"] = sorted(fonte_bloqueados(
                spec[2], reg, spec[1], spec[0]))
            reg["editaveis"] = sorted(set(spec[0]) - set(reg["bloqueados"]))
        return reg

    # ------------------------------------------------------- catálogos
    def listar_catalogo(self, nome: str, q: dict) -> dict:
        return CST.listar(self.conn, nome, q)

    def detalhe_catalogo(self, nome: str, chave: str) -> dict | None:
        reg = CST.detalhe(self.conn, nome, chave)
        if reg is None:
            return None
        # A trava acompanha o detalhe: a tela mostra o mesmo campo travado que o
        # servidor recusaria, e nunca há dois lugares que discordem do que é
        # oficial.
        if nome in ("empresa", "usuario") and reg.get("natureza"):
            reg["bloqueados"] = sorted(pessoa_bloqueados(_extra(self.conn, reg["id"])))
        elif nome == "aeronave" and reg.get("id"):
            spec = (CAMPOS_AERONAVE, COLUNA_AERONAVE, "aeronave", "id")
            reg["bloqueados"] = sorted(fonte_bloqueados(spec[2], reg, spec[1], spec[0]))
        elif nome == "aerodromo":
            reg["bloqueados"] = sorted(fonte_bloqueados(
                "aerodromo", reg, COLUNA_AERODROMO, CAMPOS_AERODROMO))
        if "bloqueados" in reg:
            reg["editaveis"] = sorted(
                set(CAMPOS_PESSOA if nome in ("empresa", "usuario")
                    else CAMPOS_AERODROMO if nome == "aerodromo"
                    else CAMPOS_AERONAVE) - set(reg["bloqueados"]))
        return reg


def main(argv=None) -> int:
    """Ponto de entrada. HTTP em `registro_http`, consultas aqui."""
    from registro_http import main as http_main
    return http_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
