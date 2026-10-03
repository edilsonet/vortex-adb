"""Normalização das 3 eras de esquema do RAB.

A ANAC trocou o formato de exportação duas vezes dentro da série de 13 meses.
As três eras descrevem a mesma relação com nomes de campo diferentes:

  Era A  2025-09..2026-01  PROPRIETARIO + OUTROSPROPRIETARIOS + CPFCNPJ + SGUF
                          NMOPERADOR + OUTROSOPERADORES + CPFCGC + UFOPERADOR
  Era B  2026-02..2026-04  PROPRIETARIOSARRAY ("NOME|DOC|PCT" repetido em pipe)
                          + NMOPERADOR + OUTROSOPERADORES + CPFCGC
  Era C  2026-05..2026-09  PROPRIETARIOSJSON + OPERADORESJSON (arrays JSON)

`parse_snapshot` reduz as três ao mesmo tuplo, para que o restante do ETL nunca
precisa saber qual formato era o do mês.
"""

from __future__ import annotations

import json
import re

# Campos da aeronave, na ordem do schema. Tuplas (chave_json, chave_csv).
CAMPOS_AERONAVE = [
    ("nr_cert_matricula", "NRCERTMATRICULA", "NR_CERT_MATRICULA"),
    ("nr_serie",          "NRSERIE",         "NR_SERIE"),
    ("cd_tipo",           "CDTIPO",          "CD_TIPO"),
    ("ds_modelo",         "DSMODELO",        "DS_MODELO"),
    ("nm_fabricante",     "NMFABRICANTE",    "NM_FABRICANTE"),
    ("cd_classe",         "CDCLS",           "CD_CLS"),
    ("nr_pmd",            "NRPMD",           "NR_PMD"),
    ("cd_tipo_icao",      "CDTIPOICAO",      "CD_TIPO_ICAO"),
    ("nr_tripulacao_min", "NRTRIPULACAOMIN", "NR_TRIPULACAO_MIN"),
    ("nr_passageiros_max","NRPASSAGEIROSMAX","NR_PASSAGEIROS_MAX"),
    ("nr_assentos",       "NRASSENTOS",      "NR_ASSENTOS"),
    ("nr_ano_fabricacao", "NRANOFABRICACAO", "NR_ANO_FABRICACAO"),
    ("dt_validade_cva",   "DTVALIDADECVA",   "DT_VALIDADE_CVA"),
    ("dt_validade_ca",    "DTVALIDADECA",    "DT_VALIDADE_CA"),
    ("dt_cancelamento",   "DTCANC",          "DT_CANC"),
    ("ds_motivo_cancelamento", "DSMOTIVOCANC", "DS_MOTIVO_CANC"),
    ("cd_interdicao",     "CDINTERDICAO",    "CD_INTERDICAO"),
    ("ds_gravame",        "DSGRAVAME",       "DS_GRAVAME"),
    ("dt_matricula",      "DT_MATRICULA",    "DT_MATRICULA"),
    ("tp_motor",          "TPMOTOR",         "TP_MOTOR"),
    ("qt_motor",          "QTMOTOR",         "QT_MOTOR"),
    ("tp_pouso",          "TPPOUSO",         "TP_POUSO"),
    ("tp_ca",             "TPCA",            "TP_CA"),
    ("cd_proposito_cave", "CDPROPOSITOCAVE", "CD_PROPOSITO_CAVE"),
    ("cf_operacional",    "CFOPERACIONAL",   "CF_OPERACIONAL"),
    ("ds_categoria_homologacao", "DSCATEGORIAHOMOLOGACAO", "DS_CATEGORIA_HOMOLOGACAO"),
    ("tp_operacao",       "TPOPERADOR",      "TP_OPERACAO"),
    ("dt_venda",          "DTVENDA",         "DT_VENDA"),
    ("ds_moeda",          "DSMOEDA",         "DS_MOEDA"),
    ("nr_preco_venda",    "NRPRECOSVENDA",   "NR_PRECO_VENDA"),
]

# Flags que só existem no objeto de operador da era C.
FLAGS_OPERADOR = [
    ("operacao_121",   "OPERACAO121"),
    ("operacao_135",   "OPERACAO135"),
    ("transp_reg_121", "TRANSPREGULAR121"),
    ("transp_reg_135", "TRANSPREGULAR135"),
    ("aut_pmac_121",   "AUTORIZACAOPMAC121"),
    ("aut_pmac_135",   "AUTORIZACAOPMAC135"),
    ("sae",            "SAE"),
    ("authistrut",     "AUTHISTRUT"),
]

_VAZIO = {None, "", "None", "null"}


def _txt(valor) -> str | None:
    if valor in _VAZIO:
        return None
    s = str(valor).strip()
    return s or None


def _pct(valor) -> float | None:
    s = _txt(valor)
    if s is None:
        return None
    s = s.replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def detecta_era(registro: dict) -> str:
    if "PROPRIETARIOSJSON" in registro or "OPERADORESJSON" in registro:
        return "C"
    if "PROPRIETARIOSARRAY" in registro:
        return "B"
    return "A"


def detectar_passo_era_b(valores: list[str]) -> int:
    """Quantos campos cada proprietário ocupa no `*ARRAY` da era B.

    Não há um formato único: 2026-02 usa `NOME|DOCUMENTO|PERCENTUAL` (3 campos)
    e 2026-03/2026-04 usam `NOME|DOCUMENTO|PERCENTUAL|UF` (4). Assumir 3
    desalinha 33 mil registros de 03/04 e atribui a UF ao próximo proprietário.

    O passo é modal no mês, não por registro: um valor com 12 partes é
    ambíguo entre 3x4 e 4x3, e a resposta certa depende do formato daquele mês.
    """
    contagem = {3: 0, 4: 0}
    for v in valores:
        n = len([p for p in v.split("|") if p.strip()])
        if n and n % 4 == 0:
            contagem[4] += n // 4
        if n and n % 3 == 0:
            contagem[3] += n // 3
    return 4 if contagem[4] > contagem[3] else 3


_RE_DOC_B = re.compile(
    r"^(\d{14}|\d{11}|\d{3}[.\*]{3}\d{3}[.\*]{3}\d{2})$", re.I)


def _classifica_restante(campos: list[str], passo: int) -> dict:
    """Recupera um grupo final com menos campos que `passo`.

    A ANAC preenche o ausente com texto sentinela (`Documento Indisponível`)
    **dentro de um grupo completo** — isso não desalinha nada. Mas quando o
    campo simplesmente não é serializado, o último grupo fica curto e o
    parseamento posicional o descarta inteiro: em 2026-02, 5.738 Aircraft
    perdiam o dono. Aqui o primeiro campo é sempre o nome e os demais são
    realocados pelo formato do valor, não pela posição.
    """
    item = {"NOME": campos[0], "DOCUMENTO": None, "PERCENTUAL": None, "UF": None}
    ordem = ["DOCUMENTO", "PERCENTUAL", "UF"][: max(passo - 1, 0)]
    for chave, valor in zip(ordem, campos[1:]):
        if chave == "DOCUMENTO" and not _RE_DOC_B.match(valor):
            # Nao parece documento: e o percentual que sobrou.
            chave = "PERCENTUAL"
        item[chave] = valor
    return item


def _parse_array_b(bruto: str | None, passo: int = 3) -> list[dict]:
    """Era B: grupos de `passo` campos concatenados por pipe.

    passo 3 -> NOME|DOCUMENTO|PERCENTUAL
    passo 4 -> NOME|DOCUMENTO|PERCENTUAL|UF

    Grupos completos sao lidos por posicao. O resto final, mais curto, e
    recuperado por formato do valor em vez de ser descartado.
    """
    s = _txt(bruto)
    if s is None:
        return []
    partes = [p.strip() for p in s.split("|") if p.strip()]
    itens, n = [], len(partes)
    i = 0
    while i + passo <= n:
        item = {
            "NOME": partes[i],
            "DOCUMENTO": partes[i + 1],
            "PERCENTUAL": partes[i + 2],
        }
        if passo >= 4:
            item["UF"] = partes[i + 3]
        itens.append(item)
        i += passo
    if i < n:
        itens.append(_classifica_restante(partes[i:], passo))
    return itens


def _parse_json_c(bruto: str | None) -> list[dict]:
    """Era C: array JSON já válido (o reparo acontece na leitura do arquivo)."""
    s = _txt(bruto)
    if s is None:
        return []
    try:
        dados = json.loads(s)
    except json.JSONDecodeError:
        return []
    return dados if isinstance(dados, list) else [dados]


def _parse_pipe(bruto: str | None) -> list[dict]:
    """Triplas `NOME|DOCUMENTO|UF` concatenadas em pipe (era B, operadores)."""
    s = _txt(bruto)
    if s is None:
        return []
    partes = [p for p in s.split("|") if p.strip()]
    itens, i = [], 0
    while i + 2 < len(partes):
        itens.append({"NOME": partes[i].strip(),
                      "DOCUMENTO": partes[i + 1].strip(),
                      "UF": partes[i + 2].strip()})
        i += 3
    return itens


def extrai_proprietarios(rec: dict, era: str, passo_b: int = 3) -> list[dict]:
    if era == "C":
        brutos = _parse_json_c(rec.get("PROPRIETARIOSJSON"))
    elif era == "B":
        brutos = _parse_array_b(rec.get("PROPRIETARIOSARRAY"), passo_b)
    else:
        brutos = []
        principal = _txt(rec.get("PROPRIETARIO"))
        if principal:
            brutos.append({
                "NOME": principal,
                "DOCUMENTO": _txt(rec.get("CPFCNPJ")),
                "PERCENTUAL": None,
                "UF": _txt(rec.get("SGUF")),
            })
        # Era A lista os demais proprietários só como nome, sem documento nem
        # percentual. Entram assim mesmo: a identidade via nome é o que a
        # fonte permite nesse formato.
        outros = _txt(rec.get("OUTROSPROPRIETARIOS"))
        if outros:
            for nome in re.split(r"\s*(?:/|\|)\s*", outros):
                if nome.strip():
                    brutos.append({"NOME": nome.strip(), "DOCUMENTO": None,
                                   "PERCENTUAL": None, "UF": None})

    return [
        {
            "nome": _txt(b.get("NOME")),
            "documento": _txt(b.get("DOCUMENTO")),
            "percentual": _pct(b.get("PERCENTUAL")),
            "uf": _txt(b.get("UF")),
        }
        for b in brutos
        if _txt(b.get("NOME"))
    ]


def extrai_operadores(rec: dict, era: str) -> list[dict]:
    if era == "C":
        brutos = _parse_json_c(rec.get("OPERADORESJSON"))
    elif "OPERADORESARRAY" in rec:
        # Sub-variante da era B (2026-03 e 2026-04): `NOME|DOCUMENTO|UF`,
        # sem percentual. A era B de 2026-02 usa NMOPERADOR/CPFCGC no lugar.
        brutos = _parse_pipe(rec.get("OPERADORESARRAY"))
    else:
        brutos = []
        principal = _txt(rec.get("NMOPERADOR"))
        if principal:
            item = {"NOME": principal, "DOCUMENTO": _txt(rec.get("CPFCGC")),
                    "UF": _txt(rec.get("UFOPERADOR"))}
            brutos.append(item)
        outros = _txt(rec.get("OUTROSOPERADORES"))
        if outros:
            for nome in re.split(r"\s*(?:/|\|)\s*", outros):
                if nome.strip():
                    brutos.append({"NOME": nome.strip(), "DOCUMENTO": None,
                                   "UF": None})

    saida = []
    for b in brutos:
        nome = _txt(b.get("NOME"))
        if not nome:
            continue
        item = {
            "nome": nome,
            "documento": _txt(b.get("DOCUMENTO")),
            "percentual": _pct(b.get("PERCENTUAL")),
            "uf": _txt(b.get("UF")),
        }
        for destino, origem in FLAGS_OPERADOR:
            item[destino] = _txt(b.get(origem))
        saida.append(item)
    return saida


def normaliza_aeronave(rec: dict, era: str) -> dict:
    """Traduz o registro de qualquer era para as colunas de `aeronave`."""
    saida = {}
    for destino, k_json, k_csv in CAMPOS_AERONAVE:
        if k_json in rec:
            saida[destino] = _txt(rec.get(k_json))
        else:
            saida[destino] = None
    saida["marca"] = _txt(rec.get("MARCA"))
    return saida


def chave_natural(aer: dict) -> str:
    return "|".join([
        aer.get("marca") or "",
        aer.get("nr_cert_matricula") or "",
        aer.get("nr_serie") or "",
    ])
