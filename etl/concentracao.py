"""Análise de concentração de propriedade da frota.

A pergunta é quem detém a frota brasileira de aeronaves e o tanto que a
titularidade está concentrada. Quatro decisões de método mudam o número, e
todas ficam explícitas aqui porque nenhuma é neutra.

**1. A identidade é resolvida em dois níveis.** A base se fragmenta por
grafia: `AIR TRACTOR CAPITAL, LLC.` aparece em seis variantes que somam 215
aeronaves, `WELLS FARGO BANK NORTHWEST` em três. O HHI é calculado sobre o
nome normalizado (acentos e espaços) e de novo sobre a chave de radicais, e
os dois números saem lado a lado. A segunda chave só funde quando os
conjuntos de radicais são **iguais** — `AIR TRACTOR` e `AIR TRACTOR CAPITAL`
são empresas diferentes e continuam separadas.

**2. O HHI principal é por frota, não por percentual declarado.** A série
incomparável mais óbvia seria esta: a era A (2025-09..2026-01) não tem campo
de percentual nenhum, e a era B de 2026-02 preenche quase todos. Somar
percentuais que existem em uns meses e não em outros produz uma série que
sobe de 12 para 14 sem que nada tenha acontecido na frota. Por isso o HHI
de referência é o da fatia de frota (aeronaves por dono sobre a frota do
mês), que existe nos doze meses. O HHI por percentual declarado é reportado
à parte, sempre com a cobertura daquele mês.

**3. O HHI relevante é o do mercado financiável.** A EMBRAER é a maior
proprietária do país, com cerca de 880 aeronaves, mas são aeronaves de
fábrica que ela vende. Somá-la ao HHI mede integração vertical, não
concorrência. O HHI de crédito exclui governo e fabricante e responde à
pergunta que interessa: se uma transportadora quisesse uma aeronave
financiada, de quantos financiadores ela dependeria.

**4. Titular não é o mesmo que financiar, e a fonte não diz.** Um veículo de
securitização é um dono que não opera aquela aeronave. O teste é por
aeronave, não por empresa: em 2026-03 a ANAC lista `BRADESCO LEASING S.A
ARREND.MERCANTIL` como operador de três aeronaves, e a leitura por empresa
reclassificava as 383 que a empresa detém. A cauda mostrada aqui é essa
população; a checagem por nome (`LEASING`, `TRUST`, `NATIONAL ASSOCIATION`)
confirma, não decide.

**O que não é comparável entre meses, e por isso não vira gráfico.** O
recorte por segmento depende de `pessoa.natureza`, e `classifica_documento`
devolve `JURIDICA` quando o documento não vem. Na era A os proprietários
secundários aparecem só como nome, sem documento, então pessoa física sem
documento é arquivada como empresa: a fatia de PF cai de 42,8% (2026-09) para
39,2% (2026-01) por causa do formato, não da frota. Por isso a série mensal
traz só HHI, Gini, CR4 e titularização, que não dependem de natureza; o
recorte por segmento é lido no mês mais recente.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

MES = "2026-09"
_NAO_PESSOA = "('Indisponível', 'Indisponivel', 'DESCONHECIDO', 'N/I')"

# --- Identidade -------------------------------------------------------------

# A ANAC escreve o mesmo nome de formas diferentes conforme a fonte e o ano.
# Canonicalizar antes de tokenizar é o que funde `WELLS FARGO ... N.A.` com
# `WELLS FARGO ... NAT.ASSOC`, que de outro modo contam como dois donos.
ABREVIATURAS = [
    (re.compile(r"\bNAT\.?\s*ASSOC\.?\b", re.I), "NAT"),
    (re.compile(r"\bNAT'\s*ASSOC\.?\b", re.I), "NAT"),
    (re.compile(r"\bN\s*\.\s*A\s*\.", re.I), "NAT"),
    (re.compile(r"\bS\s*\.\s*A\s*\.", re.I), "SA"),
    (re.compile(r"\bS\s*/\s*A\b", re.I), "SA"),
    (re.compile(r"\bLTDA\.?\b", re.I), "LTDA"),
    (re.compile(r"\bLLC\.?\b", re.I), "LLC"),
    (re.compile(r"\bCORPORATION\b", re.I), "CORP"),
    (re.compile(r"\bINC\.?\b", re.I), "INC"),
]

# Formas jurídicas e conectivos: são de constituição, não de identidade.
_RADICAIS_VAZIOS = {
    "S", "A", "SA", "LTDA", "ME", "EIRELI", "EPP", "LLC", "LIMITED", "LTD",
    "INC", "CORP", "CORPORATION", "NV", "SPA", "GMBH", "AG", "CO", "THE",
    "OF", "DO", "DA", "DOS", "DAS", "E", "AS", "OS",
}

# Designador de série ou emissão: `SFG EQUIPMENT LEASING CORPORATION I`,
    # `COMMUTER AIRCRAFT LEASING 2017 III LIMITED`. Sao series do mesmo
_RE_SERIE = re.compile(r"\s+(?:\d{4}\s+)?[IVX]{1,6}$")


def norm(s: str | None) -> str:
    """Maiúsculas, sem acento, espaços colapsados. Não funde grafias."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def _canonico(s: str | None) -> str:
    t = norm(s)
    for rx, sub in ABREVIATURAS:
        t = rx.sub(sub, t)
    return t


def radicais(s: str | None) -> frozenset[str]:
    """Conjunto de palavras significativas, sem forma jurídica.

    Só funde nomes cujos conjuntos são iguais. Subconjuntos não fundem:
    `AIR TRACTOR` está contido em `AIR TRACTOR CAPITAL`, mas são a
    fabricante e o braço de financiamento dela, não a mesma empresa.
    """
    t = re.sub(r"[^A-Z0-9 ]", " ", _canonico(s))
    return frozenset(w for w in t.split() if w not in _RADICAIS_VAZIOS)


def raiz_pool(s: str | None) -> str:
    """Nome sem forma jurídica nem série, para achar o pool de securitização."""
    t = _RE_SERIE.sub("", re.sub(r"[^A-Z0-9 ]", " ", _canonico(s)))
    return " ".join(w for w in t.split() if w not in _RADICAIS_VAZIOS)


# --- Segmentação ------------------------------------------------------------

# Indicadores de atividade financeira. Forma jurídica de propósito: `S.A.`,
# `LTDA` e `LLC` são constituição, e incluí-los inflaria a categoria com
# qualquer empresa grande.
RE_FINANCEIRO = re.compile(
    # `BANCO` ao lado de `BANK`: banco brasileiro se escreve em portugues, e
# sem isso nenhum BANCO BRADESCO, BANCO SAFRA ou BANCO DO BRASIL era
# reconhecido como credito pelo nome.
    r"LEASING|ARREND|FIDC|FUNDI|CREDITORA|TRUST|FINANC|CAPITAL|"
    r"ASSET|GAP\s+NAT|BANK|BANCO", re.I)

RE_GOVERNO = re.compile(
    # `AERONAUTICA` saiu de proposito: ela e o nome da fabrica, nao do
    # orgao. "EMBRAER EMP BRAS. DE AERONAUTICA" (114 aeronaves) e "SCODA
    # AERONAUTICA FABR." eram classificados como governo por causa disso. Os
    # ministerios continuam pegos por `MINISTERIO`.
    r"GOVERNO|UNIAO|MINISTERIO|MINISTER|FEDERAL|ANAC|\bDAC\b|EXERCITO|"
    r"POLICIA|PREFEITURA|DISTRITO|INFOPLANEJO|CODEVASF|"
    r"DEPARTAMENTO|SENAD|ANTIDROGAS", re.I)

# Braco financeiro de fabricante: credor, nao frota captive. Vem antes de
# RE_FABRICANTE, senao EMBRAER FINANCE (17 aeronaves) entra como
# fabricante e some do HHI de credito sem motivo.
RE_BRACO_FINANCEIRO = re.compile(r"FINANCE|FINANCIAL|FINANC", re.I)

# Banco de custódia americano estructures que aparecem como titular de frota
# brasileira: `BANK OF UTAH`, `UMB BANK, NATIONAL ASSOCIATION`.
RE_TITULAR = re.compile(
    r"LEASING|ARREND|TRUST|CAPITAL|FINANC|FIDC|FUNDI|CREDITADORA|"
    r"NATIONAL\s+(NAT\s+)?ASSOC|CONSORCIO", re.I)

def _grupo_vazio(nome, natureza, uf, pool):
    return {
        "nome": nome, "nomes": set(), "natureza": natureza, "uf": uf, "pool": pool,
        "pessoa_ids": set(), "por_nome": {}, "aeronaves": set(), "fatia": 0.0,
        "links": 0, "links_com_pct": 0, "opera": False,
        "titular_aeronaves": set(),
    }


def _grupos(conn, mes: str, por_radical: bool = False) -> list[dict]:
    """Proprietários do mês, agregados por identidade.

    `por_radical=True` funde grafias pelo conjunto de radicais; o padrão é o
    nome normalizado. Os dois modos são calculados para que a diferença
    entre eles fique visível em vez de escondida numa escolha.
    """
    fab = {radicais(r[0]) for r in conn.execute(
        "SELECT p.nome FROM fabricante f JOIN pessoa p ON p.id = f.pessoa_id")
        if r[0] and radicais(r[0])}

    linhas = conn.execute(f"""
        SELECT s.id, s.nome, s.natureza, s.uf, p.percentual, p.aeronave_id
        FROM participacao p JOIN pessoa s ON s.id = p.pessoa_id
        WHERE p.papel='PROPRIETARIO' AND p.snapshot_mes=?
          AND s.nome NOT IN {_NAO_PESSOA}
    """, (mes,)).fetchall()

    # Operadores indexados por aeronave, casados por radical e não por nome
    # exato: `AIR TRACTOR, INC.` e `AIR TRACTOR INC` são o mesmo operador.
    #
    # A indexação é por aeronave, e não por empresa, porque "ser titular" é uma
    # relacao entre dois registros da mesma aeronave. Testar por empresa
    # resultado absurdo: em 2026-03 a ANAC lista `BRADESCO LEASING S.A
    # ARREND.MERCANTIL` como operador de 3 aeronaves, e a leitura por empresa
    # reclassificava as 383 que a empresa detém como frota própria dela.
    ops_por_aeronave: dict[int, set] = {}
    for aero_id, nome in conn.execute(f"""
        SELECT p.aeronave_id, s.nome FROM participacao p JOIN pessoa s ON s.id = p.pessoa_id
        WHERE p.papel='OPERADOR' AND p.snapshot_mes=?
          AND s.nome NOT IN {_NAO_PESSOA}""", (mes,)):
        if nome:
            ops_por_aeronave.setdefault(aero_id, set()).add(radicais(nome))
    operadores = {r for rs in ops_por_aeronave.values() for r in rs}

    grupos: dict = {}
    # Grafias distintas que caem no mesmo conjunto de radicais. Sem isso a
    # coluna "grafias" da cauda sairia sempre 1, porque o agrupamento padrão
    # é por nome normalizado e não funde `AIR TRACTOR CAPITAL, LLC` no
    # `AIR TRACTOR CAPITAL LLC`, que e o que a secao precisa mostrar.
    grafias_por_radical: dict = {}
    for pid, nome, natureza, uf, percentual, aero_id in linhas:
        grafias_por_radical.setdefault(radicais(nome), set()).add(norm(nome))
    for pid, nome, natureza, uf, percentual, aero_id in linhas:
        chave = radicais(nome) if por_radical else norm(nome)
        g = grupos.get(chave)
        if g is None:
            g = grupos[chave] = _grupo_vazio(
                nome, natureza, uf, raiz_pool(nome))
        g["nomes"].add(nome)
        g["pessoa_ids"].add(pid)
        # Grafia -> id. Uma grafia pode ter mais de um `pessoa.id` quando a
        # fonte repetiu o mesmo nome com documentos diferentes; a menor chave
        # é a mais estável entre recargas do ETL.
        anterior = g["por_nome"].get(nome)
        if anterior is None or pid < anterior:
            g["por_nome"][nome] = pid
        g["aeronaves"].add(aero_id)
        g["links"] += 1
        g["fatia"] += (percentual if percentual is not None else 0.0)
        if percentual is not None:
            g["links_com_pct"] += 1
        # Titulariza esta aeronave se nenhuma grafia do dono a opera.
        da_aeronave = ops_por_aeronave.get(aero_id)
        if not da_aeronave or not (radicais(nome) in da_aeronave):
            g["titular_aeronaves"].add(aero_id)
    for g in grupos.values():
        g["opera"] = any(radicais(n) in operadores for n in g["nomes"])
        g["titular_n"] = len(g["titular_aeronaves"])
        g["variantes"] = len(grafias_por_radical.get(radicais(g["nome"]), ()))
        g["tipo"] = tipificar(g["nome"], g["natureza"], g, fab)
    return list(grupos.values())


#só as fabricantes que de fato operam frota própria relevante no Brasil.
# `CESSNA FINANCE` e `TEXTRON FINANCIAL` ficaram de fora de propósito: o
# braço financeiro de um fabricante é credor, não frota captive. E a tabela
# `fabricante` do banco não entra aqui: ela diz quem produziu a aeronave, e
# quem produziu qualquer coisa -- um aeroclube com um balao -- nao e quem
# frota. Usá-la jogava 2.741 aeronaves em "Fabricante" em 2026-02 contra
# 1.500 nos outros meses, e o HHI de crédito ia junto.
RE_FABRICANTE = re.compile(
    r"EMBRAER|HELIBRAS|HELICOPTEROS DO BRASIL|"
    # `[^A-Z]{0,3}` absorve a pontuação entre o nome e o sufixo: a fonte
    # escreve `AIR TRACTOR INC.` e `AIR TRACTOR, INC`, e as duas precisam
    # cair na mesma categoria, senão a fabricante vira locadora na cauda.
    # O lookahead nega `CAPITAL`: a fabricante e o braco de financiamento
    # dela sao homonimos, e so um dos dois guarda frota propria.
    r"\bAIR\s*TRACTOR(?!\s+CAPITAL)[^A-Z]{0,3}(INC|CORP)?", re.I)


def tipificar(nome: str, natureza: str, grupo: dict, radicais_fab: set) -> str:
    """Segmento do proprietário.

    A ordem importa. Pessoa física é identidade, não atividade, e sai
    primeiro. Governamental e fabricante vêm antes de titularização porque
    nenhum dos dois disputa o mercado de aquisição financiada — é por isso
    que eles saem do HHI de crédito.

    "Titularização" usa a contagem por aeronave, não a preposição da empresa:
    # uma locadora que tambem opera tres nao deixa de ser titular das 380.
    """
    if natureza == "FISICA":
        return "Pessoa física"
    n = norm(nome)
    if RE_GOVERNO.search(n):
        return "Governamental"
    if RE_BRACO_FINANCEIRO.search(n):
        return "Financeiro / crédito"
    if RE_FABRICANTE.search(n):
        return "Fabricante"
    if grupo["titular_n"] >= len(grupo["aeronaves"]):
        # Não opera nenhuma das aeronaves que detém: só pode ser título.
        return "Titularização"
    if RE_FINANCEIRO.search(n):
        return "Financeiro / crédito"
    return "Transportadora / outra"


# --- Índices de concentração ------------------------------------------------


def hhi(valores, pesos=None) -> float:
    """Herfindahl-Hirschman sobre uma distribuição de fatias, em pontos 0-10000.

    Abaixo de 1.500 o mercado é considerado desconcentrado, de 1.500 a 2.500
    moderadamente concentrado e acima de 2.500 concentrado (FTC/DOJ). A
    escala é de pontos, não de fração: 0,01 vira 1 ponto, não 0,0001.
    """
    if not valores:
        return 0.0
    if pesos is None:
        pesos = valores
    total = sum(pesos) or 1.0
    return round(sum((v / total * 100) ** 2 for v in valores), 2)


def gini(valores) -> float:
    """Gini de uma distribuição de fatias. 0 = perfeito, ~1 = monopólio."""
    vals = sorted(v for v in valores if v > 0)
    n = len(vals)
    if n < 2:
        return 0.0
    total = sum(vals)
    if total == 0:
        return 0.0
    acc = 0.0
    for i, v in enumerate(vals, 1):
        acc += i * v
    return round((2 * acc) / (n * total) - (n + 1) / n, 4)


def lorenz(grupos: list[dict], campo="aeronaves_n") -> list[dict]:
    """Curva de Lorenz: proporção acumulada da frota contra proporção de donos.

    Ordena os proprietários do maior para o menor e devolve os pontos
    acumulados. A diagonal é a distribuição perfeita; a distância entre a
    curva e a diagonal é o que o Gini mede.
    """
    fatias = sorted((g[campo] for g in grupos), reverse=True)
    total = sum(fatias) or 1.0
    n = len(fatias)
    pontos, acc = [{"x": 0.0, "y": 0.0}], 0.0
    for i, f in enumerate(fatias, 1):
        acc += f
        pontos.append({"x": round(i / n, 6), "y": round(acc / total, 6)})
    return pontos


def cr(grupos: list[dict], k: int) -> float:
    """Concentração acumulada nos `k` maiores, em % da frota."""
    if not grupos:
        return 0.0
    total = sum(g["aeronaves_n"] for g in grupos) or 1
    return round(sum(g["aeronaves_n"] for g in grupos[:k]) / total * 100, 2)


def _amostra(pontos: list[dict], n: int) -> list[dict]:
    """Reduz a curva a `n` pontos, guardando as duas pontas.

    A curva tem um ponto por proprietário — 21 mil em 2026-09. Numa tela de
    700 pixels o excesso é invisível, e o Gini já foi calculado antes, sobre
    a lista inteira. A redução é só para o desenho.
    """
    if len(pontos) <= n:
        return pontos
    passo = (len(pontos) - 1) / (n - 1)
    return [pontos[round(i * passo)] for i in range(n)]


def _meses(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT mes FROM snapshot ORDER BY mes")]


def _frota(conn, mes: str) -> tuple[int, int]:
    """(frota do mês, aeronave que tem pelo menos um proprietário).

    A primeira vem do snapshot, que é a contagem declarada do arquivo. A
    segunda é o denominador honesto das fatias: uma aeronave sem proprietário
    registrado não pode aparecer na curva de ninguém.
    """
    frota = conn.execute("SELECT contagem FROM snapshot WHERE mes=?", (mes,)).fetchone()
    frota = frota[0] if frota else 0
    com_dono = conn.execute(
        "SELECT COUNT(DISTINCT p.aeronave_id) FROM participacao p "
        "JOIN pessoa s ON s.id=p.pessoa_id "
        "WHERE p.papel='PROPRIETARIO' AND p.snapshot_mes=? "
        f"AND s.nome NOT IN {_NAO_PESSOA}", (mes,)).fetchone()[0]
    return frota, com_dono


def _prepara(grupos: list[dict], frota_com_dono: int) -> None:
    for g in grupos:
        g["aeronaves_n"] = len(g["aeronaves"])
    grupos.sort(key=lambda g: (-g["aeronaves_n"], norm(g["nome"])))
    for g in grupos:
        g["pct_frota"] = round(g["aeronaves_n"] / frota_com_dono * 100, 3) if frota_com_dono else 0.0


def _indice(grupos: list[dict], frota_com_dono: int, com_lorenz: bool = True) -> dict:
    """HHI e curva sobre uma lista de grupos já preparada."""
    valores = [g["aeronaves_n"] for g in grupos]
    # O HHI de crédito é o mesmo índice sem governo e sem fabricante:
    # são os dois que não competem no mercado de aquisição financiada.
    credito = [g for g in grupos
               if g["tipo"] not in ("Governamental", "Fabricante")]
    declarados = [g for g in grupos if g["links_com_pct"] > 0]
    return {
        "hhi_frota": hhi(valores),
        "hhi_credito": hhi([g["aeronaves_n"] for g in credito]),
        "hhi_declarado": hhi([g["fatia"] for g in declarados]),
        "cobertura_pct": round(
            sum(g["links_com_pct"] for g in grupos) / max(sum(g["links"] for g in grupos), 1) * 100, 1),
        "gini": gini(valores),
        "cr4": cr(grupos, 4),
        "cr10": cr(grupos, 10),
        "top1": grupos[0]["pct_frota"] if grupos else 0.0,
        # A curva tem um ponto por dono: 21 mil pontos x 12 meses nao cabe
        # numa serie mensal, e so o mes corrente precisa dela.
        "curva_lorenz": _amostra(lorenz(grupos), 360) if com_lorenz else [],
    }


def _metade_em(grupos: list[dict]) -> int:
    """Quantos donos, do maior para o menor, somam metade das participações."""
    total = sum(g["aeronaves_n"] for g in grupos)
    if not total:
        return 0
    acc = 0
    for i, g in enumerate(grupos, 1):
        acc += g["aeronaves_n"]
        if acc >= total * 0.5:
            return i
    return len(grupos)


def analise(conn, mes: str = MES, completo: bool = True) -> dict:
    """Concentração de propriedade em um mês.

    Calcula tudo sobre duas identidades: nome normalizado e chave de
    radicais. A diferença entre as duas é a fragmentação que a grafia impõe.

    `completo=False` pula a curva de Lorenz e as listas nominais. A série
    mensal usa esse caminho: os índices são os mesmos, e a carga cai de
    meio milhão de pontos de curva para zero.
    """
    frota, frota_com_dono = _frota(conn, mes)
    if not frota_com_dono:
        return {}

    base = _grupos(conn, mes, por_radical=False)
    fundido = _grupos(conn, mes, por_radical=True)
    _prepara(base, frota_com_dono)
    _prepara(fundido, frota_com_dono)

    idx_base = _indice(base, frota_com_dono, completo)
    idx_fund = _indice(fundido, frota_com_dono, completo)

    por_tipo: dict[str, int] = {}
    for g in base:
        por_tipo[g["tipo"]] = por_tipo.get(g["tipo"], 0) + g["aeronaves_n"]
    tipo_pct = {k: round(v / frota_com_dono * 100, 2) for k, v in por_tipo.items()}

    # A cauda de securitização, em duas medidas que não são a mesma.
    #
    # `titularizacao` conta aeronaves: quantas estão registradas em nome de
    # alguém que não as opera. É o número da estrutura de titularização.
    #
    # `titulares` conta entidades: quantos donos não operam nenhuma das
    # aeronaves que detêm. É o número da cauda de veículos.
    #
    # Uma locadora que também opera três nao entra em `titulares`, mesmo
    # sendo titular das outras 380. Por isso os dois números somam coisas
    # diferentes e aparecem separados.
    titularizacao = sum(g["titular_n"] for g in base)
    titulares = [g for g in base if g["tipo"] == "Titularização"]
    titulares.sort(key=lambda g: -g["aeronaves_n"])
    # Colapsa séries de um mesmo pool (`... CORPORATION I`, `... 2017 III`).
    pools: dict[str, dict] = {}
    for g in titulares:
        p = pools.setdefault(g["pool"], {"pool": g["pool"], "aeronaves": 0, "series": 0, "nomes": set()})
        p["aeronaves"] += g["aeronaves_n"]
        p["series"] += 1
        p["nomes"].add(norm(g["nome"]))
    lista_pools = sorted(pools.values(), key=lambda x: -x["aeronaves"])

    fragmentadas = [g for g in base if len(g["nomes"]) > 1]
    return {
        "mes": mes,
        "frota": frota,
        "frota_com_dono": frota_com_dono,
        "participacoes": sum(g["links"] for g in base),
        "pessoas_bruto": len({pid for g in base for pid in g["pessoa_ids"]}),
        "grupos": len(base),
        "grupos_fundidos": len(fundido),
        "indices": idx_base,
        "indices_radicais": idx_fund,
        "metade_da_frota_em": _metade_em(base),
        "por_tipo": sorted(tipo_pct.items(), key=lambda kv: -kv[1]),
        "titularizacao": {
            "aeronaves": titularizacao,
            "pct_frota": round(titularizacao / frota_com_dono * 100, 2),
        },
        "titulares": {
            "grupos": len(titulares),
            "aeronaves": sum(g["aeronaves_n"] for g in titulares),
            "pct_frota": round(sum(g["aeronaves_n"] for g in titulares) / frota_com_dono * 100, 2),
            "pct_credito": round(
                sum(g["aeronaves_n"] for g in titulares)
                / max(sum(g["aeronaves_n"] for g in base
                          if g["tipo"] not in ("Governamental", "Fabricante")), 1) * 100, 2),
            "pools": len(lista_pools),
            "series_colapsadas": len(titulares) - len(lista_pools),
            "top": [{
                # O grupo é uma identidade agregada (pode reunir varias grafias),
                # então a rota usa a pessoa de maiorParticipacao: existe,
                # pertence ao grupo, e leva a uma pagina real.
                "pessoa_id": min(g["pessoa_ids"]) if g["pessoa_ids"] else None,
                "pessoa_ids": sorted(g["pessoa_ids"])[:12],
                "natureza": g["natureza"],
                "nome": g["nome"], "uf": g["uf"], "aeronaves": g["aeronaves_n"],
                "pct_frota": g["pct_frota"],
                "series": g["variantes"],
                "confirmado_nome": bool(RE_TITULAR.search(norm(g["nome"]))),
            } for g in titulares[:20]],
            "pools_top": [{
                "pool": p["pool"], "aeronaves": p["aeronaves"], "series": p["series"],
                "nomes": sorted(p["nomes"])[:4],
            } for p in lista_pools[:15]],
        },
        "fragmentacao": {
            "grupos_com_varias_grafias": len(fragmentadas),
            "aeronaves_afetadas": sum(g["aeronaves_n"] for g in fragmentadas),
            "exemplos": [{
                "grafias": sorted(g["nomes"])[:4],
                "aeronaves": g["aeronaves_n"],
                # Cada grafia e um `pessoa.id` diferente: e exatamente ai que
                # nasce a fragmentacao, e cada linha precisa abrir a sua.
                "por_grafia": sorted(
                    ({"grafia": nome, "pessoa_id": pid} for nome, pid in g["por_nome"].items()),
                    key=lambda d: d["grafia"])[:12],
            } for g in sorted(fragmentadas, key=lambda g: -g["aeronaves_n"])[:8]],
        },
        "top": [{
            "pessoa_id": min(g["pessoa_ids"]) if g["pessoa_ids"] else None,
            "pessoa_ids": sorted(g["pessoa_ids"])[:12],
            "nome": g["nome"], "tipo": g["tipo"], "uf": g["uf"],
            "aeronaves": g["aeronaves_n"], "pct_frota": g["pct_frota"],
            "grafias": len(g["nomes"]), "opera": g["opera"],
        } for g in base[:20]],
    }


def serie_mensal(conn) -> list[dict]:
    """Série comparável de concentração, mês a mês.

    Só o HHI por frota e pela chave de radicais entra aqui. O HHI por
    percentual declarado fica de fora justamente por não ter base constante:
    os doze meses não têm a mesma cobertura de percentual, e uma linha que
    misturas as duasbases seria a forma mais fácil de mentir com um gráfico.
    """
    saida = []
    for mes in _meses(conn):
        a = analise(conn, mes, completo=False)
        if not a:
            continue
        i, ir = a["indices"], a["indices_radicais"]
        tipo = dict(a["por_tipo"])
        saida.append({
            "mes": mes,
            "frota": a["frota_com_dono"],
            "grupos": a["grupos"],
            "grupos_radicais": a["grupos_fundidos"],
            "hhi_frota": i["hhi_frota"],
            "hhi_frota_radicais": ir["hhi_frota"],
            "hhi_credito": i["hhi_credito"],
            "hhi_credito_radicais": ir["hhi_credito"],
            "cobertura_pct": i["cobertura_pct"],
            "gini": i["gini"],
            "cr4": i["cr4"],
            "cr10": i["cr10"],
            "top1": i["top1"],
            "titularizacao": a["titularizacao"]["pct_frota"],
            "titulares": a["titulares"]["pct_frota"],
            "titulares_credito": a["titulares"]["pct_credito"],
            "pools": a["titulares"]["pools"],
            "financeiro": tipo.get("Financeiro / crédito", 0.0),
            "governamental": tipo.get("Governamental", 0.0),
            "fabricante": tipo.get("Fabricante", 0.0),
            "pessoa_fisica": tipo.get("Pessoa física", 0.0),
        })
    return saida
