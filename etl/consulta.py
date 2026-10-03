"""Consultas dos catálogos do painel lateral.

Cada menu do sidebar — empresas, usuários, aeronaves, fabricantes, modelos,
marcas, aeródromos, drones e vínculos — vira uma entrada em `CATALOGOS`. A
entrada diz de onde sai a lista, o que a busca texto casa e o que a lista
mostra; paginação, escape do LIKE e montagem do resultado ficam aqui.

Quatro decisões que valem explicar:

**1. A chave natural, não o id.** `marca`, `aerodromo`, `drone` e `vinculo` não
têm surrogate que faça sentido na URL: marca é o nome, aeródromo é o ICAO,
drone é o código SISANT e vínculo é o próprio `participacao.id`. A navegação
usa a chave que o usuário reconhece.

**2. `de`/`projeto` são literais, não entrada do cliente.** Só este módulo
escreve esses fragmentos, e o que vem do cliente entra sempre como `?`. É o
mesmo critério de `app_server.py`: nome de tabela e nome de coluna dinâmico
vindo do cliente é injeção esperando acontecer.

**3. A ordem das expressões do SELECT é manual.** `sqlite3.Row` resolveria isso,
mas `Row` não sobrevive a `dict()` com chave repetida entre `projeto` e
`extra_projeto`. `_dividir_proj` devolve os nomes na ordem escrita, que é a
única forma de casar `zip` com as linhas cruas.

**4. Contagem e página saem juntas, mas o `COUNT` só é barato com filtro.**
867 mil vínculos sem filtro fazem `COUNT(*)` em menos de um segundo por causa
do índice, então ele roda sempre e o número exato volta junto.
"""

from __future__ import annotations

import sqlite3

_ESCAPE_LIKE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _like(termo: str) -> str:
    return "%" + termo.translate(_ESCAPE_LIKE) + "%"


def _int(valor, padrao=None, minimo=None, maximo=None):
    try:
        n = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return padrao
    if minimo is not None and n < minimo:
        return minimo
    if maximo is not None and n > maximo:
        return maximo
    return int(n) if n.is_integer() else n


def _dividir_proj(sql: str) -> list[str]:
    """Separa expressões de SELECT e devolve o nome de cada uma.

    Respeita parênteses, aspas e comentários de linha para não cortar dentro de
    uma subconsulta, e prefere o alias declarado com `AS`. Sem alias, o SQLite
    devolve o texto da expressão como nome de coluna — inútil como chave de
    JSON — então aí não há o que fazer além de devolver o que vier.
    """
    partes, atual, prof, em_citacao = [], [], 0, False
    em_comentario = False
    for i, ch in enumerate(sql):
        # Comentário de linha: ignorado inteiro, porque uma vírgula dentro de um
        # comentário seria lida como separador de expressão. Foi assim que uma
        # linha de explicação dentro do SQL passou a contar como coluna.
        if em_comentario:
            if ch == "\n":
                em_comentario = False
                atual.append(ch)
            continue
        if not em_citacao and ch == "-" and sql[i:i + 2] == "--":
            em_comentario = True
            continue
        if ch == "'":
            em_citacao = not em_citacao
        if not em_citacao:
            if ch == "(":
                prof += 1
            elif ch == ")":
                prof -= 1
            elif ch == "," and prof == 0:
                partes.append("".join(atual).strip())
                atual = []
                continue
        atual.append(ch)
    if atual:
        partes.append("".join(atual).strip())
    partes = [p for p in partes if p]

    nomes = []
    for parte in partes:
        nome = None
        # `AS` de topo: procura de trás para frente, para o alias de uma
        # subconsulta aninhada não ser confundido com o da expressão externa.
        profundidade, i = 0, len(parte) - 1
        while i >= 0:
            if parte[i] == ")":
                profundidade += 1
            elif parte[i] == "(":
                profundidade -= 1
            elif profundidade == 0 and parte[i:i + 2].upper() == "AS" \
                    and (i == 0 or not parte[i - 1].isalnum()) \
                    and (i + 2 >= len(parte) or not parte[i + 2].isalnum()):
                nome = parte[i + 2:].strip().strip('"[]`')
                break
            i -= 1
        nomes.append(nome or parte)
    return nomes


def _colunas(conn: sqlite3.Connection, sql: str, args: tuple = ()):
    cur = conn.execute(sql, args)
    return [d[0] for d in cur.description], cur.fetchall()


def _linha_1(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> dict | None:
    nomes, linhas = _colunas(conn, sql, args)
    return dict(zip(nomes, linhas[0])) if linhas else None


def _lista(conn: sqlite3.Connection, sql: str, args: tuple = (), limite=50,
           offset=0, ordem="") -> list[dict]:
    nomes, linhas = _colunas(conn, sql, args)
    return [dict(zip(nomes, l)) for l in linhas]


# Matrícula e marca são coisas diferentes, e as duas estão na fonte: o
# certificado em `NR_CERT_MATRICULA` e o prefixo em `MARCA`. Misturar as duas
# numa coluna só — que era o que a projeção fazia, jogando `marca / s/n série`
# no lugar da matrícula — mostrava como "matrícula" um valor que a ANAC nunca
# chamou de matrícula, e escondia a série num lugar onde ninguém a procurava.
#
# Aqui a matrícula é a matrícula. Onde a fonte não traz certificado, a célula
# fica vazia e a tela diz isso; a identidade da aeronave continua existindo em
# `chave_natural`, usada só para navegar.
MATRICULA_SQL = """
        NULLIF(TRIM(COALESCE(a.nr_cert_matricula, '')), '')"""
NUMERO_SERIE_SQL = """
        NULLIF(TRIM(COALESCE(a.nr_serie, '')), '')"""
IDENTIDADE_SQL = """
        CASE WHEN NULLIF(TRIM(COALESCE(a.nr_cert_matricula, '')), '') IS NOT NULL
             THEN a.nr_cert_matricula
             WHEN NULLIF(TRIM(COALESCE(a.nr_serie, '')), '') IS NOT NULL
             THEN a.marca || ' / s/n ' || a.nr_serie
             ELSE a.chave_natural END"""

# A fonte usa zeros como "sem matrícula": 112 aeronaves têm `NR_CERT_MATRICULA`
# igual a `'0'`, e há uma família inteira de `'000'`, `'00000000'`. Ordenado como
# texto, esse bloco vinha antes de todas as matrículas de verdade e abria a
# lista com uma tela de zeros. `LTRIM(..., '0') = ''` identifica o bloco inteiro
# sem precisar enumerar os casos.
def _ordem_matricula(alias: str = "a") -> str:
    return (f"(LTRIM(COALESCE({alias}.nr_cert_matricula, ''), '0') = ''), "
            f"{alias}.nr_cert_matricula")



# ============================================================== especificações
CATALOGOS: dict[str, dict] = {
    "empresa": {
        "titulo": "Empresas",
        "singular": "empresa",
        "de": "v_pessoa_registro v",
        "busca": ("v.nome", "v.razao_social", "v.nome_fantasia", "v.documento",
                  "v.chave", "v.site", "v.email", "v.telefone"),
        "filtros": ("uf",),
        "onde": ("v.natureza = 'JURIDICA'",),
        "ordem": "(v.razao_social IS NULL), v.razao_social, v.nome",
        "projeto": """v.id AS id, v.chave AS chave, v.nome AS nome,
                      v.razao_social AS razao_social, v.nome_fantasia AS nome_fantasia,
                      v.documento AS documento, v.documento_bruto AS documento_bruto,
                      v.documento_mascarado AS documento_mascarado, v.uf AS uf,
                      v.origem_societario AS origem_societario,
                      v.origem_contato AS origem_contato,
                      v.site AS site, v.email AS email, v.telefone AS telefone,
                      v.org_nabrev AS org_nabrev, v.eh_fabricante AS eh_fabricante,
                      (SELECT COUNT(*) FROM participacao p WHERE p.pessoa_id = v.id) AS n_vinculos,
                      (SELECT COUNT(*) FROM pessoa_socio s WHERE s.empresa_pessoa_id = v.id) AS n_socios,
                      (SELECT COUNT(*) FROM registro_sisant r WHERE r.pessoa_id = v.id) AS n_drones""",
    },
    "usuario": {
        "titulo": "Usuários",
        "singular": "usuário",
        "de": "v_pessoa_registro v",
        "busca": ("v.nome", "v.documento", "v.chave", "v.uf"),
        "filtros": (),
        "onde": ("v.natureza = 'FISICA'",),
        "ordem": "v.nome, v.uf",
        "projeto": """v.id AS id, v.nome AS nome, v.documento AS documento,
                      v.documento_bruto AS documento_bruto,
                      v.documento_mascarado AS documento_mascarado, v.uf AS uf,
                      v.nome_fonte AS nome_fonte, v.uf_fonte AS uf_fonte,
                      (SELECT COUNT(*) FROM participacao p WHERE p.pessoa_id = v.id) AS n_vinculos,
                      (SELECT COUNT(*) FROM registro_sisant r WHERE r.pessoa_id = v.id) AS n_drones""",
    },
    "aeronave": {
        "titulo": "Matrículas",
        "singular": "aeronave",
        "de": "v_aeronave_registro a",
        "busca": ("a.nr_cert_matricula", "a.ds_modelo", "a.nm_fabricante",
                  "a.nr_serie", "a.marca", "a.matricula_ed", "a.modelo_ed",
                  "a.fabricante_ed"),
        "filtros": (),
        "onde": (),
        # 5.538 das 36.629 aeronaves não têm certificado na fonte, e mais um
        # bloco vem preenchido com zeros. Sem tratá-los, a lista abriria com uma
        # tela de células vazias e depois de zeros — que é o que acontecia.
        "ordem": (f"{_ordem_matricula()}, a.nr_serie, a.id"),
        "projeto": """a.id AS id,
                      """ + MATRICULA_SQL + """ AS matricula,
                      """ + NUMERO_SERIE_SQL + """ AS numero_serie,
                      a.marca AS marca,
                      a.ds_modelo AS modelo, a.nm_fabricante AS fabricante,
                      (SELECT COUNT(*) FROM participacao q
                        WHERE q.aeronave_id = a.id AND q.papel = 'OPERADOR') AS n_operadores,
                      a.nr_ano_fabricacao AS ano,
                      a.cd_classe AS classe, a.cd_tipo_icao AS tipo_icao,
                      a.tp_operacao AS tp_operacao, a.dt_matricula AS dt_matricula,
                      a.snapshot_mes AS snapshot_mes,
                      a.matricula_ed AS matricula_ed, a.modelo_ed AS modelo_ed,
                      """ + IDENTIDADE_SQL + """ AS identidade,
                      (SELECT COUNT(*) FROM participacao p WHERE p.aeronave_id = a.id) AS n_vinculos,
                      (SELECT COUNT(*) FROM registro_sisant r
                        WHERE r.num_serie IS NOT NULL AND r.num_serie = a.nr_serie) AS n_drones""",
    },
    "fabricante": {
        "titulo": "Fabricantes",
        "singular": "fabricante",
        "de": "v_pessoa_registro v JOIN fabricante f ON f.pessoa_id = v.id",
        "busca": ("v.nome", "v.razao_social", "v.documento", "f.org_codigo",
                  "f.org_nabrev", "v.site"),
        "filtros": (),
        "onde": (),
        "ordem": "(v.razao_social IS NULL), v.razao_social, v.nome",
        "projeto": """f.id AS id, f.pessoa_id AS pessoa_id, f.org_codigo AS org_codigo,
                      f.org_nabrev AS org_nabrev, v.nome AS nome,
                      v.razao_social AS razao_social, v.nome_fantasia AS nome_fantasia,
                      v.documento AS documento, v.uf AS uf, v.site AS site,
                      (SELECT COUNT(*) FROM modelo mo WHERE mo.fabricante_id = f.id) AS n_modelos,
                      (SELECT COUNT(*) FROM aeronave a WHERE a.fabricante_id = f.id) AS n_aeronaves""",
    },
    "modelo": {
        "titulo": "Modelos",
        "singular": "modelo",
        "de": ("modelo mo LEFT JOIN marca m ON m.id = mo.marca_id "
               "LEFT JOIN fabricante f ON f.id = mo.fabricante_id "
               "LEFT JOIN pessoa p ON p.id = f.pessoa_id"),
        "busca": ("mo.ds_modelo", "m.nome", "p.nome", "mo.cd_tipo"),
        "filtros": (),
        "onde": (),
        "ordem": "mo.ds_modelo",
        "projeto": """mo.id AS id, mo.ds_modelo AS modelo, mo.cd_tipo AS cd_tipo,
                      m.id AS marca_id, m.nome AS marca,
                      f.id AS fabricante_id, p.nome AS fabricante,
                      (SELECT COUNT(*) FROM aeronave a WHERE a.modelo_id = mo.id) AS n_aeronaves""",
    },
    "marca": {
        "titulo": "Marcas",
        "singular": "marca",
        "de": "marca m",
        "busca": ("m.nome",),
        "filtros": (),
        "onde": (),
        "ordem": "m.nome",
        "projeto": """m.id AS id, m.nome AS nome,
                      (SELECT COUNT(*) FROM modelo mo WHERE mo.marca_id = m.id) AS n_modelos,
                      (SELECT COUNT(*) FROM aeronave a WHERE a.marca = m.nome) AS n_aeronaves,
                      (SELECT COUNT(DISTINCT mo.fabricante_id) FROM modelo mo
                        WHERE mo.marca_id = m.id) AS n_fabricantes""",
    },
    "aerodromo": {
        "titulo": "Aeródromos",
        "singular": "aeródromo",
        "de": ("v_aerodromo_registro d "
               "LEFT JOIN redemet_aerodromo_status rs ON rs.icao = d.icao"),
        "busca": ("d.icao", "d.nome", "d.nome_ed", "d.municipio", "d.ciad"),
        "filtros": ("uf", "tipo"),
        "onde": (),
        "ordem": "d.icao",
        "projeto": """d.icao AS chave, d.icao AS icao, d.ciad AS ciad,
                      d.nome AS nome, d.nome_ed AS nome_ed, d.tipo AS tipo,
                      d.tipo_ed AS tipo_ed, d.municipio AS municipio, d.uf AS uf,
                      d.situacao AS situacao, d.situacao_ed AS situacao_ed,
                      d.operacao_diurna AS operacao_diurna,
                      d.operacao_noturna AS operacao_noturna,
                      d.validade_registro AS validade_registro,
                      d.latitude AS latitude, d.longitude AS longitude,
                      rs.cor AS redemet_cor,
                      (SELECT COUNT(*) FROM redemet_mensagem m WHERE m.icao = d.icao) AS n_mensagens""",
    },
    "drone": {
        "titulo": "Drones (SISANT)",
        "singular": "drone",
        "de": "registro_sisant r LEFT JOIN pessoa p ON p.id = r.pessoa_id",
        "busca": ("r.codigo_aeronave", "r.fabricante_nome", "r.modelo_nome",
                  "r.num_serie", "p.nome"),
        "filtros": (),
        "onde": (),
        "ordem": "r.codigo_aeronave",
        "projeto": """r.codigo_aeronave AS chave, r.codigo_aeronave AS codigo_aeronave,
                      r.data_validade AS data_validade, r.tipo_uso AS tipo_uso,
                      r.fabricante_nome AS fabricante_nome,
                      r.modelo_nome AS modelo_nome, r.num_serie AS num_serie,
                      r.peso_max_kg AS peso_max_kg, r.ramo_atividade AS ramo_atividade,
                      r.pessoa_id AS pessoa_id, p.nome AS responsavel, p.uf AS resp_uf,
                      p.documento AS responsavel_documento""",
    },
    "vinculo": {
        "titulo": "Vínculos",
        "singular": "vínculo",
        "de": ("participacao q JOIN aeronave a ON a.id = q.aeronave_id "
               "JOIN pessoa p ON p.id = q.pessoa_id"),
        "busca": ("a.nr_cert_matricula", "a.ds_modelo", "p.nome", "p.documento",
                  "q.papel"),
        "filtros": ("papel",),
        "onde": (),
        "ordem": "q.snapshot_mes DESC, a.nr_cert_matricula, p.nome",
        "projeto": """q.id AS id, q.papel AS papel, q.percentual AS percentual,
                      q.uf AS uf, q.snapshot_mes AS snapshot_mes,
                      q.operacao_121 AS operacao_121, q.operacao_135 AS operacao_135,
                      q.transp_reg_121 AS transp_reg_121,
                      q.transp_reg_135 AS transp_reg_135,
                      q.aut_pmac_121 AS aut_pmac_121, q.aut_pmac_135 AS aut_pmac_135,
                      q.sae AS sae, q.authistrut AS authistrut,
                      a.id AS aeronave_id,
                      """ + MATRICULA_SQL + """ AS matricula,
                      """ + NUMERO_SERIE_SQL + """ AS numero_serie,
                      a.marca AS marca,
                      a.ds_modelo AS modelo,
                      p.id AS pessoa_id, p.nome AS pessoa_nome, p.natureza AS natureza,
                      p.documento AS documento""",
    },
    # Empresas e fabricantes numa lista só. A aba é um filtro de `tipo`, e as
    # duas linhas que aparecem juntas são a mesma pessoa: quem tem `fabricante`
    # é fabricante **e** empresa, não duas empresas. Por isso o `LEFT JOIN` e
    # não um `INNER` — some o `INNER` e as empresas que não fabricam
    # desaparecem da aba "Todos".
    "organizacao": {
        "titulo": "Empresas e fabricantes",
        "singular": "organização",
        "de": ("v_pessoa_registro v "
               "LEFT JOIN fabricante f ON f.pessoa_id = v.id"),
        "busca": ("v.nome", "v.razao_social", "v.nome_fantasia", "v.documento",
                  "v.chave", "f.org_nabrev", "f.org_codigo"),
        "filtros": (),
        "onde": ("(v.natureza = 'JURIDICA' OR f.id IS NOT NULL)",),
        # Muitos fabricantes não têm razão social gravada — a fonte traz só o
        # nome. Ordenar só por `razao_social` os empilhava no fim da lista,
        # longe da EMBRAER e da Embraer. O `COALESCE` faz quem não tem razão
        # social ordenar pelo nome, no mesmo lugar dos outros.
        "ordem": ("COALESCE(NULLIF(v.razao_social, ''), v.nome) IS NULL, "
                  "COALESCE(NULLIF(v.razao_social, ''), v.nome), v.id"),
        # `nome_exibido` cai para o nome do RAB quando não há razão social.
        # Nenhum dos 3.133 fabricantes tem uma — a ANAC não publica razão
        # social no RAB. Sem esta coalescência a coluna "Nome" da aba
        # Fabricantes sairia vazia inteira. O comentário fica aqui, e não
        # dentro do SQL, porque `_dividir_proj` conta vírgulas.
        "projeto": """v.id AS id,
                      v.razao_social AS razao_social,
                      v.nome AS nome, v.nome_fantasia AS nome_fantasia,
                      COALESCE(NULLIF(v.razao_social, ''),
                               NULLIF(v.nome_fantasia, ''), v.nome) AS nome_exibido,
                      v.documento AS documento, v.uf AS uf,
                      CASE WHEN f.id IS NOT NULL AND v.natureza = 'JURIDICA'
                           THEN 'empresa e fabricante'
                           WHEN f.id IS NOT NULL THEN 'fabricante'
                           ELSE 'empresa' END AS tipo,
                      f.org_nabrev AS org_nabrev, f.org_codigo AS org_codigo,
                      v.eh_fabricante AS eh_fabricante,
                      (SELECT COUNT(*) FROM participacao p WHERE p.pessoa_id = v.id) AS n_vinculos,
                      (SELECT COUNT(*) FROM modelo mo WHERE mo.fabricante_id = f.id) AS n_modelos,
                      (SELECT COUNT(*) FROM pessoa_socio s
                        WHERE s.empresa_pessoa_id = v.id) AS n_socios""",
    },
}


# A chave com que a tela navega. Não é sempre a mesma: `marca` é o nome, porque
# `detalhe_marca` procura por `nome`; `aerodromo` é o OACI; e a maioria é o id.
#
# Sem esta tabela, a tela teria de adivinhar entre `chave` e `id` — e `empresa`
# traz `chave` como *identidade textual* (`NOME:...|UF`), não como chave de
# registro, então a adivinhança mandaria `NOME:AEROTRON...|MG` para um campo que
# espera inteiro. A falha só apareceria no clique, que é o pior lugar.
CHAVE_NAV = {
    "empresa": "id", "usuario": "id", "aeronave": "id", "fabricante": "id",
    "modelo": "id", "marca": "nome", "aerodromo": "icao",
    "drone": "codigo_aeronave", "vinculo": "id", "organizacao": "id",
}

# Abas da lista de organizações. O valor vai para `tipo=` na consulta; `todos`
# não filtra nada. Escrito como dados, e não como `if` espalhado, porque é a
# lista de abas que a tela percorre para desenhar os botões.
ABAS_ORGANIZACAO = [
    ("todos", "Todos", ""),
    ("empresa", "Empresas", "empresa"),
    ("fabricante", "Fabricantes", "fabricante"),
]


def abas_de(nome: str) -> list[tuple[str, str, str]]:
    """Abas de um catálogo; a maioria não tem nenhuma."""
    return ABAS_ORGANIZACAO if nome == "organizacao" else []


def listar(conn: sqlite3.Connection, nome: str, q: dict) -> dict:
    """Uma página do catálogo: filtro, busca, ordem e total."""
    spec = CATALOGOS.get(nome)
    if spec is None:
        raise LookupError(f"catálogo desconhecido: {nome}")
    limite = _int((q.get("limite") or ["50"])[0], 50, 1, 500)
    offset = _int((q.get("offset") or ["0"])[0], 0, 0)
    termo = (q.get("q") or [""])[0].strip()

    onde, args = list(spec["onde"]), []
    if termo:
        padrao = _like(termo)
        onde.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in spec["busca"]) + ")")
        args += [padrao] * len(spec["busca"])
    for chave in spec["filtros"]:
        valor = (q.get(chave) or [""])[0].strip()
        if valor:
            onde.append(f"{chave} = ?")
            args.append(valor)
    mes = (q.get("mes") or [""])[0].strip()
    if mes and nome == "vinculo":
        onde.append("q.snapshot_mes = ?")
        args.append(mes)
    # Aba da lista de organizações. O filtro precisa pegar "empresa e fabricante"
    # também na aba Empresas, senão a empresa que fabrica aircraft desapareceria
    # da lista de empresas — que é justamente o caso comum.
    tipo = (q.get("tipo") or [""])[0].strip()
    if tipo and nome == "organizacao":
        if tipo == "empresa":
            onde.append("(v.natureza = 'JURIDICA' OR f.id IS NOT NULL)")
        elif tipo == "fabricante":
            onde.append("f.id IS NOT NULL")
        elif tipo != "todos":
            # `ValueError` e não `app_server.ValorErro`: este módulo é
            # importado pelo `app_server`, e importar de volta fecharia um
            # ciclo. O `registro_http` já traduz `ValueError` em 400, que é o
            # status certo aqui.
            raise ValueError(f"aba desconhecida: {tipo}")

    sql_onde = ("WHERE " + " AND ".join(onde)) if onde else ""
    total = conn.execute(f"SELECT COUNT(*) FROM {spec['de']} {sql_onde}", args).fetchone()[0]
    sql = (f"SELECT {spec['projeto']} FROM {spec['de']} {sql_onde} "
           f"ORDER BY {spec['ordem']} LIMIT ? OFFSET ?")
    nomes, linhas = _colunas(conn, sql, (*args, limite, offset))
    campos = _dividir_proj(spec["projeto"])
    assert len(campos) == len(nomes), (
        f"{nome}: {len(campos)} expressões para {len(nomes)} colunas")
    itens = [dict(zip(campos, l)) for l in linhas]
    nav = CHAVE_NAV[nome]
    for item in itens:
        item["nav"] = item.get(nav)
    return {"catalogo": nome, "titulo": spec["titulo"], "singular": spec["singular"],
            "chave_nav": nav, "total": total, "limite": limite, "offset": offset,
            "proximo": offset + len(itens) < total, "itens": itens,
            # As abas vêm do servidor: a tela não mantém uma cópia desta
            # lista, então um filtro novo não pode aparecer num lugar e
            # faltar no outro.
            "abas": [{"chave": c, "titulo": t, "ativa": c == (tipo or "")}
                     for c, t, _ in abas_de(nome)],
            "aba": tipo or ""}


# ===================================================================== detalhe
# Cada `detalhe_*` devolve o registro inteiro mais as ligações que a tela mostra.
# A chave é a mesma que a lista usa, então o clique na linha e a URL concordam.

LIMITES_RELACIONADOS = 60


def _auditoria(conn, tabela, chave, limite=20) -> list[dict]:
    return _lista(conn, "SELECT em, por, campo, antes, depois FROM registro_auditoria "
                        "WHERE tabela = ? AND chave = ? ORDER BY id DESC LIMIT ?",
                  (tabela, str(chave), limite))


def _overrides(conn, tabela, chave) -> dict:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT campo, valor FROM registro_override WHERE tabela = ? AND chave = ?",
        (tabela, str(chave)))}


def detalhe_empresa(conn, pid: int) -> dict | None:
    reg = _linha_1(conn, "SELECT * FROM v_pessoa_registro WHERE id = ? AND natureza = 'JURIDICA'",
                   (pid,))
    if reg is None:
        return None
    reg["extra"] = _linha_1(conn, "SELECT * FROM pessoa_extra WHERE pessoa_id = ?", (pid,))
    reg["fabricante"] = _linha_1(conn, "SELECT * FROM fabricante WHERE pessoa_id = ?", (pid,))
    reg["org_producao"] = _linha_1(
        conn, "SELECT * FROM org_producao WHERE REPLACE(REPLACE(REPLACE("
              "cnpj,'.',''),'/',''),'-','') = ?", (reg.get("documento"),))
    reg["socios"] = _lista(
        conn, "SELECT * FROM pessoa_socio WHERE empresa_pessoa_id = ? ORDER BY socio_nome",
        (pid,))
    reg["vincculos"] = _lista(conn, """
        SELECT q.papel, q.percentual, q.snapshot_mes, q.operacao_121, q.operacao_135,
               q.transp_reg_121, q.transp_reg_135, a.id AS aeronave_id,
               """ + MATRICULA_SQL + """ AS matricula, a.ds_modelo AS modelo,
               COALESCE(pe.razao_social, pe.nome_fantasia, p.nome) AS pessoa_nome,
               p.id AS pessoa_id
        FROM participacao q JOIN aeronave a ON a.id = q.aeronave_id
        JOIN pessoa p ON p.id = q.pessoa_id
        LEFT JOIN pessoa_extra pe ON pe.pessoa_id = p.id
        WHERE q.pessoa_id = ? ORDER BY q.snapshot_mes DESC, a.nr_cert_matricula
        LIMIT ?""", (pid, LIMITES_RELACIONADOS))
    reg["resumo_vinculos"] = _lista(conn, """
        SELECT q.papel, q.snapshot_mes, COUNT(*) AS n
        FROM participacao q WHERE q.pessoa_id = ?
        GROUP BY q.papel, q.snapshot_mes ORDER BY q.snapshot_mes DESC, q.papel
        LIMIT 24""", (pid,))
    reg["drones"] = _lista(
        conn, "SELECT * FROM registro_sisant WHERE pessoa_id = ? ORDER BY codigo_aeronave LIMIT ?",
        (pid, LIMITES_RELACIONADOS))
    reg["modelos"] = _lista(conn, """
        SELECT mo.id, mo.ds_modelo, m.nome AS marca FROM modelo mo
        LEFT JOIN marca m ON m.id = mo.marca_id
        WHERE mo.fabricante_id = (SELECT id FROM fabricante WHERE pessoa_id = ?)
        ORDER BY mo.ds_modelo LIMIT ?""", (pid, LIMITES_RELACIONADOS))
    reg["totais"] = _linha_1(conn, """
        SELECT (SELECT COUNT(*) FROM participacao WHERE pessoa_id = ?) AS vinculos,
               (SELECT COUNT(*) FROM participacao WHERE pessoa_id = ? AND papel='PROPRIETARIO') AS propriedades,
               (SELECT COUNT(*) FROM participacao WHERE pessoa_id = ? AND papel='OPERADOR') AS operacoes,
               (SELECT COUNT(*) FROM registro_sisant WHERE pessoa_id = ?) AS drones,
               (SELECT COUNT(*) FROM pessoa_socio WHERE empresa_pessoa_id = ?) AS socios""",
                            (pid, pid, pid, pid, pid))
    reg["auditoria"] = _auditoria(conn, "pessoa_extra", pid)
    reg["key"] = pid
    return reg


def detalhe_usuario(conn, pid: int) -> dict | None:
    reg = _linha_1(conn, "SELECT * FROM v_pessoa_registro WHERE id = ? AND natureza = 'FISICA'",
                   (pid,))
    if reg is None:
        return None
    reg["extra"] = _linha_1(conn, "SELECT * FROM pessoa_extra WHERE pessoa_id = ?", (pid,))
    reg["vincculos"] = _lista(conn, """
        SELECT q.papel, q.percentual, q.snapshot_mes, q.operacao_121, q.operacao_135,
               q.transp_reg_121, q.transp_reg_135, a.id AS aeronave_id,
               """ + MATRICULA_SQL + """ AS matricula, a.ds_modelo AS modelo,
               COALESCE(pe.razao_social, pe.nome_fantasia, p.nome) AS pessoa_nome,
               p.id AS pessoa_id
        FROM participacao q JOIN aeronave a ON a.id = q.aeronave_id
        JOIN pessoa p ON p.id = q.pessoa_id
        LEFT JOIN pessoa_extra pe ON pe.pessoa_id = p.id
        WHERE q.pessoa_id = ? ORDER BY q.snapshot_mes DESC, a.nr_cert_matricula
        LIMIT ?""", (pid, LIMITES_RELACIONADOS))
    reg["resumo_vinculos"] = _lista(conn, """
        SELECT q.papel, q.snapshot_mes, COUNT(*) AS n
        FROM participacao q WHERE q.pessoa_id = ?
        GROUP BY q.papel, q.snapshot_mes ORDER BY q.snapshot_mes DESC, q.papel
        LIMIT 24""", (pid,))
    reg["drones"] = _lista(
        conn, "SELECT * FROM registro_sisant WHERE pessoa_id = ? ORDER BY codigo_aeronave LIMIT ?",
        (pid, LIMITES_RELACIONADOS))
    reg["totais"] = _linha_1(conn, """
        SELECT (SELECT COUNT(*) FROM participacao WHERE pessoa_id = ?) AS vinculos,
               (SELECT COUNT(*) FROM participacao WHERE pessoa_id = ? AND papel='PROPRIETARIO') AS propriedades,
               (SELECT COUNT(*) FROM participacao WHERE pessoa_id = ? AND papel='OPERADOR') AS operacoes,
               (SELECT COUNT(*) FROM registro_sisant WHERE pessoa_id = ?) AS drones""",
                            (pid, pid, pid, pid))
    reg["aviso_cpf"] = (
        "A fonte mascara o CPF e não há QSA nos dados abertos: a identidade aqui é "
        "nome + UF, não o documento.") if reg.get("documento_mascarado") else None
    reg["auditoria"] = _auditoria(conn, "pessoa_extra", pid)
    reg["key"] = pid
    return reg


def detalhe_aeronave(conn, aid: int) -> dict | None:
    reg = _linha_1(conn, "SELECT * FROM v_aeronave_registro WHERE id = ?", (aid,))
    if reg is None:
        return None
    # 15% da frota não tem certificado de matrícula na fonte. Sem isto, a
    # página abriria com o campo principal em branco e nenhuma pista de qual
    # aeronave é aquela.
    reg["matricula_exibida"] = (
        reg.get("nr_cert_matricula")
        or (f"{reg.get('marca')} / s/n {reg.get('nr_serie')}"
            if reg.get("nr_serie") else reg.get("chave_natural")))
    reg["matricula_oficial_vazia"] = not bool(reg.get("nr_cert_matricula"))
    reg["modelo"] = _linha_1(conn, """
        SELECT mo.id, mo.ds_modelo, mo.cd_tipo, m.nome AS marca, f.id AS fabricante_id,
               p.nome AS fabricante FROM modelo mo
        LEFT JOIN marca m ON m.id = mo.marca_id
        LEFT JOIN fabricante f ON f.id = mo.fabricante_id
        LEFT JOIN pessoa p ON p.id = f.pessoa_id WHERE mo.id = ?""", (reg["modelo_id"],))
    reg["fabricante"] = _linha_1(conn, """
        SELECT f.id, f.org_codigo, f.org_nabrev, f.pessoa_id, p.nome, pe.razao_social,
               pe.nome_fantasia, pe.origem_societario
        FROM fabricante f LEFT JOIN pessoa p ON p.id = f.pessoa_id
        LEFT JOIN pessoa_extra pe ON pe.pessoa_id = f.pessoa_id
        WHERE f.id = ?""", (reg["fabricante_id"],))
    reg["vincculos"] = _lista(conn, """
        SELECT q.id AS vinculo_id, q.papel, q.percentual, q.uf, q.snapshot_mes,
               q.operacao_121, q.operacao_135, q.transp_reg_121, q.transp_reg_135,
               q.aut_pmac_121, q.aut_pmac_135, q.sae, q.authistrut,
               p.id AS pessoa_id, p.nome AS pessoa_nome, p.natureza, p.documento,
               """ + MATRICULA_SQL + """ AS matricula,
               """ + NUMERO_SERIE_SQL + """ AS numero_serie
        FROM participacao q JOIN pessoa p ON p.id = q.pessoa_id
        JOIN v_aeronave_registro a ON a.id = q.aeronave_id
        WHERE q.aeronave_id = ? ORDER BY q.snapshot_mes DESC, q.papel, p.nome
        LIMIT ?""", (aid, LIMITES_RELACIONADOS))
    reg["totais"] = _linha_1(conn, """
        SELECT (SELECT COUNT(*) FROM participacao WHERE aeronave_id = ?) AS vinculos,
               (SELECT COUNT(*) FROM participacao WHERE aeronave_id = ? AND papel='PROPRIETARIO') AS proprietarios,
               (SELECT COUNT(*) FROM participacao WHERE aeronave_id = ? AND papel='OPERADOR') AS operadores,
               (SELECT COUNT(DISTINCT snapshot_mes) FROM participacao WHERE aeronave_id = ?) AS meses""",
                            (aid, aid, aid, aid))
    reg["sobreposicoes"] = _overrides(conn, "aeronave", aid)
    reg["auditoria"] = _auditoria(conn, "aeronave", aid)
    reg["key"] = aid
    return reg


def detalhe_aerodromo(conn, icao: str) -> dict | None:
    reg = _linha_1(conn, "SELECT * FROM v_aerodromo_registro WHERE icao = ?", (icao,))
    if reg is None:
        return None
    reg["redemet_status"] = _linha_1(
        conn, "SELECT * FROM redemet_aerodromo_status WHERE icao = ?", (icao,))
    reg["redemet_mensagens"] = _lista(conn, """
        SELECT tipo, validade_inicial, validade_final, mensagem, recebimento, colhido_em
        FROM redemet_mensagem WHERE icao = ?
        ORDER BY validade_inicial DESC LIMIT 40""", (icao,))
    reg["mensagens_por_tipo"] = _lista(conn, """
        SELECT tipo, COUNT(*) AS n FROM redemet_mensagem WHERE icao = ? GROUP BY tipo""",
                                       (icao,))
    reg["sobreposicoes"] = _overrides(conn, "aerodromo", icao)
    reg["auditoria"] = _auditoria(conn, "aerodromo", icao)
    reg["key"] = icao
    return reg


def detalhe_fabricante(conn, fid: int) -> dict | None:
    # Colunas explícitas, não `v.*`: a view também traz `id`, e `SELECT f.id AS id,
    # v.*` deixa dois `id` na descrição — o `zip` casaria o nome certo com o
    # valor errado, e o detalhe abriria a pessoa errada.
    reg = _linha_1(conn, """
        SELECT f.id AS id, f.pessoa_id AS pessoa_id, f.org_codigo AS org_codigo,
               f.org_nabrev AS org_nabrev, v.nome AS nome, v.chave AS chave,
               v.natureza AS natureza, v.nome_fonte AS nome_fonte,
               v.documento AS documento, v.documento_bruto AS documento_bruto,
               v.documento_mascarado AS documento_mascarado, v.uf AS uf,
               v.razao_social AS razao_social, v.nome_fantasia AS nome_fantasia,
               v.site AS site, v.email AS email, v.telefone AS telefone,
               v.logo_url AS logo_url, v.end_logradouro AS end_logradouro,
               v.end_numero AS end_numero, v.end_complemento AS end_complemento,
               v.end_bairro AS end_bairro, v.end_cep AS end_cep,
               v.end_municipio AS end_municipio, v.end_uf AS end_uf,
               v.end_pais AS end_pais,
               v.origem_societario AS origem_societario,
               v.origem_contato AS origem_contato,
               v.editado_em AS editado_em, v.editado_por AS editado_por
        FROM fabricante f JOIN v_pessoa_registro v ON v.id = f.pessoa_id
        WHERE f.id = ?""", (fid,))
    if reg is None:
        return None
    reg["extra"] = _linha_1(conn, "SELECT * FROM pessoa_extra WHERE pessoa_id = ?",
                            (reg["pessoa_id"],))
    reg["modelos"] = _lista(conn, """
        SELECT mo.id, mo.ds_modelo, mo.cd_tipo, m.nome AS marca,
               (SELECT COUNT(*) FROM aeronave a WHERE a.modelo_id = mo.id) AS n_aeronaves
        FROM modelo mo LEFT JOIN marca m ON m.id = mo.marca_id
        WHERE mo.fabricante_id = ? ORDER BY mo.ds_modelo LIMIT ?""",
                            (fid, LIMITES_RELACIONADOS))
    reg["marcas"] = _lista(conn, """
        SELECT m.id, m.nome,
               (SELECT COUNT(*) FROM modelo mo WHERE mo.marca_id = m.id) AS n_modelos
        FROM marca m WHERE m.id IN (SELECT marca_id FROM modelo WHERE fabricante_id = ?)
        ORDER BY m.nome LIMIT ?""", (fid, LIMITES_RELACIONADOS))
    reg["certificada"] = _linha_1(
        conn, "SELECT * FROM org_producao WHERE REPLACE(REPLACE(REPLACE("
              "cnpj,'.',''),'/',''),'-','') = ?", (reg.get("documento"),))
    reg["totais"] = _linha_1(conn, """
        SELECT (SELECT COUNT(*) FROM modelo WHERE fabricante_id = ?) AS modelos,
               (SELECT COUNT(*) FROM aeronave WHERE fabricante_id = ?) AS aeronaves""",
                            (fid, fid))
    reg["auditoria"] = _auditoria(conn, "pessoa_extra", reg["pessoa_id"])
    reg["key"] = fid
    return reg


def detalhe_modelo(conn, mid: int) -> dict | None:
    reg = _linha_1(conn, """
        SELECT mo.*, m.nome AS marca, f.id AS fabricante_id,
               p.nome AS fabricante_nome, pe.razao_social AS fabricante_razao,
               f.org_codigo, f.org_nabrev
        FROM modelo mo LEFT JOIN marca m ON m.id = mo.marca_id
        LEFT JOIN fabricante f ON f.id = mo.fabricante_id
        LEFT JOIN pessoa p ON p.id = f.pessoa_id
        LEFT JOIN pessoa_extra pe ON pe.pessoa_id = f.pessoa_id WHERE mo.id = ?""", (mid,))
    if reg is None:
        return None
    reg["aeronaves"] = _lista(conn, """
        SELECT a.id,
               """ + MATRICULA_SQL + """ AS matricula,
               """ + NUMERO_SERIE_SQL + """ AS numero_serie,
               a.nr_serie, a.nr_ano_fabricacao,
               nm_fabricante, tp_operacao, snapshot_mes
        FROM aeronave a WHERE a.modelo_id = ?
        ORDER BY """ + _ordem_matricula() + """, a.nr_serie, a.id LIMIT ?""",
                             (mid, LIMITES_RELACIONADOS))
    reg["classes"] = _lista(conn, """
        SELECT cd_classe AS classe, COUNT(*) AS n FROM aeronave WHERE modelo_id = ?
        GROUP BY cd_classe ORDER BY n DESC""", (mid,))
    reg["op_por_mes"] = _lista(conn, """
        SELECT snapshot_mes, COUNT(*) AS n FROM aeronave WHERE modelo_id = ?
        GROUP BY snapshot_mes ORDER BY snapshot_mes DESC LIMIT 12""", (mid,))
    reg["totais"] = _linha_1(conn, "SELECT COUNT(*) AS n FROM aeronave WHERE modelo_id = ?", (mid,))
    reg["key"] = mid
    return reg


def detalhe_marca(conn, nome: str) -> dict | None:
    reg = _linha_1(conn, "SELECT * FROM marca WHERE nome = ?", (nome,))
    if reg is None:
        return None
    reg["modelos"] = _lista(conn, """
        SELECT mo.id, mo.ds_modelo, mo.cd_tipo, f.org_nabrev,
               p.nome AS fabricante,
               (SELECT COUNT(*) FROM aeronave a WHERE a.modelo_id = mo.id) AS n_aeronaves
        FROM modelo mo LEFT JOIN fabricante f ON f.id = mo.fabricante_id
        LEFT JOIN pessoa p ON p.id = f.pessoa_id
        WHERE mo.marca_id = ? ORDER BY n_aeronaves DESC, mo.ds_modelo LIMIT ?""",
                             (reg["id"], LIMITES_RELACIONADOS))
    reg["fabricantes"] = _lista(conn, """
        SELECT f.id, f.org_nabrev, p.nome, pe.razao_social,
               (SELECT COUNT(*) FROM modelo mo WHERE mo.marca_id = ? AND mo.fabricante_id = f.id) AS n_modelos
        FROM modelo mo JOIN fabricante f ON f.id = mo.fabricante_id
        JOIN pessoa p ON p.id = f.pessoa_id
        LEFT JOIN pessoa_extra pe ON pe.pessoa_id = f.pessoa_id
        WHERE mo.marca_id = ? GROUP BY f.id ORDER BY n_modelos DESC LIMIT ?""",
                              (reg["id"], reg["id"], LIMITES_RELACIONADOS))
    reg["top_aeronaves"] = _lista(conn, """
        SELECT a.id,
               """ + MATRICULA_SQL + """ AS matricula,
               """ + NUMERO_SERIE_SQL + """ AS numero_serie,
               a.ds_modelo, a.nm_fabricante, a.snapshot_mes
        FROM aeronave a WHERE a.marca = ?
        ORDER BY """ + _ordem_matricula() + """, a.nr_serie, a.id LIMIT ?""",
                                  (nome, LIMITES_RELACIONADOS))
    reg["aeronaves_por_mes"] = _lista(conn, """
        SELECT snapshot_mes, COUNT(*) AS n FROM aeronave WHERE marca = ?
        GROUP BY snapshot_mes ORDER BY snapshot_mes DESC LIMIT 12""", (nome,))
    reg["totais"] = _linha_1(conn, """
        SELECT (SELECT COUNT(*) FROM modelo WHERE marca_id = ?) AS modelos,
               (SELECT COUNT(*) FROM aeronave WHERE marca = ?) AS aeronaves""",
                            (reg["id"], nome))
    reg["key"] = nome
    return reg


def detalhe_drone(conn, codigo: str) -> dict | None:
    reg = _linha_1(conn, """
        SELECT r.*, p.nome AS responsavel, p.documento AS responsavel_documento,
               p.natureza AS responsavel_natureza, p.uf AS resp_uf
        FROM registro_sisant r LEFT JOIN pessoa p ON p.id = r.pessoa_id
        WHERE r.codigo_aeronave = ?""", (codigo,))
    if reg is None:
        return None
    reg["mesmo_fabricante"] = _lista(conn, """
        SELECT COUNT(*) AS n FROM registro_sisant WHERE fabricante_nome = ?""",
                                     (reg.get("fabricante_nome"),))
    reg["mesmo_modelo"] = _linha_1(conn, """
        SELECT COUNT(*) AS n, AVG(peso_max_kg) AS peso_medio FROM registro_sisant
        WHERE modelo_nome = ?""", (reg.get("modelo_nome"),))
    reg["ultimos"] = _lista(conn, """
        SELECT codigo_aeronave, data_validade, responsavel_nome FROM (
            SELECT r.codigo_aeronave, r.data_validade, p.nome AS responsavel_nome
            FROM registro_sisant r LEFT JOIN pessoa p ON p.id = r.pessoa_id
            WHERE r.fabricante_nome = ? AND r.codigo_aeronave <> ?
            ORDER BY r.codigo_aeronave LIMIT 20) ORDER BY codigo_aeronave DESC""",
                                 (reg.get("fabricante_nome"), codigo))
    reg["key"] = codigo
    return reg


def detalhe_vinculo(conn, vid: int) -> dict | None:
    reg = _linha_1(conn, """
        SELECT q.*,
               """ + MATRICULA_SQL + """ AS matricula, a.ds_modelo AS modelo,
               a.nm_fabricante, a.cd_tipo_icao, a.tp_operacao,
               a.snapshot_mes AS mes_aeronave,
               p.nome AS pessoa_nome, p.natureza, p.documento, p.uf AS pessoa_uf,
               e.razao_social
        FROM participacao q JOIN aeronave a ON a.id = q.aeronave_id
        JOIN pessoa p ON p.id = q.pessoa_id
        LEFT JOIN pessoa_extra e ON e.pessoa_id = p.id WHERE q.id = ?""", (vid,))
    if reg is None:
        return None
    reg["aeronave"] = detalhe_aeronave(conn, reg["aeronave_id"])
    reg["outros_papeis"] = _lista(conn, """
        SELECT q.id, q.papel, q.percentual, q.snapshot_mes, q.uf,
               p.nome AS pessoa_nome, p.natureza, p.id AS pessoa_id
        FROM participacao q JOIN pessoa p ON p.id = q.pessoa_id
        WHERE q.aeronave_id = ? AND q.id <> ?
        ORDER BY q.snapshot_mes DESC, q.papel LIMIT 40""",
                                    (reg["aeronave_id"], vid))
    reg["mesma_pessoa_outros"] = _lista(conn, """
        SELECT q.id, q.papel, q.percentual, q.snapshot_mes,
               """ + MATRICULA_SQL + """ AS matricula, a.ds_modelo AS modelo
        FROM participacao q JOIN aeronave a ON a.id = q.aeronave_id
        WHERE q.pessoa_id = ? AND q.id <> ?
        ORDER BY q.snapshot_mes DESC, a.nr_cert_matricula LIMIT 40""",
                                       (reg["pessoa_id"], vid))
    reg["regra"] = (
        "RBAC 45.12-I(a): uma pessoa só pode operar uma aeronave em operações pelo "
        "RBAC 135 se na aeronave estiver inscrita TRANSPORTE PÚBLICO. "
        "`transp_reg_135 = 'N'` é exatamente essa inscrição ausente.")
    reg["key"] = vid
    return reg


def detalhe_organizacao(conn: sqlite3.Connection, pid: int) -> dict | None:
    """Uma organização com os dois papéis: empresa e/ou fabricante.

    Reaproveita `detalhe_fabricante` quando existe vínculo em `fabricante`, e o
    cadastro de empresa quando não. Sem isso, clicar numa linha da aba
    "Fabricantes" cairia num detalhe de fabricante sem o CNPJ, e o da aba
    "Empresas" num detalhe sem os modelos.
    """
    fab = _linha_1(conn, """
        SELECT f.id, f.org_codigo, f.org_nabrev FROM fabricante f
        WHERE f.pessoa_id = ? LIMIT 1""", (pid,))
    if fab is not None:
        reg = detalhe_fabricante(conn, fab["id"]) or {}
    else:
        reg = detalhe_empresa(conn, pid) or {}
    if not reg:
        return None
    pessoa = _linha_1(conn, """
        SELECT natureza, eh_fabricante FROM v_pessoa_registro WHERE id = ?""",
                   (pid,)) or {}
    reg["tipo"] = ("empresa e fabricante" if fab and pessoa.get("natureza") == "JURIDICA"
                   else "fabricante" if fab else "empresa")
    reg["key"] = pid
    reg["nav"] = pid
    reg["id"] = pid
    return reg


DETALHES = {
    "empresa": lambda c, k: detalhe_empresa(c, int(k)),
    "usuario": lambda c, k: detalhe_usuario(c, int(k)),
    "aeronave": lambda c, k: detalhe_aeronave(c, int(k)),
    "aerodromo": detalhe_aerodromo,
    "fabricante": lambda c, k: detalhe_fabricante(c, int(k)),
    "modelo": lambda c, k: detalhe_modelo(c, int(k)),
    "marca": detalhe_marca,
    "drone": detalhe_drone,
    "vinculo": lambda c, k: detalhe_vinculo(c, int(k)),
    "organizacao": lambda c, k: detalhe_organizacao(c, int(k)),
}


def detalhe(conn: sqlite3.Connection, nome: str, chave: str) -> dict | None:
    fn = DETALHES.get(nome)
    if fn is None:
        raise LookupError(f"catalogo desconhecido: {nome}")
    try:
        return fn(conn, chave)
    except (TypeError, ValueError):
        # Chave nao numerica em catalogo cujo id e numerico: e erro de rota, nao
        # de banco, e vira 404 na camada HTTP em vez de 500.
        return None


def contagens(conn: sqlite3.Connection) -> list[dict]:
    """Total de cada catalogo, para o contador ao lado do item do menu.

    `organizacao` não entra: a lista de organizações é a união de empresas e
    fabricantes, e o contador dela viria da mesma consulta da tela. Publicar os
    dois números aqui mostraria duas vezes a mesma informação.
    """
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    return [
        {"catalogo": "empresa", "n": q("SELECT COUNT(*) FROM pessoa WHERE natureza='JURIDICA'")},
        {"catalogo": "usuario", "n": q("SELECT COUNT(*) FROM pessoa WHERE natureza='FISICA'")},
        {"catalogo": "aeronave", "n": q("SELECT COUNT(*) FROM aeronave")},
        {"catalogo": "fabricante", "n": q("SELECT COUNT(*) FROM fabricante")},
        {"catalogo": "modelo", "n": q("SELECT COUNT(*) FROM modelo")},
        {"catalogo": "marca", "n": q("SELECT COUNT(*) FROM marca")},
        {"catalogo": "aerodromo", "n": q("SELECT COUNT(*) FROM aerodromo")},
        {"catalogo": "drone", "n": q("SELECT COUNT(*) FROM registro_sisant")},
        {"catalogo": "vinculo", "n": q("SELECT COUNT(*) FROM participacao")},
    ]